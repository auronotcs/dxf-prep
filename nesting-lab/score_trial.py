"""Isolated scoring experiment; no application adapter or production edits."""
import ctypes
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import sys
import time

from verify_nesting import ContactNester, pc, part, E, L, BOX, HOLE, SMALL, validate

ROOT = Path(__file__).resolve().parent


def fixture(name):
    if name in ('E12', 'E36', 'L12'):
        shape = E if name.startswith('E') else L
        return [part(f'p{i:03}', shape) for i in range(int(name[1:]))]
    rng = random.Random(7441)
    result = []
    for i in range(9 if name == 'mixed9' else 24):
        sx, sy = rng.uniform(.5, 1.3), rng.uniform(.5, 1.3)
        shape = E if i % 2 else L
        result.append(part(f'p{i:03}', [(x*sx, y*sy) for x,y in shape]))
    if name == 'mixed9':
        result.extend([part('ring', BOX, [HOLE]),
                       part('insert', SMALL),
                       part('rigid', SMALL, [[(x+5,y) for x,y in SMALL]]),
                       part('curve', [(2*math.cos(i*math.tau/24),
                                       2*math.sin(i*math.tau/24)) for i in range(24)])])
    return result


def quality(layout):
    sheets = sorted({p.sheet for p in layout})
    envelope = sum(max(x for p in layout if p.sheet==s for q in p.paths for x,y in q)
                   * max(y for p in layout if p.sheet==s for q in p.paths for x,y in q)
                   for s in sheets)
    return len(sheets), envelope


def peak_bytes():
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [
            (n, ctypes.c_size_t) for n in ('peak', 'working', 'pp', 'p', 'pnp', 'np', 'pf', 'ppf')]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    api = ctypes.WinDLL('psapi', use_last_error=True).GetProcessMemoryInfo
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    c = Counters(); c.cb = ctypes.sizeof(c)
    if not api(kernel.GetCurrentProcess(), ctypes.byref(c), c.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return c.peak


def sample(name, policy):
    parts = fixture(name)
    original = repr(parts)
    engine = ContactNester(pc, timeout=8, scoring=policy)
    cpu = time.process_time(); start = time.perf_counter()
    result = engine.nest(parts, 24, 24, spacing=.25, angles=(0,90,180))
    wall = time.perf_counter()-start; cpu = time.process_time()-cpu
    start = time.perf_counter()
    validate(result,24,24,.25)
    validation = time.perf_counter()-start
    assert original == repr(parts)
    assert sorted(p.key for p in result) == sorted(p.key for p in parts)
    assert all(p.angle in (0,90,180) for p in result)
    sheets, envelope = quality(result)
    # Even/odd area from normalized geometry, avoiding naive subtraction for rigid islands.
    engine.deadline = time.perf_counter()+8
    material = sum(abs(sum(pc.Area(q) for q in engine.oriented(p,0)['paths']))
                   / engine.scale**2 for p in parts)
    return dict(case=name,policy=policy,wall=wall,cpu=cpu,validation_wall=validation,
                sheets=sheets,envelope=envelope,material_area=material,
                sheet_utilization=material/(sheets*24*24),
                envelope_utilization=material/envelope,peak_process_bytes=peak_bytes(),
                layout_sha256=hashlib.sha256(repr(result).encode()).hexdigest(),
                clearance_passed=True, stats=engine.stats)


def run():
    data = dict(source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (ROOT/'contact_nesting.py', ROOT/'verify_nesting.py', Path(__file__))},
                notes='Fresh-process cold-cache trials; solver wall/CPU exclude validation and imports. Peak includes imports. Synthetic polygon clearance only; no production integration.',
                samples=[], summary=[])
    output = ROOT/'score-trial-2026-09-23.json'
    for name in ('L12','E12','E36','mixed9','mixed24'):
        for repeat in range(2):
            group=[]
            for policy in ('area','height','width'):
                try:
                    p=subprocess.run([sys.executable,'-B',__file__,'sample',name,policy],
                                     capture_output=True,text=True,timeout=25)
                    row=json.loads(p.stdout) if p.returncode==0 else dict(case=name,policy=policy,error=p.stderr[-1200:])
                except subprocess.TimeoutExpired:
                    row=dict(case=name,policy=policy,error='external 25-second timeout')
                row['repeat']=repeat; group.append(row); data['samples'].append(row)
                output.write_text(json.dumps(data,indent=2))
            baseline=group[0]
            if 'error' not in baseline:
                best=min((r for r in group if 'error' not in r),key=lambda r:(r['sheets'],r['envelope']))
                assert (best['sheets'],best['envelope']) <= (baseline['sheets'],baseline['envelope'])
                summary=dict(case=name,repeat=repeat,baseline_quality=[baseline['sheets'],baseline['envelope']],
                             best_policy=best['policy'],best_quality=[best['sheets'],best['envelope']],
                             baseline_wall=baseline['wall'],total_solver_wall=sum(r.get('wall',0) for r in group))
                data['summary'].append(summary); print(json.dumps(summary),flush=True)
            output.write_text(json.dumps(data,indent=2))
    for name in ('L12','E12','E36','mixed9','mixed24'):
        for policy in ('area','height','width'):
            rows=[r for r in data['samples'] if r['case']==name and r['policy']==policy and 'error' not in r]
            assert len({r['layout_sha256'] for r in rows}) <= 1, 'nondeterministic layout'


if __name__ == '__main__':
    if len(sys.argv)>1:
        print(json.dumps(sample(sys.argv[2],sys.argv[3])))
    else:
        run()

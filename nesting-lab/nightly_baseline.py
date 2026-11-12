"""Read-only production/prototype comparison; fresh process per sample."""
import contextlib
import ctypes
import hashlib
import io
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent

def sample(mode, count, shape):
    import verify_nesting as suite
    stream = io.StringIO()
    cpu = time.process_time()
    with contextlib.redirect_stdout(stream):
        suite.benchmark(mode, count, shape)
    result = json.loads(stream.getvalue())
    result['process_cpu_seconds_including_validation'] = time.process_time() - cpu
    # Windows process peak includes interpreter/imports; not algorithm-only allocation.
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in ('peak_working_set', 'working_set',
            'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    api = ctypes.WinDLL('psapi', use_last_error=True).GetProcessMemoryInfo
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not api(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    result['whole_process_peak_working_set_bytes'] = counters.peak_working_set
    print(json.dumps(result))

def run():
    output = ROOT / 'nightly-results-2026-09-22.json'
    paths = [ROOT/'contact_nesting.py', ROOT/'verify_nesting.py',
             Path('C:/Users/auron/Documents/CAM Workbench/cam_core/nesting.py')]
    data = {'source_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
            'notes': 'Fresh processes; solver wall excludes startup/validation; CPU includes validation. Peak working set includes interpreter/imports. Synthetic shapes, not production integration proof. Existing engine search policies differ.',
            'samples': [], 'summary': []}
    for shape, count in [('L',12), ('E',12), ('E',36)]:
        for mode in ['shape_aware', 'contact']:
            group = []
            for repeat in range(3):
                try:
                    process = subprocess.run([sys.executable, '-B', __file__, 'sample', mode, str(count), shape], capture_output=True, text=True, timeout=35)
                    row = json.loads(process.stdout) if process.returncode == 0 else {'error': process.stderr[-2000:]}
                except subprocess.TimeoutExpired:
                    row = {'error': '35-second process timeout'}
                row.update(mode=mode, count=count, shape=shape, repeat=repeat)
                data['samples'].append(row)
                group.append(row)
                output.write_text(json.dumps(data, indent=2))
            good = [r for r in group if 'seconds' in r]
            summary = {'mode':mode, 'count':count, 'shape':shape, 'successful_samples':len(good)}
            if good:
                summary.update(median_seconds=statistics.median(r['seconds'] for r in good),
                    median_cpu_seconds=statistics.median(r['process_cpu_seconds_including_validation'] for r in good),
                    max_process_peak_bytes=max(r['whole_process_peak_working_set_bytes'] for r in good),
                    sheets=sorted(set(r['sheets'] for r in good)), envelopes=sorted(set(r['envelope_area'] for r in good)))
            data['summary'].append(summary)
            output.write_text(json.dumps(data, indent=2))
            print(json.dumps(summary), flush=True)

if __name__ == '__main__':
    if len(sys.argv) > 1:
        sample(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    else:
        run()

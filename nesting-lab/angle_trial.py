"""Bounded experiment: orientation tie order only; no production changes."""
import itertools, json, time
from pathlib import Path
from verify_nesting import ContactNester, pc, part, E, L, validate

rows=[]
for shape,count,path in [('L',12,L),('E',12,E),('E',36,E)]:
    for angles in itertools.permutations((0,90,180)):
        start=time.perf_counter()
        engine=ContactNester(pc,timeout=5)
        result=engine.nest([part(f'p{i:03}',path) for i in range(count)],24,24,spacing=.25,angles=angles)
        elapsed=time.perf_counter()-start
        validate(result,24,24,.25)
        sheets=len({p.sheet for p in result})
        envelope=sum(max(x for p in result if p.sheet==s for q in p.paths for x,y in q)*max(y for p in result if p.sheet==s for q in p.paths for x,y in q) for s in range(sheets))
        rows.append(dict(shape=shape,count=count,angles=angles,seconds=elapsed,sheets=sheets,envelope=envelope))
    candidates=rows[-6:]
    print(json.dumps(dict(shape=shape,count=count,best=min(candidates,key=lambda r:(r['sheets'],r['envelope'])),total_solver_seconds=sum(r['seconds'] for r in candidates))),flush=True)
Path(__file__).with_name('angle-trial-2026-09-22.json').write_text(json.dumps(rows,indent=2))

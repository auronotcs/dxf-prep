from pathlib import Path
import subprocess,sys,json
root=Path(__file__).parent
rows=[]
for kind,count in [('L',12),('E',12),('E',36)]:
    for mode in ['rectangular','smart_pairs','shape_aware','contact']:
        try:
            run=subprocess.run([sys.executable,'-B',str(root/'verify_nesting.py'),'benchmark',mode,str(count),kind],capture_output=True,text=True,timeout=20)
            row=json.loads(run.stdout) if run.returncode==0 else dict(mode=mode,count=count,shape=kind,error=run.stderr[-1500:])
        except subprocess.TimeoutExpired:row=dict(mode=mode,count=count,shape=kind,timeout_seconds=20)
        rows.append(row);print(json.dumps(row),flush=True)
        (root/'benchmark-results.json').write_text(json.dumps(rows,indent=2))

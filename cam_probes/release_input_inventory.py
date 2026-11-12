"""Read-only runtime-input inventory; not a release/package manifest."""
from pathlib import Path
import ast, hashlib, json, subprocess, sys
from collections import Counter
root=Path(r'C:\Users\auron\Documents\CAM Workbench')
records=[]
imports=set()
paths=list(root.glob('*.py'))+[root/'default_tool_catalog.json',root/'Run CAM Workbench.bat']
for folder in ('cam_core','assets'):
    paths.extend(p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and '.git' not in p.parts)
for p in sorted(set(paths)):
    data=p.read_bytes()
    records.append({'path':p.relative_to(root).as_posix(),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    if p.suffix=='.py' and '_vendor' not in p.parts:
        try:
            tree=ast.parse(data)
            for n in ast.walk(tree):
                if isinstance(n,ast.Import): imports.update(a.name.split('.')[0] for a in n.names)
                elif isinstance(n,ast.ImportFrom) and not n.level and n.module: imports.add(n.module.split('.')[0])
        except (SyntaxError,UnicodeError): pass
cmd=['git','-c',f'safe.directory={root.as_posix()}','-C',str(root)]
status=subprocess.check_output(cmd+['status','--porcelain'],text=True).splitlines()
result={'purpose':'read-only candidate input inventory, not approved complete release manifest','source_head':subprocess.check_output(cmd+['rev-parse','HEAD'],text=True).strip(),'python':sys.version,'status_counts':dict(Counter(s[:2] for s in status)),'candidate_count':len(records),'candidate_bytes':sum(r['bytes'] for r in records),'external_import_candidates':sorted(imports-set(sys.stdlib_module_names)-{'cam_core','cam_ui_theme'}),'explicit_exclusions':['live settings and recovery','customer drawings and jobs','comparison fixtures pending review','tests pending synthetic fixture review','output and test artifacts','.git and __pycache__'],'files':records}
out=Path(__file__).with_name('WORKBENCH_RELEASE_INPUT_INVENTORY_2026-09-16.json')
out.write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='files'},indent=2))

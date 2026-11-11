"""Capture an allowlisted, content-verified backend without touching production."""
import hashlib
import json
from pathlib import Path
import shutil

source = Path(r'C:\Users\auron\Documents\CAM Workbench')
target = Path(__file__).parent / 'core_snapshot'
if target.exists():
    raise SystemExit('Snapshot already exists; never replace an in-use backend automatically.')
manifest = json.loads((source / 'release_manifest.json').read_text(encoding='utf-8'))
paths = [r['path'] for r in manifest['files'] if r['path'].startswith('cam_core/')]
paths += [p.name for p in source.iterdir() if p.is_file() and
          (p.name in ('cam_build.json', 'default_tool_catalog.json') or p.name.startswith(('LICENSE', 'NOTICE')))]
paths = sorted(set(paths))
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
before = {name: digest(source / name) for name in paths}
target.mkdir(parents=True)
for name in paths:
    destination = target / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / name, destination)
after = {name: digest(source / name) for name in paths}
copied = {name: digest(target / name) for name in paths}
if before != after or before != copied:
    raise SystemExit('Source changed during copy. Snapshot not approved; inspect before use.')
record = {'source': str(source), 'build': json.loads((source/'cam_build.json').read_text()),
          'files': before, 'snapshot_id': hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()[:16]}
(target.parent/'source_manifest.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
print(f"Verified {len(paths)} files; snapshot {record['snapshot_id']}")

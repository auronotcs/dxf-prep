"""Stage immutable upstream snapshots, then explicitly adopt a verified candidate.

Never edits production or replaces core files used by an existing process.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
SOURCE = Path(r'C:\Users\auron\Documents\CAM Workbench')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contained(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f'Path escapes snapshot root: {name}')
    return path


def inventory(source):
    release = json.loads((source / 'release_manifest.json').read_text(encoding='utf-8'))
    paths = {item['path'] for item in release['files'] if item['path'].startswith('cam_core/')}
    # Include new Python modules even before the production release manifest is rebuilt.
    paths.update(p.relative_to(source).as_posix() for p in (source / 'cam_core').rglob('*.py')
                 if '__pycache__' not in p.parts)
    paths.update(p.name for p in source.iterdir() if p.is_file() and
                 (p.name in ('cam_build.json', 'default_tool_catalog.json') or p.name.startswith(('LICENSE', 'NOTICE'))))
    missing = [name for name in paths if not contained(source, name).is_file()]
    if missing:
        raise ValueError(f'Release inventory references missing files; reconcile before capture: {missing}')
    return {name: digest(contained(source, name)) for name in sorted(paths)}


def ui_inventory(source):
    # Hash source only; never copy user jobs/settings/recoveries.
    paths = list(source.glob('*.py'))
    if (source / 'cam_ui').is_dir():
        paths += list((source / 'cam_ui').rglob('*.py'))
    return {p.relative_to(source).as_posix(): digest(p) for p in sorted(paths)
            if not p.name.startswith('test_') and '__pycache__' not in p.parts}


def verify(record):
    folder = contained(ROOT, record.get('snapshot_directory', 'core_snapshot'))
    actual = {name: digest(contained(folder, name)) for name in record['files']}
    if actual != record['files']:
        raise ValueError('Snapshot contents no longer match their manifest.')
    if record.get('derived_files'):
        from snapshot_derivation import replay
        for name in record['derived_files']:
            if contained(folder, name).read_bytes() != replay(ROOT, record, name):
                raise ValueError('Derived snapshot bytes differ from replay.')
    return folder


def stage(source):
    active = json.loads((ROOT / 'source_manifest.json').read_text(encoding='utf-8'))
    verify(active)
    before = inventory(source)
    ui_before = ui_inventory(source)
    identity = hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()[:16]
    folder = ROOT / 'snapshots' / identity
    core = folder / 'core'
    manifest = folder / 'manifest.json'
    if manifest.exists():
        record = json.loads(manifest.read_text(encoding='utf-8'))
        verify(record)
        if record['files'] != before:
            raise ValueError('Snapshot identity collision.')
        if record.get('ui_source_files') != ui_before:
            # GUI-only development can change while the backend stays identical.
            # Keep core + prior observations immutable; stage a new observation.
            ui_id = hashlib.sha256(json.dumps(ui_before, sort_keys=True).encode()).hexdigest()[:12]
            manifest = folder / f'manifest-{ui_id}.json'
            record = dict(record, ui_source_files=ui_before,
                          captured_at_utc=datetime.now(timezone.utc).isoformat(),
                          previous_snapshot=active['snapshot_id'])
            if before != inventory(source) or ui_before != ui_inventory(source):
                raise ValueError('Production changed during capture; candidate not approved.')
            if not manifest.exists():
                manifest.write_text(json.dumps(record, indent=2), encoding='utf-8')
    else:
        if folder.exists():
            raise ValueError(f'Incomplete earlier capture at {folder}; inspect it before retrying.')
        core.mkdir(parents=True)
        for name in before:
            target = contained(core, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(contained(source, name), target)
        if before != inventory(source) or ui_before != ui_inventory(source):
            raise ValueError('Production changed during capture; candidate not approved.')
        record = {'source': str(source), 'build': json.loads((core / 'cam_build.json').read_text()),
                  'files': before, 'snapshot_id': identity,
                  'snapshot_directory': core.relative_to(ROOT).as_posix(),
                  'captured_at_utc': datetime.now(timezone.utc).isoformat(),
                  'ui_source_files': ui_before, 'previous_snapshot': active['snapshot_id']}
        verify(record)
        manifest.write_text(json.dumps(record, indent=2), encoding='utf-8')
    old = active['files']
    return {'candidate_manifest': str(manifest), 'snapshot_id': identity, 'file_count': len(before),
            'build': record['build']['build_id'],
            'changed': [name for name in before if name in old and before[name] != old[name]],
            'added': [name for name in before if name not in old],
            'removed': [name for name in old if name not in before],
            'ui_baseline_established': 'ui_source_files' not in active,
            'ui_changed': [name for name in ui_before if name in active.get('ui_source_files', {})
                           and ui_before[name] != active['ui_source_files'][name]]}


def adopt(manifest):
    manifest = contained(ROOT, manifest)
    record = json.loads(manifest.read_text(encoding='utf-8'))
    verify(record)
    source = Path(record['source'])
    if inventory(source) != record.get('source_files', record['files']) or ui_inventory(source) != record['ui_source_files']:
        raise ValueError('Production changed after staging; review a new candidate before adoption.')
    if record.get('source_files'):
        prior_record = json.loads((ROOT / 'source_manifest.json').read_text(encoding='utf-8'))
        verify(prior_record)
        # Explicitly deferred additions are absent from the runnable snapshot,
        # rather than shipping modules whose dependencies are still retained.
        deferred = record.get('deferred_added_files', {})
        missing = set(record['source_files']) - set(record['files'])
        if (set(record['files']) - set(record['source_files']) or missing != set(deferred)
                or missing & set(prior_record['files'])
                or any(deferred[n] != record['source_files'][n] for n in missing)):
            raise ValueError('Partial adoption must preserve the complete source inventory or explicitly defer only new source files with matching hashes.')
        changed = {n for n in record['files'] if record['files'][n] != record['source_files'].get(n)}
        derived = record.get('derived_files', {})
        if set(derived) & set(record.get('retained_files', {})) or any(
            spec['base_snapshot'] != prior_record['snapshot_id'] for spec in derived.values()):
            raise ValueError('Derived files must start from the current active snapshot.')
        changed -= set(derived)
        if changed != set(record.get('retained_files', {})) or any(
            record['files'][n] != prior_record['files'].get(n) or record['retained_files'][n] != prior_record['snapshot_id'] for n in changed):
            raise ValueError('Partial adoption must retain exactly the declared active-snapshot files.')
    active = ROOT / 'source_manifest.json'
    prior = json.loads(active.read_text(encoding='utf-8'))
    archive = ROOT / 'snapshots' / f"{prior['snapshot_id']}-manifest.json"
    if not archive.exists():
        archive.write_text(json.dumps(prior, indent=2), encoding='utf-8')
    tmp = active.with_suffix('.tmp')
    tmp.write_text(json.dumps(record, indent=2), encoding='utf-8')
    tmp.replace(active)  # Only new processes read this pointer; old core folders stay intact.
    return {'adopted': record['snapshot_id'], 'previous': prior['snapshot_id'],
            'checked_at_utc': datetime.now(timezone.utc).isoformat()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['stage', 'adopt', 'verify'])
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--manifest', type=Path)
    args = parser.parse_args()
    if args.action == 'stage':
        result = stage(args.source)
    elif args.action == 'adopt':
        if args.manifest is None: parser.error('adopt requires --manifest after candidate tests pass')
        result = adopt(args.manifest)
    else:
        record = json.loads((args.manifest or ROOT / 'source_manifest.json').read_text(encoding='utf-8'))
        result = {'verified': str(verify(record)), 'snapshot_id': record['snapshot_id']}
    print(json.dumps(result, indent=2))

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sync_source as sync


class SnapshotSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'backend'
        self.source = Path(self.temp.name) / 'source'
        (self.root / 'core_snapshot/cam_core').mkdir(parents=True)
        (self.source / 'cam_core').mkdir(parents=True)
        self.old = self.root / 'core_snapshot/cam_core/example.py'
        self.old.write_text('old backend')
        (self.source / 'cam_core/example.py').write_text('new backend')
        (self.source / 'cam_build.json').write_text(json.dumps({'build_id': 'test-build'}))
        (self.source / 'cam_editor_app.py').write_text('ui version 1')
        (self.source / 'release_manifest.json').write_text(json.dumps({'files': [{'path': 'cam_core/example.py'}]}))
        self.prior = {'source': str(self.source), 'snapshot_id': 'old',
                      'files': {'cam_core/example.py': sync.digest(self.old)}}
        (self.root / 'source_manifest.json').write_text(json.dumps(self.prior))
        self.patch = patch.object(sync, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_stage_then_adopt_preserves_running_snapshot_and_rollback_manifest(self):
        result = sync.stage(self.source)
        self.assertEqual(json.loads((self.root / 'source_manifest.json').read_text()), self.prior)
        sync.adopt(Path(result['candidate_manifest']))
        record = json.loads((self.root / 'source_manifest.json').read_text())
        self.assertEqual(record['snapshot_id'], result['snapshot_id'])
        self.assertEqual(self.old.read_text(), 'old backend')
        self.assertEqual(json.loads((self.root / 'snapshots/old-manifest.json').read_text()), self.prior)

    def test_source_drift_and_candidate_tampering_prevent_adoption(self):
        result = sync.stage(self.source)
        manifest = Path(result['candidate_manifest'])
        source_file = self.source / 'cam_core/example.py'
        source_file.write_text('changed during testing')
        with self.assertRaisesRegex(ValueError, 'Production changed'):
            sync.adopt(manifest)
        source_file.write_text('new backend')
        record = json.loads(manifest.read_text())
        (self.root / record['snapshot_directory'] / 'cam_core/example.py').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'no longer match'):
            sync.adopt(manifest)
        self.assertEqual(json.loads((self.root / 'source_manifest.json').read_text()), self.prior)

    def test_new_uncommitted_module_and_gui_only_followup_are_detected(self):
        (self.source / 'cam_core/new_module.py').write_text('new module')
        first = sync.stage(self.source)
        self.assertIn('cam_core/new_module.py', first['added'])
        sync.adopt(Path(first['candidate_manifest']))
        original_manifest = Path(first['candidate_manifest']).read_bytes()
        (self.source / 'cam_editor_app.py').write_text('ui version 2')
        second = sync.stage(self.source)
        self.assertEqual(first['snapshot_id'], second['snapshot_id'])
        self.assertIn('cam_editor_app.py', second['ui_changed'])
        self.assertNotEqual(first['candidate_manifest'], second['candidate_manifest'])
        sync.adopt(Path(second['candidate_manifest']))
        self.assertEqual(Path(first['candidate_manifest']).read_bytes(), original_manifest)

    def test_partial_batch_retains_declared_verified_prior_file_and_tracks_drift(self):
        import shutil
        staged=sync.stage(self.source);base=json.loads(Path(staged['candidate_manifest']).read_text())
        record=dict(base,source_files=base['files'].copy(),files=base['files'].copy(),snapshot_id='partial',snapshot_directory='snapshots/partial/core',retained_files={'cam_core/example.py':'old'})
        folder=self.root/record['snapshot_directory'];shutil.copytree(self.root/base['snapshot_directory'],folder)
        shutil.copy2(self.old,folder/'cam_core/example.py');record['files']['cam_core/example.py']=self.prior['files']['cam_core/example.py']
        manifest=folder.parent/'manifest.json';manifest.write_text(json.dumps(record))
        bad=dict(record,retained_files={'cam_core/example.py':'wrong'})
        manifest.write_text(json.dumps(bad))
        with self.assertRaisesRegex(ValueError,'exactly the declared'):sync.adopt(manifest)
        bad=dict(record,files={k:v for k,v in record['files'].items() if k!='cam_build.json'})
        manifest.write_text(json.dumps(bad))
        with self.assertRaisesRegex(ValueError,'complete source inventory'):sync.adopt(manifest)
        manifest.write_text(json.dumps(record));sync.adopt(manifest)
        self.assertEqual(sync.verify(record),folder)
        self.assertEqual(sync.stage(self.source)['changed'],['cam_core/example.py'])

    def test_deferred_new_modules_are_absent_hashed_and_remain_pending(self):
        import shutil
        (self.source/'cam_core/new_module.py').write_text('requires unadopted interface')
        staged=sync.stage(self.source);base=json.loads(Path(staged['candidate_manifest']).read_text())
        record=dict(base,source_files=base['files'].copy(),files=base['files'].copy(),
            snapshot_id='bounded',snapshot_directory='snapshots/bounded/core',retained_files={})
        deferred='cam_core/new_module.py'
        record['deferred_added_files']={deferred:record['files'].pop(deferred)}
        folder=self.root/record['snapshot_directory'];folder.mkdir(parents=True)
        for name in record['files']:
            target=folder/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(self.root/base['snapshot_directory']/name,target)
        manifest=folder.parent/'manifest.json'
        for bad in ({}, {deferred:'wrong'}, {deferred:record['source_files'][deferred],'cam_core/example.py':record['source_files']['cam_core/example.py']}):
            manifest.write_text(json.dumps(dict(record,deferred_added_files=bad)))
            with self.assertRaisesRegex(ValueError,'complete source inventory'): sync.adopt(manifest)
            self.assertEqual(json.loads((self.root/'source_manifest.json').read_text()),self.prior)
        # Omitting a previously adopted file is forbidden even with its source hash.
        old='cam_core/example.py';bad=dict(record,files={n:h for n,h in record['files'].items() if n!=old},
            deferred_added_files=record['deferred_added_files']|{old:record['source_files'][old]})
        manifest.write_text(json.dumps(bad))
        with self.assertRaisesRegex(ValueError,'complete source inventory'): sync.adopt(manifest)
        manifest.write_text(json.dumps(record));sync.adopt(manifest)
        self.assertFalse((folder/deferred).exists())
        followup=sync.stage(self.source)
        self.assertIn(deferred,followup['added'])
        self.assertEqual(self.old.read_text(),'old backend')


if __name__ == '__main__':
    unittest.main()

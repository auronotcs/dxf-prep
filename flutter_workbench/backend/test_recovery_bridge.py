import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from design_bridge import Bridge
from recovery_support import RecoveryStore, file_token


class RecoveryBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stores = []
        self.bridge = self.make()

    def make(self):
        b = Bridge(self.root/'layout.json', recovery_directory=self.root/'recovery')
        self.stores.append(b)
        self.addCleanup(lambda: b.recovery.lock.close() if b.recovery else None)
        return b

    def command(self, b, command, **params):
        r = b.handle(dict(command=command, params=params, revision=b.revision))
        self.assertTrue(r['ok'], (command, r.get('error')))
        return r['state']

    def rectangle(self, b=None):
        return self.command(b or self.bridge, 'geometry.add_rectangle', x=2, y=3, width=4, height=5)

    def interrupted(self, b=None):
        b = b or self.bridge
        path = b.recovery.path(b.recovery.identity)
        b.recovery.close()
        return path

    def entry(self, b):
        return self.command(b, 'recovery.list')['recovery']['candidates'][0]

    def test_restore_units_annotations_guides_original_and_fresh_history(self):
        for units in ['in', 'mm']:
            b = self.bridge
            self.command(b, 'project.new', units=units)
            self.rectangle(b)
            source = self.root/f'original-{units}.camjob.json'
            self.command(b, 'project.save', path=str(source))
            original = source.read_bytes()
            self.command(b, 'geometry.transform', dx=7, dy=8)
            reference = lambda x, y: dict(entity_id='', feature='free', fallback=dict(x=x, y=y))
            preview = b.handle(dict(command='design.dimension_preview', revision=b.revision, params=dict(
                annotation=dict(kind='note', start=reference(1, 1), end=reference(2, 2), offset=1,
                precision=3, text_height=.2, extension_offset=.1, layer='0', metadata=dict(text='recover note')))))
            self.assertTrue(preview['ok'], preview)
            self.command(b, 'design.dimension_apply', token=preview['preview']['token'])
            self.command(b, 'design.guide_add', orientation='vertical', position=8, name='Fence')
            expected = copy.deepcopy(b.session.project.to_dict())
            path = self.interrupted(b)
            other = self.make()
            e = self.entry(other)
            state = self.command(other, 'recovery.restore', id=e['id'], token=e['token'])
            self.assertEqual(other.session.project.to_dict(), expected)
            self.assertTrue(state['dirty'])
            self.assertIsNone(state['savedPath'])
            self.assertEqual(state['path'], str(source.resolve()))
            self.assertFalse(state['canUndo'])
            self.assertEqual(state['history']['entries'], ['Recovered drawing'])
            self.assertFalse(path.exists())
            self.assertEqual(source.read_bytes(), original)
            denied = other.handle(dict(command='project.save', params={'path':str(source)}, revision=other.revision))
            self.assertFalse(denied['ok'])
            saved = self.root/f'restored-{units}.camjob.json'
            self.command(other, 'project.save', path=str(saved))
            self.assertFalse(other.recovery.path(other.recovery.identity).exists())
            self.assertEqual(source.read_bytes(), original)
            self.command(other, 'project.open', path=str(saved))
            self.assertEqual(other.session.project.to_dict(), expected)
            self.bridge = other

    def test_active_sessions_are_excluded_and_independent(self):
        self.rectangle()
        a_path = self.bridge.recovery.path(self.bridge.recovery.identity)
        original = a_path.read_bytes()
        other = self.make()
        self.assertEqual(other.recovery.candidates, [])
        self.rectangle(other)
        self.assertEqual(a_path.read_bytes(), original)
        denied = other.handle(dict(command='recovery.discard', revision=other.revision,
            params=dict(id=self.bridge.recovery.identity, token=file_token(a_path))))
        self.assertFalse(denied['ok'])
        self.interrupted()
        self.assertEqual(len(self.command(other, 'recovery.list')['recovery']['candidates']), 1)

    def test_atomic_failure_preserves_previous_copy_and_successful_edit_and_retry(self):
        self.rectangle()
        path = self.bridge.recovery.path(self.bridge.recovery.identity)
        previous = path.read_bytes()
        with patch('cam_core.project_storage.os.replace', side_effect=OSError('disk unavailable')):
            state = self.command(self.bridge, 'geometry.transform', dx=1, dy=0)
        self.assertTrue(state['dirty'])
        self.assertIn('disk unavailable', state['recovery']['warning'])
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(self.bridge.session.project.entities[0].corner_a.x, 3)
        self.assertFalse(list(path.parent.glob('*.tmp')))
        state = self.command(self.bridge, 'recovery.checkpoint')
        self.assertIsNone(state['recovery']['warning'])
        self.assertNotEqual(path.read_bytes(), previous)

    def test_failed_save_open_and_invalid_new_retain_copy(self):
        self.rectangle()
        path = self.bridge.recovery.path(self.bridge.recovery.identity)
        previous = path.read_bytes()
        for command, params in [
            ('project.open', {'path':str(self.root/'missing.camjob.json')}),
            ('project.save', {'path':str(self.root/'not-json.dxf')}),
            ('project.new', {'units':'invalid'}),
        ]:
            before = self.bridge.snapshot()
            r = self.bridge.handle(dict(command=command, params=params, revision=self.bridge.revision))
            self.assertFalse(r['ok'], command)
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(self.bridge.snapshot(), before)

    def test_noop_selection_does_not_rewrite_and_successful_switch_clears_only_owned_copy(self):
        state = self.rectangle()
        path = self.bridge.recovery.path(self.bridge.recovery.identity)
        before = path.stat().st_mtime_ns
        self.command(self.bridge, 'selection.clear')
        self.assertEqual(path.stat().st_mtime_ns, before)
        orphan = self.make()
        self.rectangle(orphan)
        other_path = self.interrupted(orphan)
        self.command(self.bridge, 'project.new')
        self.assertFalse(path.exists())
        self.assertTrue(other_path.exists())
        self.rectangle()
        self.command(self.bridge, 'recovery.clear')
        self.assertFalse(path.exists())

    def test_restore_requires_empty_unmodified_document_and_matching_revision(self):
        self.rectangle()
        self.interrupted()
        other = self.make()
        e = self.entry(other)
        self.rectangle(other)
        before = other.snapshot()
        r = other.handle(dict(command='recovery.restore', params=e, revision=other.revision))
        self.assertFalse(r['ok'])
        self.assertEqual(other.snapshot(), before)
        self.command(other, 'project.new')
        r = other.handle(dict(command='recovery.restore', params=e, revision=other.revision-1))
        self.assertFalse(r['ok'])
        self.assertTrue(other.recovery.path(e['id']).exists())

    def test_corrupt_unsupported_and_changed_candidates_are_preserved_until_explicit_discard(self):
        self.rectangle()
        path = self.interrupted()
        path.write_text('{broken', encoding='utf-8')
        other = self.make()
        e = self.entry(other)
        self.assertIsNotNone(e['error'])
        r = other.handle(dict(command='recovery.restore', params=e, revision=other.revision))
        self.assertFalse(r['ok'])
        self.assertTrue(path.exists())
        path.write_text('changed', encoding='utf-8')
        r = other.handle(dict(command='recovery.discard', params=e, revision=other.revision))
        self.assertFalse(r['ok'])
        e = self.entry(other)
        self.command(other, 'recovery.discard', id=e['id'], token=e['token'])
        self.assertFalse(path.exists())

    def test_recovery_cannot_follow_paths_or_delete_current_copy(self):
        self.rectangle()
        path = self.bridge.recovery.path(self.bridge.recovery.identity)
        original = path.read_bytes()
        for identity in ['../source', str(path), self.bridge.recovery.identity]:
            r = self.bridge.handle(dict(command='recovery.discard', revision=self.bridge.revision,
                                        params=dict(id=identity, token=file_token(path))))
            self.assertFalse(r['ok'])
        self.assertEqual(path.read_bytes(), original)

    def test_failed_recovery_handoff_keeps_old_copy_and_empty_document(self):
        self.rectangle()
        old = self.interrupted()
        other = self.make()
        e = self.entry(other)
        before = other.snapshot()
        with patch('recovery_support.write_project_payload', side_effect=OSError('no space')):
            r = other.handle(dict(command='recovery.restore', params=e, revision=other.revision))
        self.assertFalse(r['ok'])
        self.assertEqual(other.snapshot(), before)
        self.assertTrue(old.exists())

    def test_discard_removal_failure_is_not_reported_as_success(self):
        self.rectangle()
        path = self.interrupted()
        other = self.make()
        e = self.entry(other)
        with patch.object(Path, 'unlink', side_effect=PermissionError('busy disk')):
            r = other.handle(dict(command='recovery.discard', params=e, revision=other.revision))
        self.assertFalse(r['ok'])
        self.assertTrue(path.exists())

    def test_disabled_bridge_does_not_touch_recovery(self):
        other = Bridge(self.root/'non-recovery-layout.json')
        self.rectangle(other)
        self.assertFalse(other.snapshot()['recovery']['enabled'])
        self.assertIsNone(other.recovery)

    def test_oversized_copy_warns_and_retains_previous_recoverable_copy(self):
        self.rectangle()
        path = self.bridge.recovery.path(self.bridge.recovery.identity)
        previous = path.read_bytes()
        with patch('recovery_support.MAX_BYTES', 32):
            state = self.command(self.bridge, 'geometry.transform', dx=1, dy=0)
        self.assertIn('64 MiB', state['recovery']['warning'])
        self.assertEqual(path.read_bytes(), previous)

    def test_invalid_plate_state_never_consumes_recovery_copy(self):
        self.rectangle()
        path = self.interrupted()
        data = json.loads(path.read_text(encoding='utf-8'))
        data['project']['metadata']['plates'] = [{'width': 'broken'}]
        path.write_text(json.dumps(data), encoding='utf-8')
        original = path.read_bytes()
        other = self.make()
        entry = self.entry(other)
        before = other.snapshot()
        result = other.handle(dict(command='recovery.restore', params=entry, revision=other.revision))
        self.assertFalse(result['ok'])
        self.assertEqual(other.snapshot(), before)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(other.recovery.path(other.recovery.identity).exists())


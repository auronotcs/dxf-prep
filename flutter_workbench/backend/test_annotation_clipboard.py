import copy
import unittest
from test_clipboard_bridge import ClipboardBridgeTests
from design_bridge import Bridge
from cam_core.dimensions import DimensionAnnotation, DimensionReference
from cam_core.guides import Guide
from cam_core.model import Point


class AnnotationClipboardTests(unittest.TestCase):
    setUp = ClipboardBridgeTests.setUp
    run_cmd = ClipboardBridgeTests.run_cmd
    fixture = ClipboardBridgeTests.fixture
    reject = ClipboardBridgeTests.reject

    def annotations(self):
        self.fixture()
        p = self.b.session.project
        p.dimensions = [DimensionAnnotation(id='dim', kind='horizontal',
            start=DimensionReference(Point(-2,3), 'a', 'rectangle:corner:0'),
            end=DimensionReference(Point(8,3), 'a', 'rectangle:corner:1'), offset=2)]
        p.guides = [Guide(id='guide', orientation='vertical', position=5, name='Datum')]
        self.b.session._reset_history()
        self.b.history_labels = ['Fixture']
        self.b.saved_digest = self.b.digest()

    def export(self):
        r = self.b.handle(dict(command='design.clipboard_export', revision=self.b.revision))
        self.assertTrue(r['ok'], r.get('error'))
        return r['clipboard']

    def test_cross_document_remaps_owners_and_preserves_source_and_separate_undo(self):
        self.annotations()
        before = self.b.session.project.to_dict()
        self.run_cmd('design.copy', dimension_ids=['dim'], guide_ids=['guide'])
        data = self.export()
        other = Bridge(self.folder/'other-prefs.json')
        r = other.handle(dict(command='design.paste', params={'clipboard':data}, revision=other.revision))
        self.assertTrue(r['ok'], r.get('error'))
        p = other.session.project
        self.assertEqual(len(p.entities), 2)
        self.assertEqual(p.dimensions[0].start.entity_id, p.entities[0].id)
        self.assertNotEqual(p.guides[0].id, 'guide')
        self.assertEqual(p.guides[0].position, 5)
        self.assertEqual(self.b.session.project.to_dict(), before)
        self.assertEqual(self.b.session._history_index, 0)
        self.assertEqual(other.session._history_index, 1)
        other.handle(dict(command='history.undo', revision=other.revision))
        self.assertFalse(other.session.project.entities)
        self.assertFalse(other.session.project.dimensions)
        self.assertFalse(other.session.project.guides)
        self.assertEqual(self.b.session.project.to_dict(), before)

    def test_annotation_only_paste_freezes_external_refs_and_translates_guides(self):
        self.annotations()
        self.run_cmd('design.copy', include_geometry=False, dimension_ids=['dim'], guide_ids=['guide'])
        self.assertEqual(self.b.snapshot()['clipboardCount'], 2)
        captured = copy.deepcopy(self.b.clipboard_dimensions[0])
        self.run_cmd('design.paste', dx=2, dy=3)
        p = self.b.session.project
        d = p.dimensions[-1]
        self.assertEqual(d.start.entity_id, '')
        self.assertEqual(d.start.feature, 'free')
        self.assertEqual(d.start.fallback, Point(captured.start.fallback.x+2, captured.start.fallback.y+3))
        self.assertEqual(p.guides[-1].position, 7)
        self.assertEqual(len(p.entities), 2)
        self.assertNotEqual(d.id, 'dim')
        path = self.folder/'annotations.camjob.json'
        expected = p.to_dict()
        self.run_cmd('project.save', path=str(path))
        self.run_cmd('project.open', path=str(path))
        self.assertEqual(self.b.session.project.to_dict(), expected)

    def test_cut_is_one_transaction_and_locked_guides_are_copy_only(self):
        self.annotations()
        self.b.session.project.guides[0].locked = True
        self.reject('design.cut', include_geometry=False, guide_ids=['guide'])
        self.run_cmd('design.copy', include_geometry=False, guide_ids=['guide'])
        self.b.session.project.guides[0].locked = False
        before = self.b.session.project.to_dict()
        self.b.session._reset_history()
        self.run_cmd('design.cut', include_geometry=False, dimension_ids=['dim'], guide_ids=['guide'])
        self.assertFalse(self.b.session.project.dimensions)
        self.assertFalse(self.b.session.project.guides)
        self.assertEqual(len(self.b.session.project.entities), 2)
        self.run_cmd('history.undo')
        self.assertEqual(self.b.session.project.to_dict(), before)
        self.assertEqual(self.b.snapshot()['clipboardCount'], 2)

    def test_locked_layers_stale_ids_units_and_invalid_payload_are_atomic(self):
        self.annotations()
        self.reject('design.copy', include_geometry=False, dimension_ids=['gone'])
        self.run_cmd('design.copy', dimension_ids=['dim'])
        data = self.export()
        self.run_cmd('layer.set', name='DIMENSIONS', locked=True)
        self.reject('design.paste')
        self.reject('design.copy', include_geometry=False, dimension_ids=['dim'])
        self.run_cmd('project.new', units='mm')
        self.reject('design.paste', clipboard=data)
        self.run_cmd('project.new', units='in')
        for change in ({'hasLED': True}, {'version': 99}, {'units':'bad'}, {'entities':data['entities']*2}):
            self.reject('design.paste', clipboard={**data, **change})

    def test_dimension_destination_layer_and_repeat_identity(self):
        self.annotations()
        self.run_cmd('design.copy', include_geometry=False, dimension_ids=['dim'])
        self.run_cmd('layer.set', name='Notes')
        self.run_cmd('design.paste', target_layer='Notes')
        self.run_cmd('design.paste', target_layer='Notes')
        dims = self.b.session.project.dimensions
        self.assertEqual([d.layer for d in dims[1:]], ['Notes','Notes'])
        self.assertEqual(len({d.id for d in dims}),3)

"""Clipboard transactions, ownership, protected destinations and persistence."""
import copy
import tempfile
import unittest
from pathlib import Path
from design_bridge import Bridge
from cam_core.model import Point, Rectangle, RouterOffsetOperation
from cam_core.led_layout import LEDLayout, LEDPlacement


class ClipboardBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.b = Bridge(self.folder/'preferences.json')

    def run_cmd(self, command, **params):
        r = self.b.handle(dict(command=command, params=params, revision=self.b.revision))
        self.assertTrue(r['ok'], r.get('error'))
        return r['state']

    def fixture(self, **metadata):
        a = Rectangle(id='a', layer='source', corner_a=Point(-2,3), corner_b=Point(8,9),
            metadata=dict(group_id='shared', compound_id='shared', mounting_part_id='part',
                mounting_feature_key='part:slot', pattern_source_entity_ids=['a','b']))
        b = Rectangle(id='b', layer='source', corner_a=Point(9,3), corner_b=Point(12,9),
            metadata=dict(group_id='shared', mounting_part_id='part', mounting_owner_entity_id='a',
                owner_entity_id='a', bent_tab_inner_cutout_ids=['a']))
        self.b.session.project.entities = [a,b]
        self.b.session.project.metadata.update(metadata)
        self.b.session.selected_ids = {'a'}
        self.b.session._reset_history()
        self.b.history_labels = ['Fixture']
        self.b.saved_digest = self.b.digest()

    def reject(self, command, **params):
        before = self.b.session.project.to_dict()
        history = copy.deepcopy(self.b.session._history)
        selected = set(self.b.session.selected_ids)
        clipboard = copy.deepcopy(self.b.clipboard)
        r = self.b.handle(dict(command=command, params=params, revision=self.b.revision))
        self.assertFalse(r['ok'])
        self.assertEqual(self.b.session.project.to_dict(), before)
        self.assertEqual(self.b.session._history, history)
        self.assertEqual(self.b.session.selected_ids, selected)
        self.assertEqual(self.b.clipboard, clipboard)
        return r

    def test_in_place_group_id_remapping_repeat_and_roundtrip(self):
        self.fixture()
        before = self.b.session.project.to_dict()
        self.assertEqual(self.run_cmd('design.copy')['clipboardCount'], 2)
        self.assertEqual(self.b.session.project.to_dict(), before)
        state = self.run_cmd('design.paste')
        a,b,x,y = self.b.session.project.entities
        self.assertEqual(x.corner_a, a.corner_a)
        self.assertEqual(y.corner_b, b.corner_b)
        self.assertEqual([a.to_dict(),b.to_dict()],before['entities'])
        self.assertEqual(set(state['selected']),{x.id,y.id})
        self.assertEqual(x.metadata['group_id'], y.metadata['group_id'])
        self.assertNotEqual(x.metadata['group_id'], x.metadata['compound_id'])
        self.assertNotEqual(x.metadata['mounting_part_id'], 'part')
        self.assertEqual(y.metadata['mounting_owner_entity_id'], x.id)
        self.assertEqual(y.metadata['owner_entity_id'], x.id)
        self.assertEqual(x.metadata['pattern_source_entity_ids'], [x.id,y.id])
        self.assertEqual(y.metadata['bent_tab_inner_cutout_ids'], [x.id])
        self.assertEqual(x.metadata['mounting_feature_key'],x.metadata['mounting_part_id']+':slot')
        after = self.b.session.project.to_dict()
        self.assertEqual(self.b.session._history_index,1)
        self.run_cmd('project.save',path=str(self.folder/'roundtrip.camjob.json'))
        self.run_cmd('history.undo')
        self.assertEqual(self.b.session.project.to_dict(),before)
        self.run_cmd('history.redo')
        self.assertEqual(self.b.session.project.to_dict(),after)
        self.run_cmd('project.open',path=str(self.folder/'roundtrip.camjob.json'))
        self.assertEqual(self.b.session.project.to_dict(),after)
        self.run_cmd('design.paste')
        all_ids = [e.id for e in self.b.session.project.entities]
        self.assertEqual(len(all_ids),len(set(all_ids)))
        self.assertNotEqual(self.b.session.project.entities[-1].metadata['group_id'], y.metadata['group_id'])

    def test_destination_guards_and_current_layer(self):
        self.fixture()
        self.run_cmd('design.copy')
        self.run_cmd('layer.set',name='destination',visible=True,locked=True)
        for target in ['destination','missing',False]:
            self.reject('design.paste',target_layer=target)
        self.run_cmd('layer.set',name='source',locked=True)
        self.reject('design.paste')
        self.run_cmd('layer.set',name='destination',visible=False,locked=False)
        self.reject('design.paste',target_layer='destination')
        self.run_cmd('layer.set',name='destination',visible=True)
        state = self.run_cmd('design.paste',target_layer='destination')
        self.assertEqual(len(state['selected']),2)
        self.assertEqual([e.layer for e in self.b.session.project.entities],['source','source','destination','destination'])
        self.assertEqual(self.b.session.project.entities[-1].corner_a,Point(9,3))
        self.assertEqual(state['history']['entries'][-1],'Paste to layer destination')

    def test_units_clipboard_snapshot_and_cross_document(self):
        self.fixture()
        self.b.session.project.units = 'mm'
        self.run_cmd('design.copy')
        self.b.session.project.entities[0].corner_a = Point(99,99)
        self.run_cmd('project.new',units='mm')
        self.run_cmd('design.paste',target_layer='0')
        self.assertEqual(self.b.session.project.entities[0].corner_a,Point(-2,3))
        self.run_cmd('project.new',units='in')
        self.reject('design.paste')

    def test_cut_group_atomic_undo_and_protection(self):
        self.fixture()
        before = self.b.session.project.to_dict()
        self.run_cmd('design.cut')
        self.assertEqual(self.b.session.project.entities,[])
        self.assertEqual(self.b.session._history_index,1)
        self.run_cmd('history.undo')
        self.assertEqual(self.b.session.project.to_dict(),before)
        self.run_cmd('selection.set',entity_ids=['a'])
        self.b.session.project.entities[1].layer='blocked'
        self.run_cmd('layer.set',name='blocked',locked=True)
        self.reject('design.cut')
        self.reject('design.copy')

    def test_led_ownership_cut_paste_and_missing_plan_atomic(self):
        layout=LEDLayout((LEDPlacement('led','a','product',1,2,0,.1,.2),))
        self.fixture(led_plans=[dict(id='plan',layout=layout.to_dict(),linked_entity_ids=['a'])])
        self.run_cmd('design.copy')
        self.run_cmd('design.paste')
        plan=self.b.session.project.metadata['led_plans'][0]
        placements=plan['layout']['placements']
        self.assertEqual(len(placements),2)
        self.assertNotEqual(placements[0]['placement_id'],placements[1]['placement_id'])
        self.assertEqual(placements[1]['owner_part_id'],self.b.session.project.entities[2].id)
        self.assertEqual(placements[1]['x_in'],1)
        self.run_cmd('design.cut')
        self.assertEqual(len(self.b.session.project.metadata['led_plans'][0]['layout']['placements']),1)
        self.run_cmd('design.paste')
        self.assertEqual(len(self.b.session.project.metadata['led_plans'][0]['layout']['placements']),2)
        self.run_cmd('project.new')
        self.reject('design.paste')

    def test_empty_machining_stale_and_import_preserves_clipboard(self):
        self.reject('design.paste')
        self.fixture()
        self.run_cmd('design.copy')
        clipboard=copy.deepcopy(self.b.clipboard)
        self.run_cmd('project.save',path=str(self.folder/'source.camjob.json'))
        self.run_cmd('design.import',path=str(self.folder/'source.camjob.json'))
        self.assertEqual(self.b.clipboard,clipboard)
        digest=self.b.digest()
        self.assertFalse(self.b.handle(dict(command='design.paste',revision=-1))['ok'])
        self.assertEqual(self.b.digest(),digest)
        self.b.session.project.operations.append(RouterOffsetOperation(name='Cut',tool_station='T1',target_depth=.1,safe_z=.2,entity_ids=['a']))
        self.reject('design.paste')
        self.reject('design.cut')

    def test_registered_hidden_outline_follows_owner_without_becoming_visible(self):
        self.fixture()
        source=Rectangle(id='source-ref',layer='reference',corner_a=Point(-2.,3.),corner_b=Point(8.,9.),
            metadata=dict(reference_only=True,tab_slot_source_reference=True,mounting_owner_entity_id='a'))
        self.b.session.project.entities.append(source)
        self.run_cmd('layer.set',name='reference',visible=False,locked=True)
        self.run_cmd('design.copy')
        self.assertEqual(len(self.b.clipboard),3)
        state=self.run_cmd('design.paste',target_layer='0')
        copies=self.b.session.project.entities[-3:]
        self.assertEqual(copies[-1].metadata['mounting_owner_entity_id'],copies[0].id)
        self.assertTrue(copies[-1].metadata['annotation_hidden'])
        self.assertNotIn(copies[-1].id,state['selected'])
        self.assertEqual(self.b.session.project.entities[2].to_dict(),source.to_dict())


if __name__ == '__main__':
    unittest.main()

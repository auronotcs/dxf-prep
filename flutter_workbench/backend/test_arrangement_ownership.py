"""Arrangement ownership, production-body registration and persistence regressions."""
import copy
import tempfile
import unittest
from pathlib import Path
from design_bridge import Bridge
from arrangement_support import ArrangementSupport
from cam_core.model import CamProject, Point, Rectangle, Polyline
from cam_core.layout import entity_bounds
from cam_core.transform import transform_entity
from cam_core.tab_slot_mounting import TabSlotRailSettings, resolve_tab_slot_placement, generate_tab_slot_part
from cam_core.led_layout import LEDLayout, LEDPlacement


class ArrangementOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.b = Bridge(self.folder/'preferences.json')

    def command(self, command, **params):
        result = self.b.handle(dict(command=command, params=params, revision=self.b.revision))
        self.assertTrue(result['ok'], result.get('error'))
        return result

    def install(self, entities, selected, **metadata):
        self.b.session.project.entities = entities
        self.b.session.project.metadata.update(metadata)
        self.b.session.project = CamProject.from_dict(self.b.session.project.to_dict())
        self.b.session.selected_ids = set(selected)
        self.b.session._reset_history()
        self.b.history_labels = ['Fixture']
        self.b.saved_digest = self.b.digest()

    def preview(self, **params):
        before = self.b.session.project.to_dict()
        result = self.command('design.preview_shapes', kind='arrange', **params)['preview']
        self.assertEqual(self.b.session.project.to_dict(), before)
        return result

    def rect(self, identity, x, y, **metadata):
        return Rectangle(id=identity, corner_a=Point(x,y), corner_b=Point(x+3,y+5), metadata=metadata)

    def test_transitive_compound_mounting_groups_expand_and_roundtrip(self):
        a = self.rect('a',0,0,group_id='g')
        b = self.rect('b',4,0,group_id='g',compound_id='c')
        c = self.rect('c',8,0,compound_id='c',mounting_part_id='m')
        d = self.rect('d',12,0,mounting_part_id='m')
        anchor = self.rect('anchor',30,20)
        self.install([a,b,c,d,anchor], ['a','anchor'])
        items = self.b.snapshot()['arrangementItems']
        self.assertEqual(items[0]['entityIds'],['a','b','c','d'])
        self.assertEqual(len(items),2)
        before = self.b.session.project.to_dict()
        preview = self.preview(action='align_top', reference='object', reference_item_id='anchor')
        self.assertEqual(len(preview['entities']),5)
        self.assertEqual(self.b.session.selected_ids,{'a','anchor'})
        self.command('design.apply_shapes',token=preview['token'])
        after = self.b.session.project.to_dict()
        self.assertEqual(self.b.session._history_index,1)
        self.assertEqual(self.b.session.selected_ids,{'a','b','c','d','anchor'})
        for entity in self.b.session.project.entities[:4]:
            self.assertEqual(entity_bounds(entity)[1],20)
        self.assertEqual(self.b.session.project.entities[-1].to_dict(),anchor.to_dict())
        path = self.folder/'assembly.camjob.json'
        self.command('project.save',path=str(path))
        self.command('history.undo')
        self.assertEqual(self.b.session.project.to_dict(),before)
        self.command('history.redo')
        self.assertEqual(self.b.session.project.to_dict(),after)
        self.command('project.open',path=str(path))
        self.assertEqual(self.b.session.project.to_dict(),after)

    def test_hidden_locked_member_blocks_entire_move_and_stale_apply(self):
        a = self.rect('a',0,0,compound_id='c')
        b = self.rect('b',4,0,compound_id='c'); b.layer='blocked'
        self.install([a,b], ['a'])
        for settings in ({'locked':True},{'visible':False,'locked':False}):
            self.command('layer.set',name='blocked',**settings)
            before=self.b.digest()
            result=self.b.handle(dict(command='design.preview_shapes',params=dict(kind='arrange',action='align_top',reference='plate')))
            self.assertFalse(result['ok'])
            self.assertIn('associated',result['error'])
            self.assertEqual(self.b.digest(),before)
        self.command('layer.set',name='blocked',visible=True,locked=False)
        preview=self.preview(action='align_top',reference='plate')
        self.b.session.project.entities[1].metadata['changed']='outside revision'
        self.assertFalse(self.b.handle(dict(command='design.apply_shapes',params=dict(token=preview['token'])))['ok'])

    def test_locked_compound_reference_is_one_stationary_anchor(self):
        a=self.rect('a',20,10,compound_id='c'); a.layer='lock'
        b=self.rect('b',24,10,compound_id='c')
        moving=self.rect('moving',0,0)
        self.install([a,b,moving],['moving'],layer_settings={'lock':{'locked':True}})
        refs=self.b.snapshot()['lockedArrangementReferences']
        self.assertEqual(refs[0]['entityIds'],['a','b'])
        preview=self.preview(action='space_horizontal_gap',reference='locked',reference_item_id=refs[0]['id'],gap=2)
        self.command('design.apply_shapes',token=preview['token'])
        self.assertEqual([e.to_dict() for e in self.b.session.project.entities[:2]],[a.to_dict(),b.to_dict()])
        self.assertEqual(entity_bounds(self.b.session.project.entities[-1]),(15,0,18,5))
        self.assertEqual(self.b.session.selected_ids,{'moving'})

    def tabbed(self):
        settings=TabSlotRailSettings(nominal_tab_width=1,minimum_tab_width=.5,letter_thickness=.5,
            rail_thickness=.25,rail_width=2,tab_projection=.25,slot_width_clearance=.08,
            slot_thickness_clearance=.08,cutter_diameter=.25,root_relief_radius=.125,
            edge_end_clearance=.1,short_edge_center_threshold=4,rail_slot_center_y=1,tolerance=1e-6)
        body=Polyline(id='letter',closed=True,points=[Point(0,0),Point(3,0),Point(3,5),Point(0,5)],metadata={'mounting_part_id':'part'})
        placement,_=resolve_tab_slot_placement(body,Point(.4,.05),settings,pair_id='pair')
        generated=generate_tab_slot_part(body,(placement,),settings)
        displayed=generated.bent_result.modified_cut_outline
        displayed.id='letter'; displayed.metadata['tab_slot_letter_outline']=True
        source=copy.deepcopy(body); source.id='source'; source.layer='source'
        source.metadata.update(reference_only=True,tab_slot_source_reference=True,mounting_owner_entity_id='letter')
        rail=generated.rail_slots[0]; rail.id='rail'
        rail.metadata['mounting_part_id']='rail-part'
        plan=dict(owner_entity_id='letter',settings=settings.to_dict(),placements=[placement.to_dict()],
            base_trim={'baseline_y':0,'trim_owner_ids':[]})
        # Tkinter assigns the persisted reference a different ID, but its saved
        # placements still point at the original source/visible owner ID.
        return displayed,source,rail,plan

    def test_body_bounds_tabs_hidden_source_and_pair_metadata_move_once(self):
        letter,source,rail,plan=self.tabbed()
        anchor=self.rect('anchor',20,10)
        self.install([letter,source,rail,anchor],['letter','anchor'],tab_slot_rail_plans=[plan],
            layer_settings={'source':{'visible':False,'locked':True}})
        before=self.b.session.project.to_dict()
        original_y=entity_bounds(letter)[1]
        self.assertLess(original_y,0)
        full=self.preview(action='align_bottom',reference='object',reference_item_id='anchor',use_body_bounds=False)
        self.assertAlmostEqual(full['entities'][0]['bounds'][1],10)
        body=self.preview(action='align_bottom',reference='object',reference_item_id='anchor',use_body_bounds=True)
        self.assertAlmostEqual(body['entities'][0]['bounds'][1],original_y+10)
        self.command('design.apply_shapes',token=body['token'])
        values={e.id:e for e in self.b.session.project.entities}
        self.assertEqual(entity_bounds(values['source']),(0,10,3,15))
        self.assertEqual(values['rail'].to_dict(),rail.to_dict())
        self.assertEqual(values['anchor'].to_dict(),anchor.to_dict())
        changed=self.b.session.project.metadata['tab_slot_rail_plans'][0]
        self.assertEqual(changed['placements'][0]['assembly_center'],[1.5,10])
        self.assertEqual(changed['placements'][0]['rail_slot_center'],plan['placements'][0]['rail_slot_center'])
        self.assertEqual(changed['base_trim']['baseline_y'],10)
        self.assertEqual(self.b.session.selected_ids,{'letter','anchor'})
        info=ArrangementSupport(self.b.session.project).describe('letter',[values['letter']],body=True)
        self.assertEqual(info['bodyBounds'],(0,10,3,15))
        self.command('history.undo')
        self.assertEqual(self.b.session.project.to_dict(),before)

    def test_rotated_nested_body_registration_and_rail_only_move(self):
        letter,source,rail,plan=self.tabbed()
        displayed=transform_entity(letter,20,10,90)
        self.install([displayed,source,rail],['letter'],tab_slot_rail_plans=[plan],
            layer_settings={'source':{'visible':False,'locked':True}})
        info=ArrangementSupport(self.b.session.project).describe('letter',[displayed],body=True)
        for actual,expected in zip(info['bodyBounds'],(15,10,20,13)):
            self.assertAlmostEqual(actual,expected)
        # Moving the separate rail must update only its saved slot center.
        self.b.session.selected_ids={'rail'}
        before=[displayed.to_dict(),source.to_dict()]
        preview=self.preview(action='align_bottom',reference='plate')
        delta=-entity_bounds(rail)[1]
        self.command('design.apply_shapes',token=preview['token'])
        self.assertEqual([e.to_dict() for e in self.b.session.project.entities[:2]],before)
        moved=self.b.session.project.metadata['tab_slot_rail_plans'][0]
        self.assertEqual(moved['placements'][0]['assembly_center'],plan['placements'][0]['assembly_center'])
        self.assertAlmostEqual(moved['placements'][0]['rail_slot_center'][1],1+delta)

    def test_led_owner_translation_converts_mm_to_inches(self):
        self.b.new({'units':'mm'})
        a=self.rect('a',0,0); anchor=self.rect('anchor',25.4,25.4)
        layout=LEDLayout(placements=(LEDPlacement('led','a','product',1,1,0,.1,.2),))
        self.install([a,anchor],['a','anchor'],led_plans=[{'layout':layout.to_dict()}])
        preview=self.preview(action='align_both_centers',reference='object',reference_item_id='anchor')
        self.command('design.apply_shapes',token=preview['token'])
        placement=self.b.session.project.metadata['led_plans'][0]['layout']['placements'][0]
        self.assertAlmostEqual(placement['x_in'],2)
        self.assertAlmostEqual(placement['y_in'],2)

    def test_unregistered_body_refuses_guess_but_full_outline_can_move(self):
        letter,source,rail,plan=self.tabbed()
        letter.points[0]=Point(letter.points[0].x+.5,letter.points[0].y)
        self.install([letter,source,rail],['letter'],tab_slot_rail_plans=[plan],
            layer_settings={'source':{'visible':False,'locked':True}})
        before=self.b.session.project.to_dict()
        result=self.b.handle(dict(command='design.preview_shapes',params=dict(kind='arrange',
            action='align_top',reference='plate',use_body_bounds=True)))
        self.assertFalse(result['ok'])
        self.assertIn('original body bounds',result['error'])
        self.assertEqual(self.b.session.project.to_dict(),before)
        self.assertFalse(self.b.snapshot()['dirty'])
        preview=self.preview(action='align_top',reference='plate',use_body_bounds=False)
        self.assertIn('token',preview)


if __name__=='__main__': unittest.main()

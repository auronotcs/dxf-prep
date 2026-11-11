import copy
import unittest
from test_design_bridge import DesignBridgeTests
from cam_core.layout import entity_bounds


class PatchworkBridgeTests(unittest.TestCase):
    setUp = DesignBridgeTests.setUp
    run_command = DesignBridgeTests.run_command

    def preview(self, areas, **extra):
        result = self.bridge.handle(dict(command='design.patchwork_preview',params=dict(plate_number=1,areas=areas,**extra)))
        self.assertTrue(result['ok'], result.get('error'))
        return result['preview']

    def seed(self):
        p = self.preview([],repeat=dict(width=10,height=5,count=2,x=2,y=3,gap=2,direction='x',name='Drop'))
        self.run_command('design.patchwork_apply',token=p['token'])
        return p['areas']

    def test_repeat_edit_remove_undo_round_trip_and_no_geometry(self):
        before = self.bridge.snapshot()
        p = self.preview([],repeat=dict(width=10,height=5,count=2,x=2,y=3,gap=2,direction='x',name='Drop'))
        self.assertEqual(self.bridge.snapshot(),before)
        self.assertEqual([a['x'] for a in p['areas']],[2,14])
        self.run_command('design.patchwork_apply',token=p['token'])
        self.assertEqual(self.bridge.session.project.entities,[])
        self.assertEqual(self.bridge.snapshot()['history']['index'],1)
        original = copy.deepcopy(self.bridge.session.project.to_dict())
        p = self.preview([dict(p['areas'][0],name='Renamed',x=1)])
        self.run_command('design.patchwork_apply',token=p['token'])
        self.assertEqual(len(self.bridge.snapshot()['patchworkAreas']),1)
        self.run_command('history.undo')
        self.assertEqual(self.bridge.session.project.to_dict(),original)
        path=self.folder/'patchwork.camjob.json'
        self.run_command('project.save',path=str(path))
        self.run_command('project.new')
        self.run_command('project.open',path=str(path))
        self.assertEqual(self.bridge.session.project.to_dict(),original)

    def test_invalid_overlap_outside_stale_and_plate_shrink(self):
        areas = self.seed()
        for draft in [[areas[0],dict(areas[1],x=3)],[dict(areas[0],width=1000)],[areas[0],areas[0]]]:
            before=self.bridge.snapshot()
            self.assertFalse(self.bridge.handle(dict(command='design.patchwork_preview',params=dict(plate_number=1,areas=draft)))['ok'])
            self.assertEqual(self.bridge.snapshot(),before)
        p=self.preview(areas)
        self.run_command('geometry.add_rectangle',x=0,y=0,width=1,height=1)
        before=self.bridge.snapshot()
        self.assertFalse(self.bridge.handle(dict(command='design.patchwork_apply',params=dict(token=p['token'])))['ok'])
        self.assertEqual(self.bridge.snapshot(),before)
        self.assertFalse(self.bridge.handle(dict(command='plate.update',params=dict(number=1,width=5,height=5)))['ok'])
        self.assertEqual(self.bridge.snapshot(),before)

    def test_alignment_world_coordinates_and_nesting_stay_in_material(self):
        areas=self.seed()
        self.run_command('plate.update',number=1,x=100,y=20)
        self.run_command('geometry.add_rectangle',x=50,y=0,width=3,height=2)
        p=self.bridge.handle(dict(command='design.preview_shapes',params=dict(kind='arrange',action='align_left',reference='patchwork',patchwork_id=areas[0]['id'])))
        self.assertTrue(p['ok'],p.get('error'))
        self.assertEqual(p['preview']['referenceBounds'],(102,23,112,28))
        self.run_command('design.apply_shapes',token=p['preview']['token'])
        self.assertEqual(entity_bounds(self.bridge.session.project.entities[0])[0],102)
        self.run_command('geometry.add_rectangle',x=50,y=0,width=9,height=4)
        self.run_command('selection.set',entity_ids=[e.id for e in self.bridge.session.project.entities])
        before=self.bridge.session.project.to_dict()
        self.run_command('geometry.auto_nest',spacing=0)
        regions=[a['bounds'] for a in self.bridge.snapshot()['patchworkAreas']]
        for e in self.bridge.session.project.entities:
            x,y,r,t=entity_bounds(e)
            self.assertTrue(any(x>=a and y>=b and r<=c and t<=d for a,b,c,d in regions))
            self.assertEqual(e.metadata['plate_number'],1)
        self.assertEqual(len(self.bridge.snapshot()['plates']),1)
        self.run_command('history.undo')
        self.assertEqual(self.bridge.session.project.to_dict(),before)
        self.run_command('geometry.add_rectangle',x=0,y=0,width=60,height=30)
        before=self.bridge.snapshot()
        self.assertFalse(self.bridge.handle(dict(command='geometry.auto_nest',params=dict(spacing=0)))['ok'])
        self.assertEqual(self.bridge.snapshot(),before)

    def test_auto_place_axis_keeps_existing_piece_and_does_not_rotate(self):
        self.run_command('plate.update',number=1,width=10,height=10)
        areas=[dict(id='fixed',plate_number=1,x=0,y=0,width=6,height=4,name='Fixed')]
        before=self.bridge.snapshot()
        x=self.preview(areas,auto_place=dict(width=4,height=4,name='New',direction='x'))
        y=self.preview(areas,auto_place=dict(width=4,height=4,name='New',direction='y'))
        self.assertEqual((x['areas'][1]['x'],x['areas'][1]['y']),(6,0))
        self.assertEqual((y['areas'][1]['x'],y['areas'][1]['y']),(0,4))
        self.assertEqual(x['areas'][0],areas[0])
        self.assertEqual(self.bridge.snapshot(),before)
        self.run_command('plate.update',number=1,width=5,height=3)
        before=self.bridge.snapshot()
        result=self.bridge.handle(dict(command='design.patchwork_preview',params=dict(plate_number=1,areas=[],
            auto_place=dict(width=3,height=4,name='Too tall',direction='x'))))
        self.assertFalse(result['ok'])
        self.assertEqual(self.bridge.snapshot(),before)

    def test_pack_preserves_ids_sizes_order_and_other_plates(self):
        self.run_command('plate.update',number=1,width=10,height=10,x=100,y=20)
        self.run_command('plate.add',width=10,height=10,x=200,y=20)
        other=dict(id='other',plate_number=2,x=1,y=1,width=2,height=2,name='Other plate')
        self.bridge.session.project.metadata['patchwork_areas']=[other]
        self.run_command('plate.set_active',number=1)
        areas=[dict(id='a',x=4,y=6,width=6,height=4,name='A'),
               dict(id='b',x=0,y=6,width=4,height=4,name='B'),
               dict(id='c',x=0,y=0,width=10,height=6,name='C')]
        self.run_command('geometry.add_rectangle',x=101,y=21,width=1,height=1)
        before=self.bridge.session.project.to_dict()
        selected=set(self.bridge.session.selected_ids)
        p=self.preview(areas,pack_direction='x')
        self.assertEqual([(a['x'],a['y']) for a in p['areas']],[(0,0),(6,0),(0,4)])
        for original,packed in zip(areas,p['areas']):
            for key in ('id','name','width','height'): self.assertEqual(original[key],packed[key])
        self.assertEqual(self.bridge.session.project.to_dict(),before)
        self.run_command('design.patchwork_apply',token=p['token'])
        self.assertEqual(self.bridge.session.selected_ids,selected)
        self.assertEqual(self.bridge.session.project.to_dict()['entities'],before['entities'])
        self.assertEqual(self.bridge.session.project.metadata['patchwork_areas'][0],other)
        self.assertEqual(self.bridge.snapshot()['patchworkAreas'][1]['bounds'],[100,20,106,24])
        self.run_command('history.undo')
        self.assertEqual(self.bridge.session.project.to_dict(),before)

    def test_table_envelope_inch_mm_atomic_apply_undo_and_save(self):
        for units,factor in [('in',1),('mm',25.4)]:
            self.run_command('project.new',units=units)
            self.run_command('plate.update',number=1,x=200*factor,y=10*factor,name='Keep name')
            self.run_command('geometry.add_rectangle',x=201*factor,y=11*factor,width=factor,height=factor)
            before=self.bridge.session.project.to_dict()
            history=self.bridge.session._history_index
            p=self.preview([],envelope=dict(width=120*factor,height=62*factor),
                repeat=dict(width=120*factor,height=12*factor,count=5,x=0,y=0,gap=0,direction='y',name='Bar'))
            self.assertEqual(self.bridge.session.project.to_dict(),before)
            self.assertTrue(p['envelopeChanged'])
            self.assertEqual((p['plate']['x'],p['plate']['y'],p['plate']['name']),(200*factor,10*factor,'Keep name'))
            self.run_command('design.patchwork_apply',token=p['token'])
            after=copy.deepcopy(self.bridge.session.project.to_dict())
            self.assertEqual(self.bridge.session._history_index,history+1)
            self.assertEqual(after['entities'],before['entities'])
            self.assertEqual(len(self.bridge.snapshot()['patchworkAreas']),5)
            self.assertEqual(self.bridge.snapshot()['plates'][0]['width'],120*factor)
            self.run_command('history.undo')
            self.assertEqual(self.bridge.session.project.to_dict(),before)
            self.run_command('history.redo')
            self.assertEqual(self.bridge.session.project.to_dict(),after)
            path=self.folder/f'table-{units}.camjob.json'
            self.run_command('project.save',path=str(path))
            self.run_command('project.new')
            self.run_command('project.open',path=str(path))
            self.assertEqual(self.bridge.session.project.to_dict(),after)

    def test_failed_pack_resize_and_stale_envelope_leave_document_unchanged(self):
        areas=self.seed()
        before=self.bridge.snapshot()
        attempts=[dict(envelope=dict(width=5,height=5)),
                  dict(envelope=dict(width=5,height=5),pack_direction='x'),
                  dict(pack_direction='diagonal'),dict(pack_direction='x',auto_place=dict(width=1,height=1)),
                  dict(envelope=dict(width=-1,height=62))]
        for values in attempts:
            result=self.bridge.handle(dict(command='design.patchwork_preview',params=dict(plate_number=1,areas=areas,**values)))
            self.assertFalse(result['ok'])
            self.assertEqual(self.bridge.snapshot(),before)
            self.assertIsNone(self.bridge.patchwork_preview)
        p=self.preview(areas,envelope=dict(width=120,height=62))
        self.run_command('plate.update',number=1,name='Changed after preview')
        before=self.bridge.snapshot()
        self.assertFalse(self.bridge.handle(dict(command='design.patchwork_apply',params=dict(token=p['token'])))['ok'])
        self.assertEqual(self.bridge.snapshot(),before)
        # A table expansion must not silently shift the neighboring plate.
        self.run_command('plate.add',x=96,y=0,width=10,height=48)
        before=self.bridge.snapshot()
        self.assertFalse(self.bridge.handle(dict(command='design.patchwork_preview',params=dict(plate_number=1,
            areas=areas,envelope=dict(width=120,height=62))))['ok'])
        self.assertEqual(self.bridge.snapshot(),before)


if __name__ == '__main__': unittest.main()

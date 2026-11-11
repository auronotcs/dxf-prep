"""Integration checks against the captured production core, using temporary jobs."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from design_bridge import Bridge, ROOT
from cam_core.model import RouterOffsetOperation


class DesignBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.bridge = Bridge(self.folder / 'layout.json')

    def run_command(self, command, **params):
        result = self.bridge.handle({'command':command,'params':params,'revision':self.bridge.revision})
        self.assertTrue(result['ok'], (command,result.get('error')))
        return result.get('state',{})

    def rectangle(self, x=0, y=0, **extra):
        return self.run_command('geometry.add_rectangle',x=x,y=y,width=10,height=5,**extra)

    def select_all(self):
        return self.run_command('selection.set',entity_ids=[e.id for e in self.bridge.session.project.entities])

    def point_command(self, operation, **params):
        return self.run_command('design.point_edit',entity_id=self.bridge.session.project.entities[0].id,
            expected_revision=self.bridge.revision,operation=operation,**params)

    def test_graphical_rectangle_identity_move_delete_and_undo(self):
        self.rectangle()
        e = self.bridge.session.project.entities[0]
        e.metadata['point_test'] = 'preserved'
        identity = e.id
        self.run_command('design.point_begin',entity_id=identity,expected_revision=self.bridge.revision)
        before = self.bridge.session.project.to_dict()
        history = self.bridge.session._history_index
        state = self.point_command('move',indices=[0,3],dx=2,dy=1)
        e = state['entities'][0]
        self.assertEqual(e['id'],identity)
        self.assertEqual(e['metadata']['point_test'],'preserved')
        self.assertEqual(e['points'],[{'x':2,'y':1},{'x':10,'y':0},{'x':10,'y':5},{'x':2,'y':6}])
        self.assertEqual(self.bridge.session._history_index,history+1)
        self.run_command('history.undo')
        self.assertEqual(self.bridge.session.project.to_dict(),before)
        self.select_all()
        state = self.point_command('delete',indices=[1])
        self.assertEqual(len(state['entities'][0]['points']),3)
        self.assertEqual(state['entities'][0]['id'],identity)
        self.run_command('history.undo')
        self.assertEqual(self.bridge.session.project.to_dict(),before)
        self.run_command('history.undo')
        self.assertEqual(self.bridge.snapshot()['entities'][0]['kind'],'rectangle')

    def test_point_guards_never_delete_objects_or_record_history(self):
        self.run_command('geometry.add_line',x1=0,y1=0,x2=10,y2=0)
        identity = self.bridge.session.project.entities[0].id
        base = dict(entity_id=identity,expected_revision=self.bridge.revision,operation='delete',indices=[0])
        for patch in [{},{'indices':[]},{'indices':[True]},{'indices':[-1]},{'indices':[2]},
                      {'entity_id':'wrong'},{'expected_revision':-1},{'expected_revision':True},
                      {'operation':'move','indices':[0],'dx':float('nan'),'dy':0}]:
            before = self.bridge.snapshot()
            result = self.bridge.handle({'command':'design.point_edit','params':dict(base,**patch)})
            self.assertFalse(result['ok'],patch)
            self.assertEqual(self.bridge.snapshot(),before)
        self.run_command('layer.set',name='0',locked=True)
        result = self.bridge.handle({'command':'design.point_begin','params':dict(base,expected_revision=self.bridge.revision)})
        self.assertFalse(result['ok'])
        self.assertEqual(len(self.bridge.session.project.entities),1)

    def test_point_insert_line_and_closed_seam_preserve_identity(self):
        self.run_command('geometry.add_line',x1=0,y1=0,x2=10,y2=0)
        identity = self.bridge.session.project.entities[0].id
        state = self.point_command('insert',x=5,y=2)
        self.assertEqual(state['entities'][0]['kind'],'polyline')
        self.assertFalse(state['entities'][0]['closed'])
        self.assertEqual(state['entities'][0]['id'],identity)
        self.assertEqual(state['entities'][0]['points'][1],{'x':5,'y':2})
        self.run_command('history.undo')
        self.assertEqual(self.bridge.snapshot()['entities'][0]['kind'],'line')
        self.run_command('project.new')
        self.rectangle()
        self.run_command('design.point_begin',entity_id=self.bridge.session.project.entities[0].id,
            expected_revision=self.bridge.revision)
        state = self.point_command('insert',x=-1,y=2)
        self.assertEqual(state['entities'][0]['points'][-1],{'x':-1,'y':2})
        self.assertTrue(state['entities'][0]['closed'])
        before = self.bridge.snapshot()
        self.point_command('move',indices=[0],dx=0,dy=0)
        self.assertEqual(self.bridge.snapshot()['history'],before['history'])

    def test_new_transform_undo_redo_save_reopen(self):
        self.assertFalse(self.bridge.snapshot()['dirty'])
        state = self.rectangle()
        identity = state['selected'][0]
        state = self.run_command('geometry.transform',dx=7,dy=3)
        self.assertEqual(state['entities'][0]['bounds'], (7,3,17,8))
        self.run_command('history.undo')
        self.assertEqual(self.bridge.snapshot()['entities'][0]['bounds'], (0,0,10,5))
        state = self.run_command('history.redo')
        path = self.folder/'drawing.camjob.json'
        state = self.run_command('project.save',path=str(path))
        self.assertFalse(state['dirty'])
        self.assertEqual(state['savedPath'],str(path))
        source_bytes=path.read_bytes()
        self.run_command('project.new')
        state=self.run_command('project.open',path=str(path))
        self.assertEqual(state['entities'][0]['id'],identity)
        self.select_all()
        self.run_command('geometry.transform',dx=1)
        result=self.bridge.handle({'command':'project.save','params':{'path':str(path)}})
        self.assertFalse(result['ok'])
        self.assertEqual(source_bytes,path.read_bytes())
        copy_path=self.folder/'copy.camjob.json'
        self.run_command('project.save',path=str(copy_path))
        self.assertTrue(copy_path.exists())

    def test_appearance_exact_colors_provenance_undo_and_save(self):
        from cam_core.appearance import ColorValue, ObjectAppearance
        self.rectangle()
        entity=self.bridge.session.project.entities[0]
        entity.appearance=ObjectAppearance(fill_enabled=True,fill=ColorValue.cmyk(11.123456789,20,30,4,
            palette_id='palette',swatch_id='sample',name='Sample',source='Synthetic test'))
        identity=entity.id
        before=entity.appearance.to_dict()
        payload=json.loads(json.dumps(before));payload['stroke_width']=.02
        state=self.run_command('design.appearance',entity_ids=[identity],expected_revision=self.bridge.revision,appearance=payload)
        self.assertEqual(state['entities'][0]['appearance']['fill'],before['fill'])
        self.assertEqual(state['entities'][0]['layer'],'0')
        self.assertEqual(state['entities'][0]['bounds'],(0,0,10,5))
        payload['fill']['channels']=[0,100,100,0]
        state=self.run_command('design.appearance',entity_ids=[identity],expected_revision=self.bridge.revision,appearance=payload)
        self.assertEqual(state['entities'][0]['appearance']['fill']['preview_rgb'],(255,0,0))
        self.assertEqual(state['entities'][0]['appearance']['fill']['palette_id'],'')
        path=self.folder/'appearance.camjob.json'
        self.run_command('project.save',path=str(path))
        self.run_command('design.appearance',entity_ids=[identity],expected_revision=self.bridge.revision,inherit_layer=True)
        self.assertIsNone(self.bridge.session.project.entities[0].appearance)
        self.run_command('history.undo')
        self.assertEqual(self.bridge.session.project.entities[0].appearance.fill.preview_rgb,(255,0,0))
        self.run_command('project.new');state=self.run_command('project.open',path=str(path))
        self.assertEqual(state['entities'][0]['appearance']['stroke_width'],.02)

    def test_appearance_rejects_stale_locked_and_invalid_values_without_history(self):
        from cam_core.appearance import ObjectAppearance
        self.rectangle();identity=self.bridge.session.project.entities[0].id
        payload=ObjectAppearance().to_dict()
        base=dict(entity_ids=[identity],expected_revision=self.bridge.revision,appearance=payload)
        for patch in [dict(expected_revision=-1),dict(entity_ids=[]),
                      dict(appearance=dict(payload,stroke_width=-1)),dict(appearance=dict(payload,fill_enabled='false'))]:
            before=self.bridge.snapshot()
            self.assertFalse(self.bridge.handle({'command':'design.appearance','params':dict(base,**patch)})['ok'])
            self.assertEqual(self.bridge.snapshot(),before)
        self.run_command('layer.set',name='0',locked=True)
        self.assertFalse(self.bridge.handle({'command':'design.appearance','params':dict(base,expected_revision=self.bridge.revision)})['ok'])

    def test_history_navigation_branching_and_saved_state(self):
        self.rectangle()
        state = self.run_command('geometry.add_circle', x=20, y=10, diameter=4)
        self.assertEqual(state['history']['entries'], ['New drawing', 'Add rectangle', 'Add circle'])
        self.assertEqual(state['history']['index'], 2)
        self.run_command('project.save', path=str(self.folder/'history.camjob.json'))
        state = self.run_command('history.goto', index=0)
        self.assertEqual(state['entities'], [])
        self.assertTrue(state['canRedo'])
        self.assertTrue(state['dirty'])
        state = self.run_command('history.goto', index=2)
        self.assertEqual(len(state['entities']), 2)
        self.assertFalse(state['dirty'])
        self.run_command('history.undo')
        state = self.run_command('geometry.add_line', x1=0, y1=0, x2=3, y2=4)
        self.assertEqual(state['history']['entries'], ['New drawing', 'Add rectangle', 'Add line'])
        self.assertFalse(state['canRedo'])
        before = self.bridge.snapshot()
        for target in [-1, 3, True, '1']:
            self.assertFalse(self.bridge.handle({'command':'history.goto','params':{'index':target}})['ok'])
            self.assertEqual(self.bridge.snapshot(), before)
        state = self.run_command('project.new')
        self.assertEqual(state['history'], {'index':0,'entries':['New drawing']})

    def test_selection_length_caption_and_bounds(self):
        state = self.rectangle()
        self.assertEqual(state['selectionMeasurement'], {'caption':'Perimeter','value':30})
        state = self.run_command('geometry.add_line', x1=0, y1=0, x2=3, y2=4)
        self.assertEqual(state['selectionMeasurement'], {'caption':'Length','value':5})
        state = self.select_all()
        self.assertEqual(state['selectionMeasurement'], {'caption':'Total length (2)','value':35})
        self.assertEqual(state['selectionBounds'], (0,0,10,5))
        state = self.run_command('selection.clear')
        self.assertIsNone(state['selectionMeasurement'])

    def test_arrangement_fixed_reference_plate_gap_and_history(self):
        first = self.rectangle(x=10,y=3)['selected'][0]
        second = self.rectangle(x=40,y=12)['selected'][0]
        self.select_all()
        before = self.bridge.snapshot()['entities']
        preview = self.preview(kind='arrange',action='align_top',reference='object',reference_item_id=first)
        self.assertEqual(preview['entities'][0], before[0])
        self.assertEqual(preview['entities'][1]['bounds'], (40,3,50,8))
        state = self.run_command('design.apply_shapes',token=preview['token'])
        self.assertEqual(state['history']['entries'][-1], 'Align top')
        self.run_command('history.undo')
        self.assertEqual(self.bridge.snapshot()['entities'],before)
        self.run_command('history.redo')
        self.select_all()
        preview = self.preview(kind='arrange',action='space_horizontal_gap',reference='object',reference_item_id=second,gap=2)
        self.assertEqual(preview['gap'],2)
        self.assertEqual(preview['entities'][1]['bounds'], (40,3,50,8))
        self.assertEqual(preview['entities'][0]['bounds'], (28,3,38,8))
        self.run_command('plate.define',plates=[dict(number=1,x=100,y=200,width=96,height=48,name='Offset plate')])
        self.select_all()
        preview = self.preview(kind='arrange',action='align_both_centers',reference='plate')
        self.assertEqual(preview['referenceBounds'], (100,200,196,248))
        self.assertTrue(all(e['bounds']==(143,221.5,153,226.5) for e in preview['entities']))
        before=self.bridge.snapshot()
        self.assertFalse(self.bridge.handle({'command':'design.preview_shapes','params':dict(kind='arrange',action='space_horizontal_gap',gap=-1)})['ok'])
        self.assertEqual(self.bridge.snapshot(),before)

    def test_locked_arrangement_reference_align_gap_undo_and_save(self):
        for units in ('in','mm'):
            self.run_command('project.new',units=units)
            anchor=self.rectangle(x=10,y=20,layer='reference')['selected'][0]
            self.run_command('layer.set',name='reference',locked=True)
            moving=self.rectangle(x=50,y=2)['selected'][0]
            before=self.bridge.snapshot()
            self.assertEqual(before['lockedArrangementReferences'][0]['entityIds'],[anchor])
            preview=self.preview(kind='arrange',action='align_top',reference='locked',reference_item_id=anchor)
            self.assertEqual(self.bridge.snapshot(),before)
            self.assertEqual(preview['referenceBounds'],(10,20,20,25))
            self.assertEqual(preview['movedCount'],1)
            self.assertEqual(preview['entities'][0]['bounds'],(50,20,60,25))
            self.assertEqual([e['id'] for e in preview['entities']],[moving])
            state=self.run_command('design.apply_shapes',token=preview['token'])
            self.assertEqual(state['entities'][0],before['entities'][0])
            self.assertEqual(state['selected'],[moving])
            self.assertEqual(state['history']['index'],before['history']['index']+1)
            self.run_command('history.undo')
            self.assertEqual(self.bridge.snapshot()['entities'],before['entities'])
            self.run_command('history.redo')
            self.run_command('selection.set',entity_ids=[anchor,moving])
            self.assertEqual(self.bridge.snapshot()['selected'],[moving])
            preview=self.preview(kind='arrange',action='space_horizontal_gap',reference='locked',reference_item_id=anchor,gap=3)
            self.assertEqual(preview['entities'][0]['bounds'],(23,20,33,25))
            state=self.run_command('design.apply_shapes',token=preview['token'])
            path=self.folder/f'locked-{units}.camjob.json'
            self.run_command('project.save',path=str(path))
            self.run_command('project.new')
            reopened=self.run_command('project.open',path=str(path))
            self.assertEqual(reopened['entities'],state['entities'])
            self.assertTrue(next(x for x in reopened['layers'] if x['name']=='reference')['locked'])

    def test_locked_group_reference_hidden_members_and_stale_preview(self):
        first=self.rectangle(layer='reference')['selected'][0]
        second=self.rectangle(x=12,layer='other')['selected'][0]
        self.select_all()
        self.run_command('design.group')
        group=self.bridge.session.project.entities[0].metadata['group_id']
        self.run_command('layer.set',name='reference',locked=True)
        moving=self.rectangle(x=40,y=9)['selected'][0]
        before=self.bridge.snapshot()
        self.assertEqual(set(before['lockedArrangementReferences'][0]['entityIds']),{first,second})
        preview=self.preview(kind='arrange',action='space_horizontal_gap',reference='locked',reference_item_id=group,gap=2)
        self.assertEqual(preview['entities'][0]['bounds'],(24,9,34,14))
        state=self.run_command('design.apply_shapes',token=preview['token'])
        self.assertEqual(state['entities'][:2],before['entities'][:2])
        self.run_command('selection.set',entity_ids=[moving])
        preview=self.preview(kind='arrange',action='align_top',reference='locked',reference_item_id=group)
        self.run_command('layer.set',name='other',visible=False)
        self.assertEqual(self.bridge.snapshot()['lockedArrangementReferences'],[])
        before=self.bridge.snapshot()
        result=self.bridge.handle({'command':'design.apply_shapes','params':{'token':preview['token']}})
        self.assertFalse(result['ok'])
        self.assertEqual(self.bridge.snapshot(),before)
        self.assertFalse(self.bridge.handle({'command':'design.preview_shapes','params':dict(kind='arrange',action='align_top',reference='locked',reference_item_id=group)})['ok'])

    def test_locked_reference_rejects_invalid_targets_and_untracked_drift(self):
        anchor=self.rectangle(layer='reference')['selected'][0]
        self.run_command('layer.set',name='reference',locked=True)
        moving=self.rectangle(x=30,y=10)['selected'][0]
        for target in ('missing',moving):
            before=self.bridge.snapshot()
            self.assertFalse(self.bridge.handle({'command':'design.preview_shapes','params':dict(kind='arrange',action='align_top',reference='locked',reference_item_id=target)})['ok'])
            self.assertEqual(self.bridge.snapshot(),before)
        preview=self.preview(kind='arrange',action='align_top',reference='locked',reference_item_id=anchor)
        self.bridge.session.project.entities[0].metadata['annotation_hidden']=True
        before=self.bridge.snapshot()
        self.assertFalse(self.bridge.handle({'command':'design.apply_shapes','params':{'token':preview['token']}})['ok'])
        self.assertEqual(self.bridge.snapshot(),before)
        self.assertEqual(before['lockedArrangementReferences'],[])
        # Locked geometry stays protected even when callers bypass selection.set.
        self.bridge.session.selected_ids={anchor,moving}
        self.assertFalse(self.bridge.handle({'command':'design.arrange','params':dict(action='align_top',reference='locked',reference_item_id=anchor)})['ok'])

    def test_arrangement_groups_equal_spacing_and_rejection(self):
        first=self.rectangle()['selected'][0]
        second=self.rectangle(x=12)['selected'][0]
        self.select_all()
        self.run_command('design.group')
        group=self.bridge.session.project.entities[0].metadata['group_id']
        third=self.rectangle(x=40)['selected'][0]
        self.select_all()
        preview=self.preview(kind='arrange',action='space_horizontal_gap',reference='object',reference_item_id=group,gap=3)
        self.assertEqual(preview['entities'][0]['bounds'],(0,0,10,5))
        self.assertEqual(preview['entities'][1]['bounds'],(12,0,22,5))
        self.assertEqual(preview['entities'][2]['bounds'],(25,0,35,5))
        self.run_command('selection.set',entity_ids=[first,third])
        # Partial selection still includes every associated member in the preview.
        preview=self.preview(kind='arrange',action='align_top',reference='plate')
        self.assertEqual({e['id'] for e in preview['entities']},{first,second,third})
        self.select_all()
        self.run_command('design.ungroup')
        preview=self.preview(kind='arrange',action='space_horizontal_equal')
        self.assertEqual(preview['gap'],10)
        self.assertEqual(preview['entities'][1]['bounds'],(20,0,30,5))
        self.run_command('selection.set',entity_ids=[first,second])
        for params in [dict(action='space_horizontal_equal'),dict(action='align_left',reference='object',reference_item_id='missing')]:
            self.assertFalse(self.bridge.handle({'command':'design.preview_shapes','params':dict(kind='arrange',**params)})['ok'])

    def test_opening_repair_scan_review_apply_undo_and_guarded_context(self):
        self.run_command('geometry.add_line',x1=0,y1=0,x2=1,y2=0)
        first=self.bridge.snapshot()['selected'][0]
        self.run_command('geometry.add_line',x1=1.0001,y1=0,x2=2,y2=0)
        before=self.bridge.snapshot()
        result=self.bridge.handle({'command':'design.repair_scan'})
        self.assertTrue(result['ok']);review=result['review']
        self.assertEqual(review['gaps'],1);self.assertEqual(review['editableCount'],2)
        self.assertEqual(self.bridge.snapshot(),before)
        self.run_command('design.review_repair',token=review['token'])
        self.assertEqual(self.bridge.snapshot()['history'],before['history'])
        preview=self.preview(kind='repair',mode='join',opening_review=True,tolerance=.001)
        self.assertEqual(preview['joinCount'],1)
        result=self.run_command('design.apply_shapes',token=preview['token'])
        self.assertEqual(len(result['entities']),1);self.assertEqual(result['entities'][0]['id'],first)
        self.run_command('history.undo');self.assertEqual(self.bridge.snapshot()['entities'],before['entities'])
        # A manual selection edit invalidates the accepted diagnostic context.
        review=self.bridge.handle({'command':'design.repair_scan'})['review']
        self.run_command('selection.clear')
        self.assertFalse(self.bridge.handle({'command':'design.review_repair','params':{'token':review['token']}})['ok'])
        # A nearby unselected endpoint prevents a partial-chain join at larger tolerance.
        self.run_command('geometry.add_line',x1=1.0012,y1=0,x2=1,y2=2)
        self.run_command('selection.set',entity_ids=[e.id for e in self.bridge.session.project.entities[:2]])
        self.assertFalse(self.bridge.handle({'command':'design.preview_shapes','params':dict(kind='repair',mode='join',opening_review=True,tolerance=.002)})['ok'])

    def test_opening_repair_excludes_whole_hidden_chains_and_reports_backtracks(self):
        self.run_command('geometry.add_line',x1=0,y1=0,x2=1,y2=0)
        self.run_command('geometry.add_line',x1=1.0001,y1=0,x2=2,y2=0)
        self.bridge.session.project.entities[0].metadata['annotation_hidden']=True
        review=self.bridge.handle({'command':'design.repair_scan'})['review']
        self.assertEqual(review['gaps'],1);self.assertEqual(review['editableCount'],0)
        self.assertEqual(review['blockedCount'],2)
        self.assertFalse(self.bridge.handle({'command':'design.review_repair','params':{'token':review['token']}})['ok'])
        self.run_command('project.new',units='mm')
        self.run_command('geometry.add_polyline',points=[dict(x=x,y=0) for x in [0,25.4,25.395,50.8]],closed=False)
        review=self.bridge.handle({'command':'design.repair_scan'})['review']
        self.assertEqual(review['backtracks'],1)
        self.run_command('layer.set',name='0',locked=True)
        review=self.bridge.handle({'command':'design.repair_scan'})['review']
        self.assertEqual(review['editableCount'],0)
        self.assertEqual(review['blockedCount'],1)

    def test_contour_repair_preview_join_markers_identity_undo_and_reopen(self):
        self.run_command('geometry.add_polyline',points=[dict(x=0,y=0),dict(x=2,y=0),dict(x=2,y=1)],closed=False)
        first=self.bridge.snapshot()['selected'][0]
        self.run_command('geometry.add_polyline',points=[dict(x=2.000001,y=1),dict(x=0,y=1),dict(x=0,y=.000001)],closed=False)
        self.select_all()
        before=self.bridge.snapshot()['entities']
        preview=self.preview(kind='repair',mode='join',tolerance=.001,cleanup=True,backtracks=True)
        self.assertEqual(preview['closedCount'],1)
        self.assertEqual(preview['joinCount'],2)
        self.assertEqual(len(preview['markers']['joins']),2)
        self.assertEqual(preview['entities'][0]['id'],first)
        state=self.run_command('design.apply_shapes',token=preview['token'])
        self.assertEqual(len(state['entities']),1)
        self.assertEqual(state['history']['entries'][-1],'Join / repair contours')
        self.run_command('history.undo')
        self.assertEqual(self.bridge.snapshot()['entities'],before)
        self.run_command('history.redo')
        path=self.folder/'repaired.camjob.json'
        self.run_command('project.save',path=str(path))
        state=self.run_command('project.open',path=str(path))
        self.assertTrue(state['entities'][0]['closed'])
        self.assertEqual(len(state['entities'][0]['points']),4)

    def test_repair_points_backtracks_and_rejections(self):
        self.run_command('geometry.add_polyline',points=[dict(x=0,y=0),dict(x=.00001,y=0),dict(x=2,y=0)],closed=False)
        preview=self.preview(kind='repair',mode='points',tolerance=.0001)
        self.assertEqual(preview['removedPoints'],1)
        self.assertEqual(len(preview['markers']['points']),1)
        self.assertEqual(preview['entities'][0]['points'],[dict(x=0,y=0),dict(x=2,y=0)])
        self.run_command('geometry.transform',dx=1)
        self.assertFalse(self.bridge.handle({'command':'design.apply_shapes','params':{'token':preview['token']}})['ok'])
        self.run_command('project.new')
        self.run_command('geometry.add_polyline',points=[dict(x=0,y=0),dict(x=1,y=0),dict(x=.9999,y=0),dict(x=2,y=0)],closed=False)
        preview=self.preview(kind='repair',mode='backtracks',tolerance=.001)
        self.assertGreater(preview['backtrackCount'],0)
        self.assertEqual(preview['joinCount'],0)
        self.assertTrue(preview['markers']['backtracks'])
        self.run_command('project.new')
        for a,b in [((0,0),(1,0)),((1,0),(2,0)),((1,0),(1,1))]:
            self.run_command('geometry.add_line',x1=a[0],y1=a[1],x2=b[0],y2=b[1])
        self.select_all()
        before=self.bridge.snapshot()
        result=self.bridge.handle({'command':'design.preview_shapes','params':dict(kind='repair',tolerance=.001)})
        self.assertFalse(result['ok'])
        self.assertIn('Ambiguous',result['error'])
        self.assertEqual(self.bridge.snapshot(),before)
        self.run_command('layer.set',name='0',locked=True)
        for kind in ['repair','arrange']:
            self.assertFalse(self.bridge.handle({'command':'design.preview_shapes','params':dict(kind=kind,action='align_top')})['ok'])

    def preview(self, **params):
        before = self.bridge.snapshot()
        result = self.bridge.handle({'command':'design.preview_shapes', 'params':params,
                                     'revision':self.bridge.revision})
        self.assertTrue(result['ok'], result.get('error'))
        self.assertEqual(self.bridge.snapshot(), before)
        return result['preview']

    def test_offset_preview_preserves_sources_and_undo_redo(self):
        original = self.rectangle()['entities'][0]
        preview = self.preview(kind='offset', distance=1)
        self.assertEqual(preview['entities'][0]['bounds'], (-1,-1,11,6))
        state = self.run_command('design.apply_shapes', token=preview['token'])
        self.assertEqual(state['entities'][0], original)
        self.assertEqual(len(state['entities']), 2)
        self.assertEqual(state['selected'], [preview['entities'][0]['id']])
        self.assertEqual(state['history']['entries'][-1], 'Offset outline')
        self.run_command('history.undo')
        self.assertEqual(self.bridge.snapshot()['entities'], [original])
        self.run_command('history.redo')
        self.assertEqual(self.bridge.snapshot()['entities'], state['entities'])
        self.run_command('selection.set', entity_ids=[original['id']])
        preview = self.preview(kind='offset', distance=-1)
        self.assertEqual(preview['entities'][0]['bounds'], (1,1,9,4))

    def test_shape_preview_stale_selection_geometry_and_invalid_values(self):
        self.rectangle()
        preview = self.preview(kind='convert', mode='circle', scope='each', diameter=4)
        self.run_command('geometry.transform', dx=2)
        before = self.bridge.snapshot()
        result = self.bridge.handle({'command':'design.apply_shapes', 'params':{'token':preview['token']}})
        self.assertFalse(result['ok'])
        self.assertEqual(self.bridge.snapshot(), before)
        preview = self.preview(kind='convert', mode='outline')
        self.run_command('selection.clear')
        self.assertFalse(self.bridge.handle({'command':'design.apply_shapes','params':{'token':preview['token']}})['ok'])
        self.select_all()
        for params in [dict(kind='offset',distance=0), dict(kind='offset',distance=-100),
                       dict(mode='repair'), dict(mode='outline',tolerance=-1),
                       dict(mode='circle',diameter=float('nan'))]:
            before = self.bridge.snapshot()
            self.assertFalse(self.bridge.handle({'command':'design.preview_shapes','params':params})['ok'])
            self.assertEqual(self.bridge.snapshot(), before)

    def test_convert_and_editable_outline_keep_identity_and_metadata(self):
        original = self.rectangle()['entities'][0]
        entity = self.bridge.session.project.entities[0]
        entity.metadata['custom'] = 'kept'
        preview = self.preview(mode='circle', scope='each', diameter=4)
        self.assertEqual(preview['entities'][0]['bounds'], (3,.5,7,4.5))
        state = self.run_command('design.apply_shapes', token=preview['token'])
        self.assertEqual(state['entities'][0]['id'], original['id'])
        self.assertEqual(state['entities'][0]['metadata']['custom'], 'kept')
        preview = self.preview(mode='outline', tolerance=.001)
        self.assertEqual(preview['entities'][0]['kind'], 'polyline')
        self.assertTrue(preview['entities'][0]['closed'])
        state = self.run_command('design.apply_shapes', token=preview['token'])
        self.assertEqual(len(state['entities']), 1)
        self.assertEqual(state['history']['entries'][-1], 'Editable outlines')
        self.run_command('history.undo')
        self.assertEqual(self.bridge.snapshot()['entities'][0]['kind'], 'circle')
        self.run_command('history.redo')
        path = self.folder/'outline.camjob.json'
        self.run_command('project.save', path=str(path))
        reopened = self.run_command('project.open', path=str(path))
        self.assertEqual(reopened['entities'], state['entities'])

    def test_shape_tools_guard_locked_open_machining_and_millimeters(self):
        self.run_command('project.new', units='mm')
        self.run_command('geometry.add_circle', x=0,y=0,diameter=25.4)
        preview = self.preview(kind='offset', distance=2.54)
        self.assertAlmostEqual(preview['entities'][0]['radius'], 15.24)
        self.run_command('layer.set',name='0',locked=True)
        self.assertFalse(self.bridge.handle({'command':'design.apply_shapes', 'params':{'token':preview['token']}})['ok'])
        self.run_command('layer.set',name='0',locked=False)
        self.run_command('geometry.add_line',x1=0,y1=0,x2=10,y2=0)
        before = self.bridge.snapshot()
        self.assertFalse(self.bridge.handle({'command':'design.preview_shapes','params':{'kind':'offset','distance':1}})['ok'])
        self.assertEqual(self.bridge.snapshot(), before)
        self.select_all()
        self.bridge.session.project.operations.append(RouterOffsetOperation(
            name='Existing',tool_station=1,entity_ids=list(self.bridge.session.selected_ids),
            target_depth=1,safe_z=5))
        for command in ['design.preview_shapes','design.apply_shapes']:
            self.assertFalse(self.bridge.handle({'command':command,'params':{'kind':'offset','distance':1}})['ok'])

    def test_dxf_export_import_and_units(self):
        self.rectangle()
        self.run_command('geometry.add_circle',x=20,y=10,diameter=3)
        path=self.folder/'drawing.dxf'
        self.run_command('project.export_dxf',path=str(path))
        self.run_command('project.new')
        state=self.run_command('project.open',path=str(path))
        self.assertEqual(len(state['entities']),2)
        self.assertEqual(state['units'],'in')
        state=self.run_command('design.import',path=str(path))
        self.assertEqual(len(state['entities']),4)
        self.run_command('project.new',units='mm')
        self.assertEqual(self.bridge.snapshot()['plates'][0]['width'],96*25.4)
        result=self.bridge.handle({'command':'design.import','params':{'path':str(path)}})
        self.assertFalse(result['ok'])

    def test_locked_hidden_layers_and_selection(self):
        self.rectangle(layer='cut')
        identity=self.bridge.snapshot()['selected'][0]
        state=self.run_command('layer.set',name='cut',locked=True)
        self.assertEqual(state['selected'],[])
        state=self.run_command('selection.set',entity_ids=[identity])
        self.assertEqual(state['selected'],[])
        for command,params in [('design.shape',dict(kind='star',width=3,height=3,layer='cut')),
                               ('geometry.add_circle',dict(x=0,y=0,diameter=2,layer='cut'))]:
            self.assertFalse(self.bridge.handle({'command':command,'params':params})['ok'])
        self.run_command('layer.set',name='cut',locked=False,visible=False)
        self.assertEqual(self.run_command('selection.set',entity_ids=[identity])['selected'],[])

    def test_delete_layer_keeps_geometry_and_undo_restores_layer(self):
        self.run_command('layer.set', name='Cut', color='#ff0000')
        state = self.rectangle(layer='Cut')
        identity = state['entities'][0]['id']
        before = self.bridge.session.project.to_dict()
        state = self.run_command('design.delete_layer', name='Cut')
        self.assertEqual(state['entities'][0]['id'], identity)
        self.assertEqual(state['entities'][0]['layer'], '0')
        self.assertNotIn('Cut', [l['name'] for l in state['layers']])
        self.assertIn(identity, state['selected'])
        self.run_command('history.undo')
        self.assertEqual(self.bridge.session.project.to_dict(), before)
        self.run_command('history.redo')
        self.assertEqual(self.bridge.snapshot()['entities'][0]['layer'], '0')
        self.run_command('layer.set', name='Empty')
        state = self.run_command('design.delete_layer', name='Empty')
        self.assertNotIn('Empty', [l['name'] for l in state['layers']])
        for name in ['0', 'Missing']:
            self.assertFalse(self.bridge.handle({'command':'design.delete_layer','params':{'name':name}})['ok'])
        self.run_command('layer.set', name='Locked', locked=True)
        before = self.bridge.session.project.to_dict()
        self.assertFalse(self.bridge.handle({'command':'design.delete_layer','params':{'name':'Locked'}})['ok'])
        self.assertEqual(self.bridge.session.project.to_dict(), before)

    def test_delete_layer_rejects_locked_destination_and_machining_job(self):
        self.rectangle(layer='Cut')
        self.run_command('layer.set', name='0', locked=True)
        before = self.bridge.session.project.to_dict()
        self.assertFalse(self.bridge.handle({'command':'design.delete_layer','params':{'name':'Cut'}})['ok'])
        self.assertEqual(self.bridge.session.project.to_dict(), before)
        self.run_command('layer.set', name='0', locked=False)
        self.bridge.session.project.operations.append(RouterOffsetOperation(
            name='Test', tool_station=1, entity_ids=[], target_depth=0.1, safe_z=0.5))
        self.assertFalse(self.bridge.handle({'command':'design.delete_layer','params':{'name':'Cut'}})['ok'])

    def test_failures_are_atomic_and_stale_commands_rejected(self):
        self.run_command('geometry.add_circle',x=0,y=0,diameter=2)
        before=self.bridge.session.project.to_dict()
        for command,params in [('design.scale',dict(sx=2,sy=3)),
                               ('geometry.transform',dict(dx=float('nan'))),
                               ('gcode.export',dict(path=str(self.folder/'unsafe.tap')))]:
            result=self.bridge.handle({'command':command,'params':params})
            self.assertFalse(result['ok'])
            self.assertEqual(before,self.bridge.session.project.to_dict())
        self.assertFalse(self.bridge.handle({'command':'geometry.delete','revision':0})['ok'])
        self.assertFalse((self.folder/'unsafe.tap').exists())

    def test_copy_paste_group_arrange_and_undo(self):
        self.rectangle()
        self.rectangle(x=20,y=10)
        self.select_all()
        state=self.run_command('design.arrange',action='align_bottom')
        self.assertEqual([e['bounds'][1] for e in state['entities']],[0,0])
        state=self.run_command('design.group')
        group=state['entities'][0]['metadata']['group_id']
        self.assertEqual(group,state['entities'][1]['metadata']['group_id'])
        self.run_command('design.copy')
        state=self.run_command('design.paste',dx=1,dy=1)
        self.assertEqual(len(state['entities']),4)
        self.assertNotEqual(group,state['entities'][2]['metadata']['group_id'])
        self.assertEqual(state['entities'][2]['metadata']['group_id'],state['entities'][3]['metadata']['group_id'])
        state=self.run_command('history.undo')
        self.assertEqual(len(state['entities']),2)

    def test_shapes_boolean_merge_points_text_and_nesting(self):
        for kind in ['ellipse','slot','polygon','arrow','double_arrow','chevron','star']:
            self.run_command('design.shape',kind=kind,width=12,height=8,sides=5)
        self.run_command('project.new')
        self.rectangle()
        self.rectangle(x=5)
        self.select_all()
        state=self.run_command('design.boolean',operation='union')
        self.assertEqual(len(state['entities']),1)
        self.assertEqual(state['entities'][0]['bounds'],(0,0,15,5))
        self.run_command('design.explode')
        self.run_command('design.merge')
        self.run_command('design.points',points=[dict(x=0,y=0),dict(x=3,y=0),dict(x=3,y=2)])
        self.run_command('geometry.add_text',text='NORTH',x=20,y=10,height=2,font_family='Arial')
        state=self.run_command('geometry.convert_text')
        self.assertTrue(any(e['kind']=='polyline' for e in state['entities']))
        hidden=[e for e in state['entities'] if e['metadata'].get('annotation_hidden')]
        self.assertTrue(hidden)
        self.run_command('selection.clear')
        self.run_command('geometry.auto_nest',spacing=.25)
        self.run_command('geometry.add_cutoff_line',clearance_inches=.5)

    def test_preferences_and_persistent_protocol(self):
        prefs={'order':['Create','File'],'floating':{'Create':[120,80]},'dark':True}
        self.run_command('ui.save',**prefs)
        other=Bridge(self.bridge.preferences_path)
        self.assertEqual(other.handle({'command':'hello'})['preferences'],prefs)
        proc=subprocess.run([sys.executable,'-u',str(ROOT/'design_bridge.py')],
            input='{"id":1,"command":"hello"}\n{"id":2,"command":"geometry.add_circle","params":{"x":0,"y":0,"diameter":2},"revision":0}\n',
            text=True,capture_output=True,timeout=20)
        self.assertEqual(proc.returncode,0,proc.stderr)
        replies=[json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual([r['id'] for r in replies],[1,2])
        self.assertTrue(all(r['ok'] for r in replies))
        self.assertEqual(len(replies[1]['state']['entities']),1)

    def test_advanced_machining_jobs_are_view_only_and_preserved(self):
        self.rectangle()
        self.bridge.session.project.operations.append(RouterOffsetOperation(
            name='Existing profile',tab_count=4,tab_release_plan={"unported":True},tool_station=1,entity_ids=list(self.bridge.session.selected_ids),
            target_depth=.125,safe_z=.5))
        before=self.bridge.session.project.to_dict()
        self.assertTrue(self.bridge.snapshot()['readOnly'])
        for command,params in [('geometry.transform',{'dx':5}),('geometry.delete',{}),
                               ('project.save',{'path':str(self.folder/'changed.json')}),('history.undo',{})]:
            result=self.bridge.handle({'command':command,'params':params})
            self.assertFalse(result['ok'])
            self.assertEqual(before,self.bridge.session.project.to_dict())
        self.run_command('selection.clear')
        self.select_all()
        self.run_command('project.new')
        self.assertFalse(self.bridge.snapshot()['readOnly'])


if __name__=='__main__': unittest.main()

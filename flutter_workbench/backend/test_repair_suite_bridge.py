import copy
import unittest
from test_design_bridge import DesignBridgeTests
from cam_core.model import Point, Polyline, Line, RouterOffsetOperation
from cam_core.repair_suite import plan_repair_suite
from cam_core.contour_repair import apply_contour_repair


class RepairSuiteTests(unittest.TestCase):
    setUp = DesignBridgeTests.setUp
    run_command = DesignBridgeTests.run_command

    def seed(self, units='in', fragment=False):
        self.run_command('project.new', units=units)
        f = 25.4 if units == 'mm' else 1
        pts = [(0,0),(4,0),(4,3),(0,3),(0,.0008)] if fragment else [(0,0),(4,0),(4,0),(4,3),(0,3)]
        shape = Polyline(points=[Point(x*f,y*f) for x,y in pts], closed=not fragment)
        self.bridge.session.project.entities=[shape]
        if fragment:
            self.bridge.session.project.entities.append(Line(start=Point(0,.0008*f),end=Point(.0005*f,.0004*f)))
        self.bridge.snapshot()
        self.bridge.session.selected_ids={shape.id}
        self.bridge.session._record_history()
        return shape.id

    def preview(self, **p):
        before=self.bridge.snapshot()
        response=self.bridge.handle(dict(command='design.suite_preview',params=dict(scope='all',include_fragments=False)|p,revision=self.bridge.revision))
        self.assertTrue(response['ok'],response.get('error'))
        self.assertEqual(self.bridge.snapshot(),before)
        return response['preview']

    def rejected(self,command,**p):
        before=self.bridge.snapshot();index=self.bridge.session._history_index
        result=self.bridge.handle(dict(command=command,params=p,revision=self.bridge.revision))
        self.assertFalse(result['ok'],result)
        self.assertEqual(self.bridge.snapshot(),before)
        self.assertEqual(self.bridge.session._history_index,index)

    def test_safe_batch_exact_core_units_history_and_roundtrip(self):
        for units in ('in','mm'):
            identity=self.seed(units)
            original=copy.deepcopy(self.bridge.session.project)
            review=self.preview();self.assertEqual(review['count'],1)
            candidate,_,_=apply_contour_repair(original,plan_repair_suite(original,{identity}).proposal)
            index=self.bridge.session._history_index
            self.run_command('design.suite_apply',token=review['token'])
            self.assertEqual(self.bridge.session._history_index,index+1)
            self.assertEqual(self.bridge.session.project.to_dict(),candidate.to_dict())
            self.assertEqual(len(self.bridge.session.project.entities[0].points),4)
            repaired=self.bridge.session.project.to_dict()
            self.run_command('history.undo');self.assertEqual(self.bridge.session.project.to_dict(),original.to_dict())
            self.run_command('history.redo');self.assertEqual(self.bridge.session.project.to_dict(),repaired)
            path=self.folder/f'{units}.camjob.json'
            self.run_command('project.save',path=str(path));self.run_command('project.new');self.run_command('project.open',path=str(path))
            self.assertEqual(self.bridge.session.project.to_dict(),repaired)

    def test_fragment_opt_in_complete_scope_and_marker(self):
        for units in ('in','mm'):
            identity=self.seed(units,True)
            safe=self.preview();self.assertIsNone(safe['token'])
            self.assertTrue(any(r['kind']=='review' for r in safe['rows']))
            partial=self.preview(scope='selected',include_fragments=True)
            self.assertIsNone(partial['token'])
            reviewed=self.preview(include_fragments=True)
            self.assertEqual(reviewed['count'],1)
            self.assertTrue(any(r['point'] for r in reviewed['rows']))
            self.run_command('design.suite_apply',token=reviewed['token'])
            self.assertEqual(len(self.bridge.session.project.entities),1)
            self.assertTrue(self.bridge.session.project.entities[0].closed)
            self.assertEqual(self.bridge.session.project.entities[0].id,identity)

    def test_locked_hidden_protected_ambiguous_and_machining_guards(self):
        identity=self.seed()
        for settings in ({'locked':True},{'visible':False}):
            self.bridge.session.project.metadata['layer_settings']['0'].update(settings)
            review=self.preview();self.assertIsNone(review['token'])
            self.bridge.session.project.metadata['layer_settings']['0'].update(locked=False,visible=True)
        self.bridge.session.project.entities[0].metadata['mounting_generated']=True
        self.assertIsNone(self.preview()['token'])
        self.bridge.session.project.entities[0].metadata.clear()
        self.bridge.session.project.operations=[RouterOffsetOperation('Profile',1,[identity],.1,.2)]
        self.rejected('design.suite_preview',scope='all',include_fragments=False)

    def test_invalid_superseded_revision_and_digest_tokens(self):
        self.seed()
        for params in (dict(scope='bad'),dict(scope='all',include_fragments=1),dict(scope='all',tolerance=10)):
            self.rejected('design.suite_preview',**params)
        one=self.preview();two=self.preview()
        self.rejected('design.suite_apply',token=one['token'])
        self.run_command('selection.clear')
        self.rejected('design.suite_apply',token=two['token'])
        three=self.preview()
        self.bridge.session.project.entities[0].metadata['changed']=True
        self.rejected('design.suite_apply',token=three['token'])

    def test_hidden_generated_and_derived_changes_cannot_be_silently_applied(self):
        for key in ('annotation_hidden','tab_slot_generated'):
            self.seed();self.bridge.session.project.entities[0].metadata[key]=True
            self.assertIsNone(self.preview()['token'])
        for exact_copy in (False,True):
            identity=self.seed()
            dependent=copy.deepcopy(self.bridge.session.project.entities[0])
            dependent.id='derived'
            dependent.metadata.update(layer_prep_generated=True,layer_prep_source_entity_id=identity,
                layer_prep_role='cleanout_outline' if exact_copy else 'other')
            self.bridge.session.project.entities.append(dependent)
            self.rejected('design.suite_preview',scope='all',include_fragments=False)

    def small_scan(self, **p):
        r=self.bridge.handle(dict(command='design.small_scan',params=p,revision=self.bridge.revision))
        self.assertTrue(r['ok'],r.get('error'));return r['review']

    def test_explicit_delete_units_custom_threshold_history_and_dependencies(self):
        for units,f in [('in',1),('mm',25.4)]:
            self.run_command('project.new',units=units)
            identity=self.run_command('geometry.add_line',x1=0,y1=0,x2=.02*f,y2=0)['selected'][0]
            r=self.small_scan(maximum_size=.03*f)
            before=copy.deepcopy(self.bridge.session.project.to_dict());index=self.bridge.session._history_index
            self.rejected('design.small_delete',token=r['token'],entity_ids=[identity],confirmed=False)
            self.run_command('design.small_delete',token=r['token'],entity_ids=[identity],confirmed=True)
            self.assertFalse(self.bridge.session.project.entities);self.assertEqual(self.bridge.session._history_index,index+1)
            self.run_command('history.undo');self.assertEqual(self.bridge.session.project.to_dict(),before)
            self.run_command('history.redo');self.assertFalse(self.bridge.session.project.entities)
            self.run_command('history.undo')
            owner=Line(start=Point(2*f,0),end=Point(3*f,0));owner.metadata['owner_entity_id']=identity
            self.bridge.session.project.entities.append(owner);r=self.small_scan(maximum_size=.03*f)
            self.rejected('design.small_delete',token=r['token'],entity_ids=[identity],confirmed=True)

    def test_unlock_is_explicit_whole_layer_keeps_hidden_and_requires_rescan(self):
        identity=self.run_command('geometry.add_line',x1=0,y1=0,x2=.003,y2=0,layer='details')['selected'][0]
        self.run_command('layer.set',name='details',visible=False,locked=True)
        r=self.small_scan();before=copy.deepcopy(self.bridge.session.project.to_dict());index=self.bridge.session._history_index
        self.rejected('design.small_unlock',token=r['token'],entity_ids=[identity],confirmed=False)
        self.rejected('design.small_delete',token=r['token'],entity_ids=[identity],confirmed=True)
        self.run_command('design.small_unlock',token=r['token'],entity_ids=[identity],confirmed=True)
        layer=self.bridge.session.project.metadata['layer_settings']['details']
        self.assertFalse(layer['locked']);self.assertFalse(layer['visible'])
        self.assertEqual(self.bridge.session._history_index,index+1)
        self.rejected('design.small_delete',token=r['token'],entity_ids=[identity],confirmed=True)
        self.run_command('history.undo');self.assertEqual(self.bridge.session.project.to_dict(),before)
        self.run_command('history.redo');r=self.small_scan()
        self.run_command('design.small_delete',token=r['token'],entity_ids=[identity],confirmed=True)
        self.assertFalse(self.bridge.session.project.entities)

    def test_small_delete_attached_generated_stale_and_machining_refused(self):
        for mode in ('attached','generated','stale','machining'):
            self.run_command('project.new')
            identity=self.run_command('geometry.add_line',x1=0,y1=0,x2=.003,y2=0)['selected'][0]
            if mode=='attached': self.run_command('geometry.add_line',x1=0,y1=0,x2=2,y2=0)
            if mode=='generated': self.bridge.session.project.entities[0].metadata['mounting_generated']=True
            if mode=='machining': self.bridge.session.project.operations=[RouterOffsetOperation('Profile',1,[identity],.1,.2)]
            r=self.small_scan()
            if mode=='stale': self.run_command('selection.clear')
            self.rejected('design.small_delete',token=r['token'],entity_ids=[identity],confirmed=True)


if __name__=='__main__': unittest.main()

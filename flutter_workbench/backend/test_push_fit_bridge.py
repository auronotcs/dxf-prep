import copy
import unittest
from test_design_bridge import DesignBridgeTests
from cam_core.model import CamProject, Point, Polyline, Rectangle, RouterOffsetOperation


class PushFitBridgeTests(unittest.TestCase):
    setUp = DesignBridgeTests.setUp
    run_command = DesignBridgeTests.run_command

    def fixture(self):
        self.metal = Polyline(points=[Point(-.02,-.02),Point(5.02,-.02),Point(5.02,4.02),Point(3.98,4.02),
            Point(3.98,2.02),Point(1.02,2.02),Point(1.02,4.02),Point(-.02,4.02)],closed=True,layer='metal')
        self.acrylic = Polyline(points=[Point(0,0),Point(5,0),Point(5,4),Point(4,4),
            Point(4,2),Point(1,2),Point(1,4),Point(0,4)],closed=True,layer='acrylic')
        self.bridge.session.project.entities = [self.metal,self.acrylic]
        self.bridge.session.project = CamProject.from_dict(self.bridge.session.project.to_dict())
        self.metal,self.acrylic = self.bridge.session.project.entities
        self.bridge.snapshot()
        self.bridge.session._reset_history()
        self.bridge.history_labels = ['Fixture']
        self.params = dict(metal_ids=[self.metal.id],acrylic_ids=[self.acrylic.id],cutter_diameter=.25,fit_clearance=.005)

    def preview(self, command='design.push_fit_analyze', **params):
        result = self.bridge.handle(dict(command=command,params=params or self.params))
        self.assertTrue(result['ok'],result.get('error'))
        return result['preview']

    def test_review_apply_exact_preview_preserves_acrylic_save_reopen_undo(self):
        self.fixture()
        before = self.bridge.snapshot()
        original = copy.deepcopy(self.bridge.session.project.to_dict())
        preview = self.preview()
        self.assertEqual(self.bridge.snapshot(),before)
        self.assertEqual(len(preview['included']),2)
        first = preview['candidates'][0]['id']
        reviewed = self.preview('design.push_fit_review',token=preview['token'],included_ids=[first])
        self.assertEqual(self.bridge.snapshot(),before)
        stale = self.bridge.handle(dict(command='design.push_fit_apply',params=dict(token=preview['token'],review_token=preview['reviewToken'])))
        self.assertFalse(stale['ok'])
        state = self.run_command('design.push_fit_apply',token=reviewed['token'],review_token=reviewed['reviewToken'])
        self.assertEqual(state['history']['index'],1)
        self.assertEqual(state['history']['entries'][-1],'Push-through fit')
        actual = [e.to_dict() for e in self.bridge.session.project.entities if e.id in state['selected']]
        self.assertEqual(actual,[{k:v for k,v in e.items() if k!='bounds'} for e in reviewed['entities']])
        self.assertEqual(next(e for e in self.bridge.session.project.entities if e.id==self.acrylic.id).to_dict(),self.acrylic.to_dict())
        self.assertEqual(self.bridge.session.project.metadata['push_through_fit_history'][-1]['included_candidate_ids'],[first])
        applied = copy.deepcopy(self.bridge.session.project.to_dict())
        path = self.folder/'fit.camjob.json'
        self.run_command('project.save',path=str(path))
        self.run_command('history.undo')
        self.assertEqual(self.bridge.session.project.to_dict(),original)
        self.run_command('history.redo')
        self.assertEqual(self.bridge.session.project.to_dict(),applied)
        self.run_command('project.open',path=str(path))
        self.assertEqual(self.bridge.session.project.to_dict(),applied)

    def test_invalid_roles_and_settings_never_mutate(self):
        self.fixture()
        for patch in [dict(metal_ids=[]),dict(acrylic_ids=[self.metal.id]),dict(metal_ids=['missing']),
                      dict(cutter_diameter=0),dict(fit_clearance=-1),dict(fit_clearance=float('nan'))]:
            before = self.bridge.snapshot()
            result = self.bridge.handle(dict(command='design.push_fit_analyze',params=dict(self.params,**patch)))
            self.assertFalse(result['ok'],patch)
            self.assertEqual(self.bridge.snapshot(),before)
        self.bridge.session.project.entities[0].metadata['reference_only']=True
        self.assertFalse(self.bridge.handle(dict(command='design.push_fit_analyze',params=self.params))['ok'])

    def test_empty_review_invalid_candidate_stale_and_locked_guards(self):
        self.fixture()
        p = self.preview()
        p = self.preview('design.push_fit_review',token=p['token'],included_ids=[])
        before = self.bridge.snapshot()
        self.assertFalse(self.bridge.handle(dict(command='design.push_fit_apply',params=dict(token=p['token'],review_token=p['reviewToken'])))['ok'])
        self.assertEqual(self.bridge.snapshot(),before)
        self.assertFalse(self.bridge.handle(dict(command='design.push_fit_review',params=dict(token=p['token'],included_ids=['invented'])))['ok'])
        self.assertFalse(self.bridge.handle(dict(command='design.push_fit_apply',params=dict(token=p['token'])))['ok'])
        p = self.preview()
        self.run_command('layer.set',name='metal',locked=True)
        before = self.bridge.snapshot()
        self.assertFalse(self.bridge.handle(dict(command='design.push_fit_apply',params=dict(token=p['token'],review_token=p['reviewToken'])))['ok'])
        self.assertFalse(self.bridge.handle(dict(command='design.push_fit_analyze',params=self.params))['ok'])
        self.assertEqual(self.bridge.snapshot(),before)

    def test_protected_dependents_and_machining_jobs_blocked(self):
        self.fixture()
        self.acrylic.metadata['owner_entity_id'] = self.metal.id
        before = self.bridge.snapshot()
        self.assertFalse(self.bridge.handle(dict(command='design.push_fit_analyze',params=self.params))['ok'])
        self.assertEqual(self.bridge.snapshot(),before)
        self.bridge.session.project.entities[1].metadata.clear()
        self.bridge.session.project.operations.append(RouterOffsetOperation(name='test',tool_station=1,entity_ids=[self.metal.id],target_depth=.1,safe_z=.25))
        before = self.bridge.snapshot()
        self.assertFalse(self.bridge.handle(dict(command='design.push_fit_analyze',params=self.params))['ok'])
        self.assertEqual(self.bridge.snapshot(),before)

    def test_metric_preview_and_broad_mismatch_defaults(self):
        self.fixture()
        self.bridge.session.project.units='mm'
        for e in self.bridge.session.project.entities:
            e.points=[Point(q.x*25.4,q.y*25.4) for q in e.points]
        self.params.update(cutter_diameter=6.35,fit_clearance=.127)
        p=self.preview()
        self.assertEqual(len(p['included']),2)
        self.assertTrue(all(c['area']>.01 for c in p['candidates']))
        self.fixture()
        self.bridge.session.project.entities=[
            Rectangle(id=self.metal.id,corner_a=Point(0.,0.),corner_b=Point(1.,1.)),
            Rectangle(id=self.acrylic.id,corner_a=Point(0.,0.),corner_b=Point(5.,4.))]
        p=self.preview()
        self.assertTrue(p['warnings'])
        self.assertEqual(p['included'],[])
        self.assertTrue(any(not c['recommended'] for c in p['candidates']))


if __name__ == '__main__': unittest.main()

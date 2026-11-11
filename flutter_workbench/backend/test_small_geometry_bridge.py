import copy
import unittest
from test_design_bridge import DesignBridgeTests
from cam_core.model import RouterOffsetOperation


class SmallGeometryBridgeTests(unittest.TestCase):
    setUp = DesignBridgeTests.setUp
    run_command = DesignBridgeTests.run_command

    def scan(self, **params):
        before=self.bridge.snapshot()
        response=self.bridge.handle(dict(command='design.small_scan',params=params,revision=self.bridge.revision))
        self.assertTrue(response['ok'],response.get('error'))
        self.assertEqual(self.bridge.snapshot(),before)
        return response['review']

    def line(self,x,y,dx,dy,**extra):
        return self.run_command('geometry.add_line',x1=x,y1=y,x2=x+dx,y2=y+dy,**extra)['selected'][0]

    def test_whole_objects_units_attachments_protection_and_uncertain_joins(self):
        for units,factor in [('in',1),('mm',25.4)]:
            self.run_command('project.new',units=units)
            tiny=self.line(0,0,.005*factor,0)
            attached=self.line(factor,factor,.005*factor,0)
            self.line(factor,factor,0,2*factor)
            generated=self.line(3*factor,0,.002*factor,0)
            self.bridge.session.project.entities[-1].metadata['mounting_generated']=True
            locked=self.line(4*factor,0,.002*factor,0,layer='locked')
            self.run_command('layer.set',name='locked',locked=True,visible=False)
            narrow=self.line(5*factor,0,0,10*factor)
            joined=self.line(6*factor,0,0,10*factor)
            self.bridge.session.project.entities[-1].metadata['dxf_join_review']=[{'reason':'Uncertain branch at imported endpoint.'}]
            review=self.scan()
            self.assertAlmostEqual(review['maximumSize'],.01*factor)
            rows={r['id']:r for r in review['findings']}
            self.assertEqual(set(rows),{tiny,attached,generated,locked,joined})
            self.assertTrue(rows[tiny]['isolated'])
            for identity in (attached,generated,locked,joined): self.assertFalse(rows[identity]['isolated'])
            self.assertTrue(rows[locked]['hidden']); self.assertTrue(rows[locked]['locked'])
            self.assertIn('Shares an endpoint',rows[attached]['reason'])
            self.assertIn('owning workflow',rows[generated]['recommendation'])
            self.assertNotIn(narrow,rows)
            self.assertEqual([r['id'] for r in self.scan(isolated_only=True)['findings']],[tiny])
            self.assertEqual(len(self.scan(maximum_size=.001*factor)['findings']),1)

    def test_import_scope_checks_shared_endpoint_against_whole_drawing(self):
        self.line(0,0,0,10)
        imported=self.line(0,0,.005,0)
        other=self.line(2,0,.004,0)
        findings=self.scan(entity_ids=[imported])['findings']
        self.assertEqual(len(findings),1)
        self.assertFalse(findings[0]['isolated'])
        self.assertEqual(self.scan(entity_ids=[])['findings'],[])
        self.assertEqual(len(self.scan()['findings']),2)

    def test_keep_is_undoable_roundtrips_and_geometry_change_requires_review_again(self):
        identity=self.line(0,0,.005,0)
        review=self.scan()
        before=copy.deepcopy(self.bridge.session.project.to_dict())
        selection=set(self.bridge.session.selected_ids)
        history=self.bridge.session._history_index
        self.run_command('design.small_keep',token=review['token'],entity_ids=[identity])
        self.assertEqual(self.bridge.session._history_index,history+1)
        self.assertEqual(self.bridge.session.selected_ids,selection)
        self.assertEqual(self.bridge.session.project.to_dict()['entities'],before['entities'])
        self.assertEqual(self.scan()['findings'],[])
        self.assertTrue(self.scan(include_kept=True)['findings'][0]['kept'])
        self.run_command('history.undo')
        self.assertEqual(self.bridge.session.project.to_dict(),before)
        self.assertEqual(len(self.scan()['findings']),1)
        self.run_command('history.redo')
        path=self.folder/'kept.camjob.json'
        self.run_command('project.save',path=str(path))
        self.run_command('project.new')
        self.run_command('project.open',path=str(path))
        self.assertEqual(self.scan()['findings'],[])
        self.run_command('selection.set',entity_ids=[identity])
        self.run_command('geometry.transform',dx=1,dy=0)
        self.assertEqual([r['id'] for r in self.scan()['findings']],[identity])

    def test_invalid_stale_and_untracked_drift_do_not_write_review_marks(self):
        identity=self.line(0,0,.005,0)
        for params in [dict(maximum_size=0),dict(maximum_size=float('nan')),dict(include_kept='true'),dict(entity_ids=['missing'])]:
            before=self.bridge.snapshot()
            self.assertFalse(self.bridge.handle(dict(command='design.small_scan',params=params))['ok'])
            self.assertEqual(self.bridge.snapshot(),before)
        review=self.scan()
        before=self.bridge.snapshot()
        for ids in ([],['missing'],[identity,'missing']):
            self.assertFalse(self.bridge.handle(dict(command='design.small_keep',params=dict(token=review['token'],entity_ids=ids)))['ok'])
            self.assertEqual(self.bridge.snapshot(),before)
        self.run_command('layer.set',name='0',locked=True)
        before=self.bridge.snapshot()
        self.assertFalse(self.bridge.handle(dict(command='design.small_keep',params=dict(token=review['token'],entity_ids=[identity])))['ok'])
        self.assertEqual(self.bridge.snapshot(),before)
        review=self.scan()
        self.bridge.session.project.entities[0].metadata['untracked']='change'
        before=self.bridge.snapshot()
        self.assertFalse(self.bridge.handle(dict(command='design.small_keep',params=dict(token=review['token'],entity_ids=[identity])))['ok'])
        self.assertEqual(self.bridge.snapshot(),before)

    def test_machining_job_can_inspect_dependencies_but_cannot_keep(self):
        identity=self.line(0,0,.005,0)
        self.bridge.session.project.operations=[RouterOffsetOperation('Cut tiny detail',1,[identity],.1,.2)]
        review=self.scan()
        self.assertEqual(review['findings'][0]['affectedOperations'],['Cut tiny detail'])
        before=self.bridge.snapshot()
        result=self.bridge.handle(dict(command='design.small_keep',params=dict(token=review['token'],entity_ids=[identity])))
        self.assertFalse(result['ok'])
        self.assertEqual(self.bridge.snapshot(),before)


if __name__=='__main__': unittest.main()

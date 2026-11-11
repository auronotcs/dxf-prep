import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from design_bridge import Bridge
from cam_core.toolpath_background import generate_toolpath_preview_bundle
from cam_core.output_workflow import prepare_plate_outputs, execute_plate_outputs, repository_output_paths
from cam_core.automation import _project_plates
from manufacturing_support import supported

def slow_test_worker(project, request_id, queue, event):
    queue.put(('progress',request_id,'Non-cooperative test calculation'))
    time.sleep(30)

class ManufacturingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.b = Bridge(self.root/'prefs.json', recovery_directory=self.root/'recovery')
    def tearDown(self):
        self.b.manufacturing.close()
        if self.b.recovery: self.b.recovery.close()
        self.tmp.cleanup()
    def run_command(self,command,**params):
        r=self.b.handle(dict(command=command,params=params,revision=self.b.revision))
        self.assertTrue(r['ok'],r.get('error'))
        json.dumps(r,allow_nan=False)
        return r
    def fixture(self,units='in'):
        f=25.4 if units=='mm' else 1
        self.run_command('project.new',units=units)
        self.run_command('manufacturing.setup',material='Plywood',material_thickness=.5*f,safe_z=.25*f)
        self.run_command('manufacturing.tool_save',station=1,name='Quarter inch',diameter=.25*f,
            spindle_rpm=18000,feed_rate=2*f,plunge_rate=.5*f,max_stepdown=.125*f,cutting_edge_length=.75*f)
        self.run_command('geometry.add_rectangle',x=2*f,y=2*f,width=6*f,height=4*f)
        ids=list(self.b.session.selected_ids)
        self.params=dict(name='Basic profile',tool_station=1,entity_ids=ids,target_depth=.5*f,
            safe_z=.25*f,stepdown=.125*f,side='outside',cut_direction='climb')
        self.run_command('manufacturing.profile_save',**self.params)
    def generate(self, expected='ready'):
        r=self.run_command('manufacturing.generate')['manufacturing']
        self.assertEqual(r['status'],'running',r)
        deadline=time.monotonic()+20
        while r['status']=='running' and time.monotonic()<deadline:
            time.sleep(.03)
            r=self.run_command('manufacturing.poll')['manufacturing']
        self.assertEqual(r['status'],expected,r)
        return r
    def test_edit_history_recovery_and_roundtrip(self):
        self.fixture()
        before=copy.deepcopy(self.b.session.project.to_dict())
        op=self.b.session.project.operations[0]
        self.run_command('manufacturing.profile_save',**dict(self.params,id=op.id,target_depth=.375))
        self.assertEqual(self.b.session.project.operations[0].id,op.id)
        self.run_command('history.undo')
        self.assertEqual(self.b.session.project.to_dict(),before)
        self.run_command('history.redo')
        self.assertEqual(self.b.session.project.operations[0].target_depth,.375)
        store=self.b.recovery
        recovered=store.read(store.identity)[1]
        self.assertEqual(recovered.to_dict(),self.b.session.project.to_dict())
        path=self.root/'job.camjob.json'
        self.run_command('project.save',path=str(path))
        saved=copy.deepcopy(self.b.session.project.to_dict())
        self.run_command('project.open',path=str(path))
        self.assertEqual(self.b.session.project.to_dict(),saved)
        self.assertTrue(self.b.snapshot()['manufacturing']['editable'])
        self.assertFalse(self.b.handle(dict(command='geometry.delete',revision=self.b.revision))['ok'])
    def test_invalid_edits_are_atomic_and_advanced_jobs_protected(self):
        self.fixture()
        before=copy.deepcopy(self.b.session.project.to_dict())
        index=self.b.session._history_index
        for command,p in [('profile_save',dict(self.params,target_depth=-1)),('tool_delete',{'station':1}),
            ('profile_save',dict(self.params,tool_station=12)),('profile_save',dict(self.params,entity_ids=['missing'])),
            ('tool_save',dict(station=1,name='bad',diameter=float('nan')))]:
            r=self.b.handle(dict(command='manufacturing.'+command,params=p,revision=self.b.revision))
            self.assertFalse(r['ok'],r)
            self.assertEqual(self.b.session.project.to_dict(),before)
            self.assertEqual(self.b.session._history_index,index)
        # Automatic placement is supported; the separate release workflow is protected.
        self.b.session.project.operations[0].tab_release_plan={"unported":True}
        self.assertFalse(supported(self.b.session.project))
        self.assertFalse(self.b.handle(dict(command='manufacturing.setup',params={'material':'x'},revision=self.b.revision))['ok'])
    def test_real_worker_preview_export_matches_direct_core_inches_and_mm(self):
        for units in ('in','mm'):
            with self.subTest(units=units):
                self.fixture(units)
                original=copy.deepcopy(self.b.session.project.to_dict())
                preview=self.generate()
                self.assertGreater(preview['motionCount'],0)
                self.assertEqual(self.b.session.project.to_dict(),original)
                prepared=self.b.manufacturing.prepared
                direct,errors=generate_toolpath_preview_bundle(prepared[0].result.project)
                self.assertFalse(errors)
                self.assertEqual(direct,self.b.manufacturing.bundle)
                out=self.run_command('manufacturing.export',token=preview['token'],reviewed=True,name='automated-'+units)['manufacturing']
                cnc=next(Path(p) for p in out['paths'] if p.endswith('.cnc'))
                reference=self.root/units
                reference.mkdir()
                plates=_project_plates(self.b.session.project)
                expected=prepare_plate_outputs(self.b.session.project,plates,repository_output_paths(reference,'reference',[1]))
                exported=execute_plate_outputs(expected,'CNC + CAM project',generated_paths_by_plate={1:direct})
                # Program title/path differs; all machine instructions must agree.
                actual_lines=[x for x in cnc.read_text().splitlines() if x and not x.startswith('(')]
                expected_lines=[x for x in exported.controller_paths[0].read_text().splitlines() if x and not x.startswith('(')]
                self.assertEqual(actual_lines,expected_lines)
                self.assertEqual(self.b.session.project.to_dict(),original)
    def test_cancel_stale_review_and_path_guards(self):
        self.fixture()
        self.run_command('manufacturing.generate')
        self.run_command('manufacturing.cancel')
        self.assertIsNone(self.b.manufacturing.process)
        preview=self.generate()
        for p in [dict(token=preview['token'],reviewed=False,name='test'),dict(token=preview['token'],reviewed=True,name='../escape')]:
            self.assertFalse(self.b.handle(dict(command='manufacturing.export',params=p,revision=self.b.revision))['ok'])
        self.run_command('manufacturing.setup',material='Plywood',material_thickness=.6,safe_z=.25)
        self.assertFalse(self.b.handle(dict(command='manufacturing.export',params=dict(token=preview['token'],reviewed=True,name='stale'),revision=self.b.revision))['ok'])
    def test_delete_toggle_and_single_plate_guard(self):
        self.fixture()
        op=self.b.session.project.operations[0]
        self.run_command('manufacturing.operation_toggle',id=op.id)
        self.assertFalse(self.b.session.project.operations[0].enabled)
        self.assertFalse(self.b.handle(dict(command='manufacturing.generate',revision=self.b.revision))['ok'])
        self.run_command('history.undo')
        self.run_command('manufacturing.operation_delete',id=op.id)
        self.assertFalse(self.b.session.project.operations)
        self.run_command('manufacturing.tool_delete',station=1)
        self.assertFalse(self.b.session.project.tool_library.tools)

    def test_stock_resize_is_one_transaction_and_bad_draft_rolls_back(self):
        self.fixture()
        original=copy.deepcopy(self.b.session.project.to_dict())
        index=self.b.session._history_index
        self.run_command('manufacturing.setup',material='Oak',material_thickness=.6,safe_z=.3,width=48,height=24)
        self.assertEqual(self.b.session._history_index,index+1)
        self.assertEqual(_project_plates(self.b.session.project)[0].width,48)
        self.run_command('history.undo')
        self.assertEqual(self.b.session.project.to_dict(),original)

        r=self.b.handle(dict(command='manufacturing.setup',params=dict(material='Changed',width=-1),revision=self.b.revision))
        self.assertFalse(r['ok'])
        self.assertEqual(self.b.session.project.to_dict(),original)

    def test_geometry_only_recovery_keeps_unported_manufacturing_preferences(self):
        self.run_command('geometry.add_rectangle',x=1,y=1,width=2,height=2)
        self.b.session.project.metadata['cycle_optimization']={'enabled':True}
        self.b.sync_recovery(force=True)
        store=self.b.recovery
        recovered=store.read(store.identity)[1]
        self.assertEqual(recovered.metadata['cycle_optimization'],{'enabled':True})
        self.assertTrue(self.b.snapshot()['manufacturing']['editable'])

    def test_preflight_blocks_missing_tool_and_export_failure_preserves_document(self):
        self.fixture()
        tools=self.b.session.project.tool_library.tools
        self.b.session.project.tool_library.tools=[]
        result=self.generate(expected='blocked')
        self.assertTrue(result['validation']['blocking'])
        self.assertIsNone(self.b.manufacturing.bundle)
        self.b.session.project.tool_library.tools=tools
        result=self.generate()
        before=copy.deepcopy(self.b.session.project.to_dict())
        with patch('cam_core.output_workflow.post_enroute_cnc',side_effect=OSError('simulated disk failure')):
            r=self.b.handle(dict(command='manufacturing.export',params=dict(token=result['token'],reviewed=True,name='failed-publication'),revision=self.b.revision))
        self.assertFalse(r['ok'])
        self.assertEqual(self.b.session.project.to_dict(),before)
        self.assertEqual(self.b.manufacturing.result['status'],'ready')

    def test_multiple_plates_crossing_geometry_and_worker_start_failure(self):
        self.fixture()
        original=copy.deepcopy(self.b.session.project)
        self.b.session.project.metadata['plates']=[dict(number=1,x=0,y=0,width=96,height=48),dict(number=2,x=96,y=0,width=96,height=48)]
        self.assertFalse(self.b.handle(dict(command='manufacturing.generate',revision=self.b.revision))['ok'])
        self.b.session.project=copy.deepcopy(original)
        self.b.session.project.entities[0].corner_b=type(self.b.session.project.entities[0].corner_b)(100,4)
        self.assertFalse(self.b.handle(dict(command='manufacturing.generate',revision=self.b.revision))['ok'])
        self.b.session.project=original
        with patch('multiprocessing.process.BaseProcess.start',side_effect=OSError('worker unavailable')):
            r=self.b.handle(dict(command='manufacturing.generate',revision=self.b.revision))
        self.assertFalse(r['ok'])
        self.assertIsNone(self.b.manufacturing.process)
        self.generate()

    def test_cancel_stops_a_calculation_that_does_not_cooperate(self):
        self.fixture()
        with patch('manufacturing_support.generate_worker',slow_test_worker):
            self.run_command('manufacturing.generate')
        deadline=time.monotonic()+10
        result={}
        while not result.get('progress') and time.monotonic()<deadline:
            time.sleep(.03)
            result=self.run_command('manufacturing.poll')['manufacturing']
        self.assertTrue(result.get('progress'))
        started=time.monotonic()
        self.run_command('manufacturing.cancel')
        self.assertLess(time.monotonic()-started,2)
        self.assertIsNone(self.b.manufacturing.process)

    def test_mid_publication_failure_rolls_back_partial_package(self):
        self.fixture()
        preview=self.generate()
        original=copy.deepcopy(self.b.session.project.to_dict())
        output_root=Path(__file__).resolve().parents[1]/'build/manufacturing-output'
        prior=set(output_root.iterdir())
        calls=[]
        import os
        def fail_second(source,target):
            calls.append(str(target))
            if len(calls)==2: raise OSError('simulated publication failure')
            os.replace(source,target)
        def publish(*args,**kwargs):
            return execute_plate_outputs(*args,**kwargs,publish_replace=fail_second)
        with patch('manufacturing_support.execute_plate_outputs',side_effect=publish):
            r=self.b.handle(dict(command='manufacturing.export',params=dict(token=preview['token'],reviewed=True,name='atomic-failure'),revision=self.b.revision))
        self.assertFalse(r['ok'])
        self.assertGreaterEqual(len(calls),2)
        created=set(output_root.iterdir())-prior
        self.assertEqual(len(created),1)
        folder=created.pop()
        self.assertFalse(list(folder.glob('*.cnc')))
        self.assertFalse(list(folder.glob('*.camjob.json')))
        self.assertEqual(self.b.session.project.to_dict(),original)

if __name__=='__main__': unittest.main()

import copy
from pathlib import Path
import time
from test_manufacturing_bridge import ManufacturingTests as Base
from manufacturing_support import supported
from cam_core.model import Circle, Point, Polyline
from cam_core.toolpath_background import generate_toolpath_preview_bundle
from cam_core.output_workflow import prepare_plate_outputs, repository_output_paths, execute_plate_outputs
from cam_core.automation import _project_plates

class ExpandedManufacturingTests(Base):
    # This test module deliberately uses only newly defined tests; shared fixture helpers below.
    def draft(self, opid, settings, source=None):
        r=self.run_command('manufacturing.group_prepare',id=opid,settings=settings,source=source or {})['manufacturing']
        deadline=time.monotonic()+30
        while r['status']=='running' and time.monotonic()<deadline:
            time.sleep(.03)
            r=self.run_command('manufacturing.poll')['manufacturing']
        self.assertEqual(r['status'],'group_ready',r)
        return r
    def settings(self, f=1):
        return dict(station=2,feed_rate=f,plunge_rate=.25*f,spindle_rpm=18000,
            stepdown=.04*f,safe_z=.25*f,skin=.025*f,ignore_stock_less_than=.001*f)
    def group_fixture(self, units='in'):
        self.fixture(units)
        f=25.4 if units=='mm' else 1
        old=self.b.session.project.entities[0]
        self.b.session.project.entities=[Polyline(points=[Point(x*f,y*f) for x,y in [(2,2),(7,2),(7,7),(5,7),(5,5),(2,5)]],closed=True,id=old.id)]
        self.run_command('manufacturing.profile_save',**dict(self.params,id=self.b.session.project.operations[0].id,side='inside'))
        self.run_command('manufacturing.tool_save',station=2,name='Small',diameter=.125*f,
            spindle_rpm=18000,feed_rate=f,plunge_rate=.25*f,max_stepdown=.04*f,cutting_edge_length=.75*f)
    def test_group_draft_atomic_review_edit_recovery_and_motion(self):
        for units in ('in','mm'):
            self.group_fixture(units)
            f=25.4 if units=='mm' else 1
            before=copy.deepcopy(self.b.session.project.to_dict())
            index=self.b.session._history_index
            draft=self.draft(self.b.session.project.operations[0].id,self.settings(f))
            self.assertEqual(before,self.b.session.project.to_dict())
            self.assertGreater(len(draft['stages']),1)
            self.assertFalse(self.b.handle(dict(command='manufacturing.group_apply',params=dict(token=draft['token'],reviewed=False),revision=self.b.revision))['ok'])
            self.run_command('manufacturing.group_apply',token=draft['token'],reviewed=True)
            self.assertEqual(self.b.session._history_index,index+1)
            self.assertTrue(supported(self.b.session.project))
            self.run_command('history.undo');self.assertEqual(before,self.b.session.project.to_dict())
            self.run_command('history.redo')
            ids=[o.id for o in self.b.session.project.operations]
            draft=self.draft(ids[-1],dict(self.settings(f),skin=.03*f),dict(name='Revised group',stepdown=.1*f))
            self.run_command('manufacturing.group_apply',token=draft['token'],reviewed=True)
            self.assertEqual(ids,[o.id for o in self.b.session.project.operations])
            self.assertEqual(self.b.session.project.operations[0].profile_retention['edit_inputs']['source']['name'],'Revised group')
            self.assertTrue(all(o.target_depth <= .47*f+1e-9 for o in self.b.session.project.operations))
            self.run_command('manufacturing.operation_toggle',id=ids[-1])
            self.assertTrue(all(not o.enabled for o in self.b.session.project.operations))
            self.run_command('manufacturing.operation_toggle',id=ids[0])
            path=self.root/('skin-'+units+'.camjob.json')
            self.run_command('project.save',path=str(path));self.run_command('project.open',path=str(path))
            self.assertTrue(supported(self.b.session.project))
            preview=self.generate()
            direct,errors=generate_toolpath_preview_bundle(self.b.manufacturing.prepared[0].result.project)
            self.assertFalse(errors);self.assertEqual(direct,self.b.manufacturing.bundle)
            out=self.run_command('manufacturing.export',token=preview['token'],reviewed=True,name='skin-'+units)['manufacturing']
            saved=next(p for p in out['paths'] if p.endswith('.camjob.json'))
            self.run_command('project.open',path=saved)
            self.assertTrue(supported(self.b.session.project))
            self.run_command('manufacturing.operation_delete',id=self.b.session.project.operations[0].id)
            self.assertFalse(self.b.session.project.operations)
    def test_group_tampering_stale_draft_and_stock_drift_block_output(self):
        self.group_fixture()
        draft=self.draft(self.b.session.project.operations[0].id,self.settings())
        self.run_command('manufacturing.setup',material='Wood')
        self.assertFalse(self.b.handle(dict(command='manufacturing.group_apply',params=dict(token=draft['token'],reviewed=True),revision=self.b.revision))['ok'])
        draft=self.draft(self.b.session.project.operations[0].id,self.settings())
        self.run_command('manufacturing.group_apply',token=draft['token'],reviewed=True)
        before=copy.deepcopy(self.b.session.project)
        self.b.session.project.operations[0].tab_count=4
        self.assertFalse(supported(self.b.session.project))
        self.b.session.project=before
        self.run_command('manufacturing.setup',material_thickness=.6)
        self.generate('blocked')
    def test_drill_and_island_cleanout_units_history_and_core_output(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            self.run_command('manufacturing.operation_delete',id=self.b.session.project.operations[0].id)
            boundary=self.b.session.project.entities[0].id
            self.run_command('geometry.add_circle',x=4*f,y=4*f,diameter=1*f)
            island=next(iter(self.b.session.selected_ids))
            self.run_command('manufacturing.fill_save',name='Pocket',entity_ids=[boundary],island_entity_ids=[island],
                tool_station=1,target_depth=.1*f,safe_z=.25*f,stepdown=.05*f,stepover=.1*f,cut_direction='climb')
            self.assertTrue(supported(self.b.session.project))
            first=self.b.session.project.operations[0].id
            self.run_command('manufacturing.tool_save',station=2,name='Drill',kind='drill',diameter=.125*f,
                spindle_rpm=18000,feed_rate=f,plunge_rate=.25*f,max_stepdown=.05*f,cutting_edge_length=.75*f)
            self.run_command('manufacturing.drill_save',name='Drill',entity_ids=[island],tool_station=2,
                target_depth=.2*f,safe_z=.25*f,peck_depth=.05*f,peck_mode='full_clear',retract_height=.1*f,reentry_clearance=.02*f,dwell_seconds=0)
            self.assertTrue(supported(self.b.session.project))
            before=copy.deepcopy(self.b.session.project.to_dict())
            self.run_command('history.undo');self.assertEqual(len(self.b.session.project.operations),1)
            self.run_command('history.redo');self.assertEqual(before,self.b.session.project.to_dict())
            preview=self.generate()
            direct,errors=generate_toolpath_preview_bundle(self.b.manufacturing.prepared[0].result.project)
            self.assertFalse(errors);self.assertEqual(direct,self.b.manufacturing.bundle)
            out=self.run_command('manufacturing.export',token=preview['token'],reviewed=True,name='drill-fill-'+units)['manufacturing']
            self.assertTrue(all(Path(x).exists() for x in out['paths']))
            self.assertEqual(first,self.b.session.project.operations[0].id)
            # Corner coverage is now explicitly exposed and recipe-reviewed.
            self.b.session.project.operations[0].corner_tags=True
            self.assertTrue(supported(self.b.session.project))
            # A genuinely unsupported machining policy still protects the job.
            self.b.session.project.operations[0].small_part_strategy='unreviewed'
            self.assertFalse(supported(self.b.session.project))
    def test_selected_and_all_plate_output_local_motion_and_review_binding(self):
        self.fixture()
        project=self.b.session.project
        project.metadata['plates']=[dict(number=1,x=0,y=0,width=96,height=48),dict(number=2,x=100,y=0,width=96,height=48)]
        other=copy.deepcopy(project.entities[0]);other.id='entity_plate2'
        other.corner_a=Point(other.corner_a.x+100,other.corner_a.y);other.corner_b=Point(other.corner_b.x+100,other.corner_b.y)
        project.entities.append(other)
        self.run_command('manufacturing.profile_save',**dict(self.params,name='Plate 2',entity_ids=[other.id]))
        for numbers in ([2],[1,2]):
            r=self.run_command('manufacturing.generate',plates=numbers)['manufacturing']
            deadline=time.monotonic()+20
            while r['status']=='running' and time.monotonic()<deadline:
                time.sleep(.03);r=self.run_command('manufacturing.poll')['manufacturing']
            self.assertEqual(r['status'],'ready',r)
            self.assertEqual(r['plates'],numbers)
            self.assertEqual(set(self.b.manufacturing.bundles),set(numbers))
            for item in self.b.manufacturing.prepared:
                direct,errors=generate_toolpath_preview_bundle(item.result.project)
                self.assertFalse(errors);self.assertEqual(direct,self.b.manufacturing.bundles[item.plate.number])
            out=self.run_command('manufacturing.export',token=r['token'],reviewed=True,name='plates')['manufacturing']
            self.assertEqual(len([p for p in out['paths'] if p.endswith('.cnc')]),len(numbers))
            self.assertTrue(all(Path(p).exists() for p in out['paths']))
            reference=self.root/('plates-'+str(len(numbers)));reference.mkdir()
            selected=[p for p in _project_plates(self.b.session.project) if p.number in numbers]
            prepared=prepare_plate_outputs(self.b.session.project,selected,repository_output_paths(reference,'reference',numbers))
            direct={item.plate.number:generate_toolpath_preview_bundle(item.result.project)[0] for item in prepared}
            reference_out=execute_plate_outputs(prepared,'CNC + CAM project',generated_paths_by_plate=direct)
            actual=[Path(p) for p in out['paths'] if p.endswith('.cnc')]
            instructions=lambda p:[line for line in p.read_text().splitlines() if line and not line.startswith('(')]
            self.assertEqual([instructions(p) for p in actual],[instructions(p) for p in reference_out.controller_paths])

        stale=r['token'];self.run_command('manufacturing.generate',plates=[1])
        self.assertFalse(self.b.handle(dict(command='manufacturing.export',params=dict(token=stale,reviewed=True,name='stale-plates'),revision=self.b.revision))['ok'])
        self.run_command('manufacturing.cancel')

    def test_partial_unassigned_contours_and_failed_group_leave_document_intact(self):
        self.fixture()
        other=copy.deepcopy(self.b.session.project.entities[0]);other.id='entity_outside'
        other.corner_a=Point(200,2);other.corner_b=Point(204,4)
        self.b.session.project.entities.append(other)
        self.b.session.project.operations[0].entity_ids.append(other.id)
        before=copy.deepcopy(self.b.session.project.to_dict())
        r=self.b.handle(dict(command='manufacturing.generate',revision=self.b.revision))
        self.assertFalse(r['ok'],r)
        self.assertEqual(before,self.b.session.project.to_dict())
        self.group_fixture()
        before=copy.deepcopy(self.b.session.project.to_dict())
        r=self.run_command('manufacturing.group_prepare',id=self.b.session.project.operations[0].id,settings=dict(self.settings(),skin=1))['manufacturing']
        deadline=time.monotonic()+10
        while r['status']=='running' and time.monotonic()<deadline:
            time.sleep(.03);r=self.run_command('manufacturing.poll')['manufacturing']
        self.assertEqual(r['status'],'failed',r)
        self.assertEqual(before,self.b.session.project.to_dict())
        self.assertIsNone(self.b.manufacturing.process)
        self.assertFalse(self.b.handle(dict(command='manufacturing.group_apply',params=dict(token=r.get('token'),reviewed=True),revision=self.b.revision))['ok'])

# Avoid inheriting the baseline's test methods a second time.
for name in list(Base.__dict__):
    if name.startswith('test_'): setattr(ExpandedManufacturingTests,name,None)

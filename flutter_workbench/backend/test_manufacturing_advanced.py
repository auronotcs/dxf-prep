import copy
import math
from pathlib import Path
import time
from test_manufacturing_bridge import ManufacturingTests as Base
from manufacturing_support import supported, stock_verification, mutate, preflight_bundle
from cam_core.model import Circle, Point, Polyline
from cam_core.toolpath_background import generate_toolpath_preview_bundle
from cam_core.output_workflow import prepare_plate_outputs, repository_output_paths, execute_plate_outputs
from cam_core.automation import _project_plates

class AdvancedManufacturingTests(Base):
    def prepared(self, command, params, status):
        before=copy.deepcopy(self.b.session.project.to_dict())
        response=self.b.handle(dict(command='manufacturing.'+command,params=params,revision=self.b.revision))
        self.assertTrue(response['ok'],response.get('error'))
        r=response['manufacturing']
        deadline=time.monotonic()+45
        while r['status']=='running' and time.monotonic()<deadline:
            time.sleep(.03);r=self.run_command('manufacturing.poll')['manufacturing']
        self.assertEqual(r['status'],status,r)
        self.assertEqual(before,self.b.session.project.to_dict())
        return r
    def apply(self, draft, group=False):
        command='manufacturing.group_apply' if group else 'manufacturing.operation_apply'
        before=copy.deepcopy(self.b.session.project.to_dict());index=self.b.session._history_index
        self.assertFalse(self.b.handle(dict(command=command,params=dict(token=draft['token'],reviewed=False),revision=self.b.revision))['ok'])
        self.run_command(command,token=draft['token'],reviewed=True)
        self.assertEqual(self.b.session._history_index,index+1)
        after=copy.deepcopy(self.b.session.project.to_dict())
        self.run_command('history.undo');self.assertEqual(before,self.b.session.project.to_dict())
        self.run_command('history.redo');self.assertEqual(after,self.b.session.project.to_dict())
        self.assertTrue(supported(self.b.session.project))
    def parity(self, name):
        before=copy.deepcopy(self.b.session.project.to_dict())
        preview=self.generate()
        direct,errors=generate_toolpath_preview_bundle(self.b.manufacturing.prepared[0].result.project)
        self.assertFalse(errors);self.assertEqual(direct,self.b.manufacturing.bundle)
        if preview.get('requiresReleaseReview'):
            refused=self.b.handle(dict(command='manufacturing.export',params=dict(token=preview['token'],reviewed=True,name=name),revision=self.b.revision))
            self.assertFalse(refused['ok']);self.assertIn('release',refused['error'])
        result=self.run_command('manufacturing.export',token=preview['token'],reviewed=True,release_reviewed=True,name=name)['manufacturing']
        directory=self.root/name;directory.mkdir()
        prepared=prepare_plate_outputs(self.b.session.project,_project_plates(self.b.session.project),repository_output_paths(directory,'reference',[1]))
        output=execute_plate_outputs(prepared,'CNC + CAM project',generated_paths_by_plate={1:direct})
        actual=next(Path(p) for p in result['paths'] if p.endswith('.cnc'))
        lines=lambda p:[s for s in p.read_text().splitlines() if s and not s.startswith('(')]
        self.assertEqual(lines(actual),lines(output.controller_paths[0]))
        self.assertEqual(before,self.b.session.project.to_dict())
        path=self.root/(name+'.camjob.json')
        self.run_command('project.save',path=str(path));self.run_command('project.open',path=str(path))
        self.assertEqual(before,self.b.session.project.to_dict());self.assertTrue(supported(self.b.session.project))
        return direct
    def test_profile_entry_finishing_review_units_parity_and_staleness(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            opid=self.b.session.project.operations[0].id
            params=dict(self.params,id=opid,entry_mode='ramp',lead_in=.3*f,lead_out=.15*f,finishing_pass=True,
                allowance=.015*f,finish_axial_stock=.02*f,finish_feed_override=1.2*f,finish_spindle_rpm_override=16000)
            draft=self.prepared('operation_prepare',dict(command='profile_save',params=params),'operation_ready')
            self.apply(draft);self.assertEqual(opid,self.b.session.project.operations[0].id)
            self.parity('ramp-'+units)
            self.assertFalse(self.b.handle(dict(command='manufacturing.profile_save',params=dict(self.params,id=opid),revision=self.b.revision))['ok'])
            # Converting into a group must not silently discard entry/finish settings.
            rejected=self.prepared('group_prepare',dict(id=opid,settings=self.settings(),source=dict(name='Do not erase')),'failed')
            self.assertIn('plunge',rejected['error'])
            params.update(entry_mode='helical',helix_diameter=.2*f,helix_pitch=.04*f,helix_fallback='strict_error',helix_feed_override=.7*f)
            draft=self.prepared('operation_prepare',dict(command='profile_save',params=params),'operation_ready')
            self.apply(draft);self.parity('helix-'+units)
            draft=self.prepared('operation_prepare',dict(command='profile_save',params=params),'operation_ready')
            self.run_command('manufacturing.setup',material='Acrylic')
            self.assertFalse(self.b.handle(dict(command='manufacturing.operation_apply',params=dict(token=draft['token'],reviewed=True),revision=self.b.revision))['ok'])
    def settings(self,f=1,strategy='retained_skin'):
        result=dict(station=2,feed_rate=f,plunge_rate=.25*f,spindle_rpm=18000,stepdown=.04*f,safe_z=.25*f,skin=.025*f,ignore_stock_less_than=.001*f,strategy=strategy)
        if strategy.startswith('Tabbed'): result['retain_tabs']=2
        if strategy.startswith('Clear'): result.update(main_stepover=.1*f,stepover=.05*f)
        return result
    def group_fixture(self,units='in'):
        self.fixture(units);f=25.4 if units=='mm' else 1
        old=self.b.session.project.entities[0]
        self.b.session.project.entities=[Polyline(points=[Point(x*f,y*f) for x,y in [(2,2),(5,2),(5,5),(4,5),(4,4),(2,4)]],closed=True,id=old.id)]
        self.run_command('manufacturing.setup',material_thickness=.125*f)
        self.run_command('manufacturing.profile_save',**dict(self.params,id=self.b.session.project.operations[0].id,side='inside',target_depth=.125*f,stepdown=.07*f))
        self.run_command('manufacturing.tool_save',station=2,name='Small',diameter=.125*f,spindle_rpm=18000,feed_rate=f,plunge_rate=.25*f,max_stepdown=.04*f,cutting_edge_length=.75*f)
        return dict(cut_intent='through',breakthrough=.015*f,tab_count=4,tab_width=.15*f,tab_height=.02*f)
    def group_case(self,strategy,label):
        for units in ('in','mm'):
            source=self.group_fixture(units);f=25.4 if units=='mm' else 1
            draft=self.prepared('group_prepare',dict(id=self.b.session.project.operations[0].id,settings=self.settings(f,strategy),source=source),'group_ready')
            self.assertGreater(len(draft['stages']),1)
            self.apply(draft,True)
            self.assertAlmostEqual(max(o.target_depth for o in self.b.session.project.operations),.14*f)
            self.assertTrue(all(not getattr(o,'remove_tabs_after',False) for o in self.b.session.project.operations))
            self.parity(label+'-'+units)
            ids=[o.id for o in self.b.session.project.operations]
            draft=self.prepared('group_prepare',dict(id=ids[-1],settings=self.settings(f,strategy),source=dict(name='Replanned')),'group_ready')
            self.apply(draft,True);self.assertEqual(ids,[o.id for o in self.b.session.project.operations])
            self.run_command('manufacturing.operation_toggle',id=ids[-1]);self.assertTrue(all(not o.enabled for o in self.b.session.project.operations))
            self.run_command('manufacturing.operation_toggle',id=ids[0]);self.generate()
            self.run_command('manufacturing.operation_delete',id=ids[-1]);self.assertFalse(self.b.session.project.operations)
            self.run_command('history.undo');self.assertEqual(ids,[o.id for o in self.b.session.project.operations])
    def test_tabbed_groups_units_edit_identity_output(self):
        self.group_case('Tabbed profile + rest / leave X tabs','tabbed')
    def test_hybrid_groups_units_edit_identity_output(self):
        self.group_case('Clear waste + tabbed finish','hybrid')
    def test_invalid_hybrid_and_stale_tool_group_toggle_are_safe(self):
        source=self.group_fixture();strategy='Clear waste + tabbed finish';opid=self.b.session.project.operations[0].id
        self.prepared('group_prepare',dict(id=opid,settings=self.settings(1,strategy),source=dict(source,tab_height=.04)),'failed')
        draft=self.prepared('group_prepare',dict(id=opid,settings=self.settings(1,strategy),source=source),'group_ready');self.apply(draft,True)
        self.run_command('manufacturing.tool_save',station=2,name='Changed',diameter=.1,spindle_rpm=18000,feed_rate=1,plunge_rate=.25,max_stepdown=.04,cutting_edge_length=.75)
        groupid=self.b.session.project.operations[0].id
        self.run_command('manufacturing.operation_toggle',id=groupid);self.run_command('manufacturing.operation_toggle',id=groupid)
        response=self.b.handle(dict(command='manufacturing.generate',revision=self.b.revision))
        self.assertFalse(response['ok']);self.assertIn('changed',response['error'])
    def test_speed_cleanout_units_motion_and_fallback(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            self.run_command('manufacturing.operation_delete',id=self.b.session.project.operations[0].id)
            self.b.session.project.entities=[Polyline(points=[Point((3+r*math.cos(i*math.tau/128))*f,(3+r*math.sin(i*math.tau/128))*f) for i in range(128)],closed=True,id=eid) for eid,r in [('outer',2),('inner',1.45)]]
            self.run_command('manufacturing.setup',material_thickness=.75*f)
            params=dict(name='Speed ring '+units,tool_station=1,entity_ids=['outer'],island_entity_ids=['inner'],target_depth=.3*f,safe_z=.25*f,stepdown=.1*f,stepover=.125*f,cut_direction='conventional',strategy='speed_cleanout',speed_max_full_depth_engagement=.06*f,speed_center_feed_percent=60,speed_link_distance=.25*f,speed_fallback='error')
            draft=self.prepared('operation_prepare',dict(command='fill_save',params=params),'operation_ready');self.apply(draft)
            direct=self.parity('speed-'+units)
            self.assertIn('speed_center_cleanup',str(direct))
            params.update(id=self.b.session.project.operations[0].id,speed_max_full_depth_engagement=.000001*f)
            self.prepared('operation_prepare',dict(command='fill_save',params=params),'failed')
            params['speed_fallback']='depth_step'
            draft=self.prepared('operation_prepare',dict(command='fill_save',params=params),'operation_ready');self.apply(draft);self.parity('speed-fallback-'+units)
    def test_mm_circle_core_coverage_rejection_is_preserved(self):
        # Current pinned core leaves a fringe on this circle fixture. Never bypass its audit.
        self.fixture('mm');f=25.4
        self.run_command('manufacturing.operation_delete',id=self.b.session.project.operations[0].id)
        self.b.session.project.entities=[Circle(center=Point(3*f,3*f),radius=2*f,id='outer'),Circle(center=Point(3*f,3*f),radius=1.45*f,id='inner')]
        params=dict(name='Circular ring',tool_station=1,entity_ids=['outer'],island_entity_ids=['inner'],target_depth=.3*f,safe_z=.25*f,stepdown=.1*f,stepover=.125*f,cut_direction='conventional',strategy='speed_cleanout',speed_max_full_depth_engagement=.06*f,speed_center_feed_percent=60,speed_link_distance=.25*f,speed_fallback='error')
        response=self.prepared('operation_prepare',dict(command='fill_save',params=params),'failed')
        self.assertIn('unswept at final depth',response['error'])
        from cam_core.automation import AutomationSession
        candidate=AutomationSession(project=copy.deepcopy(self.b.session.project))
        mutate(candidate,'fill_save',params,True)
        paths,validation=preflight_bundle(candidate.project,lambda _:None)
        self.assertTrue(validation['blocking'])
        self.assertIn(response['error'],[i['message'] for i in validation['issues']])

    def test_stock_verification_confirmation_gate_roundtrip_and_invalidations(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            self.run_command('manufacturing.setup',machine='Evaluation router',through_cut_breakthrough=.015*f,require_verified_stock=True)
            token=self.generate()['token']
            self.assertFalse(self.b.handle(dict(command='manufacturing.export',params=dict(token=token,reviewed=True,name='unverified'),revision=self.b.revision))['ok'])
            review=stock_verification(self.b.session.project)
            confirmation=dict(token=review['token'],material_checked=True,thickness_checked=True,machine_size_checked=True)
            self.assertFalse(self.b.handle(dict(command='manufacturing.verify_stock',params=dict(confirmation,machine_size_checked=False),revision=self.b.revision))['ok'])
            self.run_command('manufacturing.verify_stock',**confirmation)
            self.assertTrue(stock_verification(self.b.session.project)['verified'])
            self.run_command('history.undo');self.assertFalse(stock_verification(self.b.session.project)['verified'])
            self.run_command('history.redo');self.assertTrue(stock_verification(self.b.session.project)['verified'])
            self.parity('verified-'+units);self.assertTrue(stock_verification(self.b.session.project)['verified'])
            self.run_command('manufacturing.setup',safe_z=.3*f);self.assertTrue(stock_verification(self.b.session.project)['verified'])
            for changes in [dict(material='Acrylic'),dict(material_thickness=.6*f),dict(machine='Other router'),dict(through_cut_breakthrough=.02*f),dict(width=48*f)]:
                previous=stock_verification(self.b.session.project)['token']
                self.run_command('manufacturing.setup',**changes);self.assertFalse(stock_verification(self.b.session.project)['verified'])
                self.assertFalse(self.b.handle(dict(command='manufacturing.verify_stock',params=dict(confirmation,token=previous),revision=self.b.revision))['ok'])
                confirmation['token']=stock_verification(self.b.session.project)['token']
                self.run_command('manufacturing.verify_stock',**confirmation)
            self.b.session.project.metadata['plates'][0]['x']=f
            self.assertFalse(stock_verification(self.b.session.project)['verified'])

for name in list(Base.__dict__):
    if name.startswith('test_'): setattr(AdvancedManufacturingTests,name,None)

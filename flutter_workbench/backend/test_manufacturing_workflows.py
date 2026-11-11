import copy
from pathlib import Path
from unittest.mock import patch
from test_manufacturing_advanced import AdvancedManufacturingTests as Base
from cam_core.model import Rectangle, Point
from cam_core.motion_verifier import verify_generated_motion

class ManufacturingWorkflowTests(Base):
    def recipe(self,action,**params):
        return self.run_command('manufacturing.recipe_'+action,**params)
    def test_recipe_roundtrip_units_and_explicit_missing_cutter(self):
        source=self.group_fixture();strategy='Tabbed profile + rest / leave X tabs'
        draft=self.prepared('group_prepare',dict(id=self.b.session.project.operations[0].id,settings=self.settings(1,strategy),source=source),'group_ready');self.apply(draft,True)
        before=copy.deepcopy(self.b.session.project.to_dict());revision=self.b.revision
        version=self.recipe('list')['version']
        saved=self.recipe('save',operation_id=self.b.session.project.operations[0].id,name='Small tabbed tool',version=version)
        self.assertEqual(before,self.b.session.project.to_dict());self.assertEqual(revision,self.b.revision)
        item=saved['recipes'][0];self.assertEqual(item['recipe']['values']['feed_rate'],60)
        self.assertNotIn('reviewed',item['recipe'])
        self.assertTrue(self.b.manufacturing_recipes.path.is_file())
        self.group_fixture('mm');opid=self.b.session.project.operations[0].id
        recalled=self.recipe('resolve',id=item['id'],operation_id=opid,version=saved['version'])
        self.assertFalse(recalled['reviewed']);self.assertAlmostEqual(recalled['settings']['feed_rate'],25.4)
        self.assertEqual(recalled['settings']['retain_tabs'],2)
        source=dict(cut_intent='through',breakthrough=.015*25.4,tab_count=4,tab_width=.15*25.4,tab_height=.02*25.4)
        draft=self.prepared('group_prepare',dict(id=opid,settings=recalled['settings'],recipe_tool=recalled['recipe_tool'],source=source),'group_ready')
        self.apply(draft,True);self.parity('recipe-mm')
        self.group_fixture();opid=self.b.session.project.operations[0].id
        self.run_command('manufacturing.tool_save',station=2,name='Changed identity',diameter=.125,spindle_rpm=18000,feed_rate=1,plunge_rate=.25,max_stepdown=.04,cutting_edge_length=.75)
        response=self.b.handle(dict(command='manufacturing.recipe_resolve',params=dict(id=item['id'],operation_id=opid,version=saved['version']),revision=self.b.revision))
        self.assertFalse(response['ok']);self.assertIn('explicitly',response['error'])
        selected=self.recipe('resolve',id=item['id'],operation_id=opid,version=saved['version'],replacement_station=2)
        self.assertTrue(selected['replaced']);self.assertEqual(selected['recipe_tool']['name'],'Changed identity')
        self.b.session.project.tool_library.tools[-1].name='Changed again'
        self.prepared('group_prepare',dict(id=opid,settings=selected['settings'],recipe_tool=selected['recipe_tool'],source=source),'failed')
        self.assertFalse(self.b.handle(dict(command='manufacturing.recipe_delete',params=dict(id=item['id'],version=version),revision=self.b.revision))['ok'])
        deleted=self.recipe('delete',id=item['id'],version=saved['version']);self.assertFalse(deleted['recipes'])
    def test_recipe_failure_never_overwrites_library_or_document(self):
        source=self.group_fixture();draft=self.prepared('group_prepare',dict(id=self.b.session.project.operations[0].id,settings=self.settings(),source={}), 'group_ready');self.apply(draft,True)
        before=copy.deepcopy(self.b.session.project.to_dict())
        args=dict(operation_id=self.b.session.project.operations[0].id,name='Stable',version=self.recipe('list')['version'])
        saved=self.recipe('save',**args);raw=self.b.manufacturing_recipes.path.read_bytes()
        args.update(name='Other',version=saved['version'])
        with patch.object(Path,'replace',side_effect=OSError('Publication refused')):
            r=self.b.handle(dict(command='manufacturing.recipe_save',params=args,revision=self.b.revision))
        self.assertFalse(r['ok']);self.assertEqual(raw,self.b.manufacturing_recipes.path.read_bytes());self.assertEqual(before,self.b.session.project.to_dict())
        from manufacturing_recipes import ManufacturingRecipes
        second=ManufacturingRecipes(self.b.manufacturing_recipes.path)
        with self.b.manufacturing_recipes.locked():
            with self.assertRaisesRegex(ValueError,'Another drawing'): second.command('recipe_save',args,self.b.session.project)
    def fill_fixture(self,units):
        self.fixture(units);f=25.4 if units=='mm' else 1
        self.run_command('manufacturing.operation_delete',id=self.b.session.project.operations[0].id)
        return dict(name='Detailed pocket',tool_station=1,entity_ids=[self.b.session.project.entities[0].id],island_entity_ids=[],target_depth=.3*f,safe_z=.25*f,stepdown=.1*f,stepover=.1*f,cut_direction='climb',strategy='concentric')
    def test_profile_custom_increments_and_fill_equal_custom_floor_passes(self):
        for units in ['in','mm']:
            f=25.4 if units=='mm' else 1;self.fixture(units)
            params=dict(self.params,id=self.b.session.project.operations[0].id,pass_depths=[.1*f,.15*f],finishing_pass=True,finish_axial_stock=.05*f,allowance=.01*f)
            draft=self.prepared('operation_prepare',dict(command='profile_save',params=params),'operation_ready');self.apply(draft);self.parity('profile-passes-'+units)
            params=self.fill_fixture(units)
            params.update(pass_mode='equal_count',pass_count=3,bottom_finish_pass=True,bottom_finish_depth=.03*f,bottom_finish_feed_override=f)
            draft=self.prepared('operation_prepare',dict(command='fill_save',params=params),'operation_ready');self.apply(draft)
            self.parity('equal-floor-'+units)
            params.update(id=self.b.session.project.operations[0].id,pass_mode='custom_increments',pass_depths=[.1*f,.1*f,.07*f])
            draft=self.prepared('operation_prepare',dict(command='fill_save',params=params),'operation_ready');self.apply(draft);self.parity('custom-floor-'+units)
            for bad in [dict(pass_depths=[.1*f]),dict(pass_count=2.5),dict(pass_depths=[True]),dict(bottom_finish_pass='true')]:
                self.prepared('operation_prepare',dict(command='fill_save',params=dict(params,**bad)),'failed')
    def test_fill_ramp_and_helical_entry_units_and_invalid_fit(self):
        for units in ['in','mm']:
            f=25.4 if units=='mm' else 1;params=self.fill_fixture(units)
            params.update(entry_mode='ramp',ramp_length=.5*f)
            draft=self.prepared('operation_prepare',dict(command='fill_save',params=params),'operation_ready');self.apply(draft);self.parity('fill-ramp-'+units)
            params.update(id=self.b.session.project.operations[0].id,entry_mode='helical',helix_diameter=.3*f,helix_min_diameter=.15*f,helix_pitch=.05*f,helix_ramp_angle=15,helix_clearance=.01*f,helix_finish_revolutions=1,helix_feed_override=f,helix_fallback='error',dwell_seconds=0)
            draft=self.prepared('operation_prepare',dict(command='fill_save',params=params),'operation_ready');self.apply(draft);self.parity('fill-helix-'+units)
            self.prepared('operation_prepare',dict(command='fill_save',params=dict(params,helix_min_diameter=100*f,helix_diameter=100*f)),'failed')
    def test_keep_down_proven_links_units_output_and_invalid_limits(self):
        for units in ['in','mm']:
            f=25.4 if units=='mm' else 1;params=self.fill_fixture(units)
            params.update(keep_tool_down=True,stay_down_max_distance=.25*f,stay_down_max_engagement=.125*f,stay_down_feed_percent=70)
            draft=self.prepared('operation_prepare',dict(command='fill_save',params=params),'operation_ready');self.apply(draft)
            direct=self.parity('keep-down-'+units)
            generated=next(iter(direct.values()))
            self.assertTrue(any(block.metadata.get('stay_down_links') for block in generated.blocks))
            self.assertFalse(verify_generated_motion(self.b.session.project,self.b.session.project.operations[0],generated))
            params['id']=self.b.session.project.operations[0].id
            for bad in [dict(stay_down_max_distance=0),dict(stay_down_max_engagement=0),dict(stay_down_feed_percent=101)]:
                self.prepared('operation_prepare',dict(command='fill_save',params=dict(params,**bad)),'failed')

for name in list(Base.__dict__):
    if name.startswith('test_'): setattr(ManufacturingWorkflowTests,name,None)

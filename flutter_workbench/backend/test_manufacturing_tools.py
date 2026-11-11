import copy
from test_manufacturing_management import ManufacturingManagementTests as Base
from manufacturing_tools import validate_turret,prepare_turret
from cam_core.model import Tool,ToolKind

class ManufacturingToolTests(Base):
    def test_catalog_units_custom_persistence_and_version(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            lib=self.run_command('manufacturing.catalog_list');before=copy.deepcopy(self.b.session.project.to_dict())
            row=next(v for v in lib['catalog'] if v['id']=='vortex-5609a')
            resolved=self.run_command('manufacturing.catalog_resolve',id=row['id'],recipe=0,station=2,version=lib['version'])['tool']
            self.assertAlmostEqual(resolved['diameter'],.125*f)
            self.assertAlmostEqual(resolved['feed_rate'],72*f/60)
            self.assertAlmostEqual(resolved['plunge_rate'],30*f/60)
            self.assertEqual(before,self.b.session.project.to_dict())
            saved=self.run_command('manufacturing.catalog_save',station=1,version=lib['version'])
            custom=[v for v in saved['catalog'] if v.get('custom')][-1]
            stale=self.b.handle(dict(command='manufacturing.catalog_delete',params=dict(id=custom['id'],version=lib['version']),revision=self.b.revision))
            self.assertFalse(stale['ok'])
            self.assertEqual(before,self.b.session.project.to_dict())
            from manufacturing_tools import ManufacturingTools
            reopened=ManufacturingTools(self.b.manufacturing_tools.path,self.b.manufacturing_tools.seed)
            self.assertTrue(any(t['id']==custom['id'] for t in reopened.command('catalog_list',{},self.b.session.project)['catalog']))
            self.run_command('manufacturing.catalog_delete',id=custom['id'],version=saved['version'])

    def test_reviewed_turret_history_output_stale_and_used_station(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            tool=self.b.session.project.tool_library.tools[0].to_dict();tool['feed_rate']=1.5*f
            p=dict(tools=[tool],minimum_rpm=8000,maximum_rpm=24000)
            d=self.workflow('turret',p);self.assertEqual(d['details']['changed_stations'],[1])
            self.apply_workflow(d);self.parity('turret-'+units)
            self.workflow('turret',dict(p,tools=[]),'failed')
            d=self.workflow('turret',p);self.run_command('manufacturing.setup',material='Wood')
            self.assertFalse(self.b.handle(dict(command='manufacturing.management_apply',params=dict(token=d['token'],reviewed=True),revision=self.b.revision))['ok'])

    def test_turret_station_limits_and_all_tool_families(self):
        t=Tool(6,'T6',.25,spindle_rpm=18000)
        with self.assertRaisesRegex(ValueError,'12000'):validate_turret([t])
        t.spindle_rpm=12000;validate_turret([t])
        t.station=8
        with self.assertRaisesRegex(ValueError,'spindle-disabled'):validate_turret([t])
        t.spindle_mode='disabled';t.spindle_rpm=None;validate_turret([t])
        for kind in ToolKind:
            t.kind=kind;validate_turret([t])
        t.diameter=float('nan')
        with self.assertRaises(ValueError):validate_turret([t])
        with self.assertRaises(ValueError):validate_turret([],25000,8000)

    def test_empty_job_turret_review_and_noop_library_mutation(self):
        self.run_command('project.new')
        tool=Tool(1,'Quarter',.25,spindle_rpm=18000,feed_rate=2,plunge_rate=.5,max_stepdown=.125,cutting_edge_length=.75)
        self.apply_workflow(self.workflow('turret',dict(tools=[tool.to_dict()],minimum_rpm=8000,maximum_rpm=24000)))
        self.assertEqual(len(self.b.session.project.tool_library.tools),1)

    def test_saved_rack_converts_units_and_remains_private_until_review(self):
        self.fixture('in');lib=self.run_command('manufacturing.catalog_list')
        saved=self.run_command('manufacturing.catalog_rack_save',version=lib['version'])
        self.run_command('project.new',units='mm')
        draft=self.run_command('manufacturing.catalog_rack_resolve',version=saved['version'])['rack']
        self.assertAlmostEqual(draft['tools'][0]['diameter'],6.35)
        self.assertAlmostEqual(draft['tools'][0]['feed_rate'],50.8)
        self.assertEqual(self.b.session.project.tool_library.tools,[])
        self.apply_workflow(self.workflow('turret',draft))
        self.assertAlmostEqual(self.b.session.project.tool_library.tools[0].diameter,6.35)

    def test_imported_catalog_override_has_one_identity_and_resolves_saved_values(self):
        self.run_command('project.new')
        lib=self.b.manufacturing_tools
        original=lib.command('catalog_list',{},self.b.session.project)
        item=copy.deepcopy(original['catalog'][0]);item['name']='Saved shop revision';item['diameter_in']=.2
        lib.path.parent.mkdir(parents=True,exist_ok=True)
        lib.write(dict(schema='flutter-tool-catalog-v1',tools=[item]))
        merged=lib.command('catalog_list',{},self.b.session.project)
        self.assertEqual(len(merged['catalog']),len(original['catalog']))
        resolved=lib.command('catalog_resolve',dict(id=item['id'],recipe=0,station=1,version=merged['version']),self.b.session.project)['tool']
        self.assertEqual(resolved['name'],'Saved shop revision');self.assertEqual(resolved['diameter'],.2)

for name in dir(Base):
    if name.startswith('test_'):setattr(ManufacturingToolTests,name,None)

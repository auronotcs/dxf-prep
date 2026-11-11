import copy
from test_manufacturing_management import ManufacturingManagementTests as Base
from cam_core.model import Tool,ToolKind,Circle,Point,Polyline

class SpecialManufacturingTests(Base):
 def special(self,p,status='operation_ready'):
  return self.prepared('operation_prepare',dict(command='special_save',params=p),status)
 def empty_fixture(self,units='in'):
  self.fixture(units);self.b.session.project.operations=[]
 def test_engrave_knife_thread_real_motion_units_history_output(self):
  for units in ('in','mm'):
   f=25.4 if units=='mm' else 1
   for kind in ('engrave','drag_knife','thread_mill'):
    self.empty_fixture(units);job=self.b.session.project
    tool=Tool(8 if kind!='thread_mill' else 3,kind,.1*f,kind=ToolKind.ENGRAVER if kind=='engrave' else ToolKind(kind),spindle_mode='disabled' if kind!='thread_mill' else 'required',spindle_rpm=None if kind!='thread_mill' else 18000,feed_rate=1*f,plunge_rate=.25*f,cutting_edge_length=.75*f,metadata={'spring_travel':.375*f,'tip_angle':90})
    job.tool_library.tools.append(tool)
    if kind=='thread_mill':job.entities=[Circle(id='pilot',center=Point(4*f,4*f),radius=.1035*f)]
    p=dict(kind=kind,name=kind,tool_station=tool.station,entity_ids=[job.entities[0].id],target_depth=.1*f,safe_z=.25*f)
    if kind=='engrave':p.update(approach_height=.05*f,require_stay_on_material=True,reverse_open_paths=False)
    if kind=='drag_knife':p.update(blade_offset=.05*f,swivel_depth=.005*f,swivel_angle=20,approach_height=.05*f,alignment_length=.15*f,curve_tolerance=.001*f,passes=2,reverse_paths=False,setup_verified=True)
    if kind=='thread_mill':p.update(thread_designation='1/4-20 UNC-2B',pitch_diameter_adjustment=0,radial_passes=2,spring_passes=1,handedness='right',cut_direction='climb',cutter_verified=True,pilot_hole_depth=.2*f,bottom_clearance=.05*f)
    self.freeze();d=self.special(p);self.apply(d);self.parity(kind+'-'+units)
    opid=self.b.session.project.operations[0].id
    self.apply(self.special(dict(p,id=opid,name='Edited '+kind)))
    self.assertEqual(self.b.session.project.operations[0].id,opid)
    if kind=='thread_mill':self.special(dict(p,id=opid,cutter_verified=False),'failed')
    if kind=='drag_knife':self.special(dict(p,id=opid,setup_verified=False),'failed')
    self.special(dict(p,tool_station=1),'failed')
    self.assertFalse(self.b.handle(dict(command='manufacturing.operation_prepare',params=dict(command='special_save',params=dict(p,safe_z=float('nan'))),revision=self.b.revision))['ok'])

 def test_independent_profile_and_pocket_rest_units_dependencies(self):
  for units in ('in','mm'):
   f=25.4 if units=='mm' else 1
   for kind in ('profile_rest_machining','rest_machining'):
    self.group_fixture(units);job=self.b.session.project
    if kind=='rest_machining':
     job.operations=[]
     from cam_core.model import IslandFillOperation
     job.operations=[IslandFillOperation('Pocket',1,[job.entities[0].id],.125*f,.25*f,stepover=.1*f,stepdown=.07*f)]
    source=job.operations[0];self.freeze()
    p=dict(kind=kind,name=kind,tool_station=2,source_operation_id=source.id,safe_z=.25*f,stepdown=.04*f,stock_to_leave=0,ignore_stock_less_than=0)
    if kind=='profile_rest_machining':p.update(inherit_pass_schedule=True,preserve_tabs=True,remove_tabs_after=False)
    else:p['stepover']=.04*f
    d=self.special(p);self.apply(d);self.parity(kind+'-'+units)
    self.assertIn(source.id,self.b.session.project.operations[-1].depends_on_ids)
    self.assertFalse(self.b.handle(dict(command='manufacturing.operation_delete',params={'id':source.id},revision=self.b.revision))['ok'])
    self.special(dict(p,tool_station=1),'failed')

 def test_final_release_audit_confirmation_stale_history_units_output(self):
  for units in ('in','mm'):
   self.fixture(units);f=25.4 if units=='mm' else 1
   job=self.b.session.project;op=job.operations[0]
   op.tab_count=4;op.tab_width=.5*f;op.tab_height=.1*f
   self.freeze();d=self.workflow('release_audit',dict(id=op.id,plate_number=1));r=d['details']['final_release']
   p=dict(id=op.id,plate_number=1,fingerprint=r['fingerprint'],confirmed_parts=r['parts'],evidence_note='SYNTHETIC independently fixed fixture; not machine qualification',waste_warning_review=r['warning_key'])
   self.workflow('final_release',dict(p,confirmed_parts=[]),'failed')
   self.workflow('final_release',dict(p,fingerprint='stale'),'failed')
   self.workflow('final_release',dict(p,evidence_note=''),'failed')
   draft=self.workflow('final_release',p);self.assertTrue(draft['requiresReleaseReview'])
   self.apply_workflow(draft);self.parity('final-release-'+units)
   self.assertEqual(self.b.session.project.operations[-1].kind,'tab_release')
   self.assertEqual(self.b.session.project.operations[0].tab_count,4)

 def test_special_cancel_stale_review_and_protected_fields(self):
  self.empty_fixture();job=self.b.session.project
  job.tool_library.tools.append(Tool(8,'Engraver',.1,kind=ToolKind.ENGRAVER,spindle_mode='disabled',feed_rate=1,plunge_rate=.25,metadata={'spring_travel':.375}))
  self.freeze()
  p=dict(kind='engrave',name='Engrave',tool_station=8,entity_ids=[job.entities[0].id],target_depth=.05,safe_z=.25,approach_height=.05)
  d=self.special(p);self.run_command('manufacturing.cancel')
  self.assertFalse(self.b.handle(dict(command='manufacturing.operation_apply',params=dict(token=d['token'],reviewed=True),revision=self.b.revision))['ok'])
  d=self.special(p);self.run_command('manufacturing.setup',material='Acrylic')
  self.assertFalse(self.b.handle(dict(command='manufacturing.operation_apply',params=dict(token=d['token'],reviewed=True),revision=self.b.revision))['ok'])
  self.special(dict(p,feed_rate_override=100),'failed')
  self.assertFalse(self.b.session.project.operations)
  self.apply(self.special(p));self.b.session.project.operations[0].feed_rate_override=1
  from manufacturing_support import supported
  self.assertFalse(supported(self.b.session.project))

 def test_specialized_tool_metadata_catalog_units_and_validation(self):
  from manufacturing_tools import ManufacturingTools,validate_turret
  from design_bridge import CORE_ROOT
  from cam_core.units import convert_project_units
  self.empty_fixture()
  job=self.b.session.project
  tool=Tool(8,'Special engraver',.1,kind=ToolKind.ENGRAVER,spindle_mode='disabled',feed_rate=1,plunge_rate=.25,metadata={'spring_travel':.375,'tip_angle':90,'thread_tpi':'20,24','thread_form':'UN 60'})
  job.tool_library.tools.append(tool)
  catalog=ManufacturingTools(self.root/'test-special-catalog.json',CORE_ROOT/'default_tool_catalog.json')
  listed=catalog.command('catalog_list',{},job)
  saved=catalog.command('catalog_save',dict(version=listed['version'],station=8),job)
  row=next(v for v in saved['catalog'] if v['name']=='Special engraver')
  metric=convert_project_units(job,'mm')
  resolved=catalog.command('catalog_resolve',dict(version=saved['version'],id=row['id'],recipe=0,station=8),metric)['tool']
  self.assertAlmostEqual(resolved['metadata']['spring_travel'],9.525)
  self.assertEqual(resolved['metadata']['tip_angle'],90)
  self.assertEqual(resolved['metadata']['thread_tpi'],'20,24')
  tool.metadata['spring_travel']=float('nan')
  with self.assertRaisesRegex(ValueError,'spring_travel'):validate_turret([tool])

for name in dir(Base):
 if name.startswith('test_') and name not in SpecialManufacturingTests.__dict__:setattr(SpecialManufacturingTests,name,None)

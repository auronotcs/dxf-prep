import copy,json,tempfile,unittest
from pathlib import Path
from design_bridge import Bridge
from cam_core.model import RouterOffsetOperation,Tool,ToolLibrary,Rectangle,Point,DrillCirclesOperation
from cam_core.units import unit_scale
from manufacturing_support import stock_verification
from guided_readiness import parameters
from cam_core.router_parameters import RouterValidationContext,validate_router_parameters

class GuidedReadinessTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.b=Bridge(self.root/'prefs.json',recovery_directory=self.root/'recovery');self.addCleanup(self.b.recovery.close);self.addCleanup(self.b.manufacturing.close)
  self.fixture()
 def call(self,verb,**p):return self.b.handle(dict(command='manufacturing.'+verb,params=p,revision=self.b.revision))
 def ok(self,verb,**p):
  r=self.call(verb,**p);self.assertTrue(r['ok'],r);json.dumps(r,allow_nan=False);return r['review']
 def fixture(self,units='in'):
  f=unit_scale('in',units)
  r=self.b.handle(dict(command='project.new',params=dict(units=units),revision=self.b.revision));self.assertTrue(r['ok'])
  self.recipe=dict(id='coupon-1',material='Plywood',material_family='plywood',qualification='production_approved',qualification_evidence='Synthetic fixture only',thickness_min_in=.5,thickness_max_in=.5,process_roles=['outside_profile'],entry_strategies=['plunge'],rpm=18000,feed=120,plunge=30,stepdown=.125)
  self.row=dict(id='custom-guided',name='Quarter',kind='end_mill',spindle_mode='required',diameter_in=.25,cutting_edge_length_in=.75,recipes=[copy.deepcopy(self.recipe)],custom=True)
  self.b.manufacturing_tools.write(dict(schema='flutter-tool-catalog-v1',tools=[self.row]))
  j=self.b.session.project;j.metadata['job_setup']=dict(material='Plywood',material_thickness=.5*f,safe_z=.25*f)
  j.entities=[Rectangle(id='body',corner_a=Point(1*f,1*f),corner_b=Point(6*f,5*f))]
  j.tool_library=ToolLibrary([Tool(1,'Quarter',.25*f,spindle_rpm=18000,feed_rate=2*f,plunge_rate=.5*f,max_stepdown=.125*f,cutting_edge_length=.75*f,metadata={'catalog_tool_id':'custom-guided'})])
  j.operations=[RouterOffsetOperation('Outside',1,['body'],.515*f,.25*f,stepdown=.125*f,cut_intent='through',breakthrough=.015*f,recipe_metadata=copy.deepcopy(self.recipe))]
  v=stock_verification(j);j.metadata['job_setup'].update(stock_verification=v['fingerprint'],flutter_stock_verification=v['token'])
  self.b.snapshot();self.b.session._record_history();self.b.sync_recovery(force=True)
 def frozen(self):
  self.b.snapshot();return copy.deepcopy((self.b.session.project.to_dict(),self.b.session._history,self.b.session.selected_ids,self.b.revision,{p.name:p.read_bytes() for p in self.b.recovery.root.glob('*.json')}))
 def action(self,r,verb='compare',op='default'):
  return self.call('guided_action',token=r['token'],operation_id=self.b.session.project.operations[0].id if op=='default' else op,action=verb)
 def test_core_parity_units_current_qualification_and_no_mutation(self):
  for units in ('in','mm','cm'):
   self.fixture(units);before=self.frozen();r=self.ok('guided_readiness')
   self.assertEqual(r['counts'],dict(ready=1,blocked=0,unsupported=0,disabled=0),r)
   j=self.b.session.project;op=j.operations[0];f=unit_scale('in',units)
   context=RouterValidationContext(.5*f,units,False,True,guided_mode=True,stock_verified=True,job_material='Plywood',job_thickness_in=.5,process_role='outside_profile')
   self.assertIsNone(validate_router_parameters(parameters(op,j.tool_library.tools[0]),context))
   self.assertEqual(self.frozen(),before);self.assertTrue(self.action(r)['ok']);self.assertEqual(self.frozen(),before)
 def test_stock_cutter_parameters_and_missing_qualification_are_separate_findings(self):
  j=self.b.session.project;j.metadata['job_setup']['material_thickness']=.6;j.tool_library.tools[0].spindle_rpm=30000;j.operations[0].cut_intent='custom';j.operations[0].recipe_metadata={}
  r=self.ok('guided_readiness');row=r['operations'][0];self.assertEqual(row['status'],'blocked');self.assertFalse(r['stock']['verified'])
  keys={c['key'] for c in row['checks'] if not c['passed']};self.assertTrue({'stock','cutter','qualification','catalog','values','parameters'}<=keys,keys)
  self.assertTrue(any('through-cut' in v for v in row['findings']))
 def test_disabled_unsupported_dependent_and_missing_tool_not_reported_ready(self):
  j=self.b.session.project;j.operations[0].enabled=False
  j.operations.extend([DrillCirclesOperation('Drill',1,['body'],.1,.25),RouterOffsetOperation('Dependent',1,['body'],.5,.25,depends_on_ids=['source']),RouterOffsetOperation('Missing cutter',4,['body'],.5,.25)])
  r=self.ok('guided_readiness');self.assertEqual(r['counts'],dict(ready=0,blocked=1,unsupported=2,disabled=1));self.assertIn('no loaded cutter',r['operations'][-1]['findings'][-1])
 def test_action_rejects_stale_job_stock_tool_library_and_seed(self):
  for change in ('revision','stock','tool','library','seed'):
   self.fixture();r=self.ok('guided_readiness')
   if change=='revision':self.b.revision+=1
   elif change=='stock':self.b.session.project.metadata['job_setup']['material']='Aluminum'
   elif change=='tool':self.b.session.project.tool_library.tools[0].feed_rate=3
   elif change=='library':
    d=self.b.manufacturing_tools.read();d['tools'][0]['recipes'][0]['qualification']='retired';self.b.manufacturing_tools.write(d)
   else:
    seed=self.root/'seed.json';seed.write_text('[]');self.b.manufacturing_tools.seed=seed
   before=self.frozen();result=self.action(r);self.assertFalse(result['ok'],change);self.assertIn('Refresh readiness',result['error']);self.assertEqual(before,self.frozen())
 def test_recipe_comparison_scope_exclusions_evidence_and_overrides(self):
  j=self.b.session.project;j.operations[0].feed_rate_override=3
  data=self.b.manufacturing_tools.read();excluded=copy.deepcopy(self.recipe);excluded.update(material_family='aluminum',qualification='retired');data['tools'][0]['recipes'].append(excluded);self.b.manufacturing_tools.write(data)
  r=self.ok('guided_readiness');row=r['operations'][0];self.assertEqual(row['status'],'blocked');self.assertTrue(any('Effective settings differ' in v for v in row['findings']))
  compared=self.action(r)['review']['operation'];candidates=[c for c in compared['candidates'] if c['id']=='custom-guided'];self.assertEqual(len(candidates),2);self.assertTrue(any(not c['eligible'] for c in candidates));self.assertTrue(all(c['evidence'] for c in candidates))
 def test_readiness_actions_are_read_only_protected_edit_rejected_and_locate_preserves_selection(self):
  r=self.ok('guided_readiness');before=self.frozen()
  for verb in ('locate','compare','library'):
   result=self.action(r,verb);self.assertTrue(result['ok'],result);self.assertEqual(before,self.frozen())
  result=self.action(r,'locate');self.assertEqual(result['review']['bounds'],[1,1,6,5])
  self.assertFalse(self.action(r,'operation')['ok']) # Imported recipe ownership remains protected.
  for verb in ('export','approve','apply'):self.assertFalse(self.action(r,verb)['ok'])
  self.assertFalse(self.action(r,'compare',op='missing')['ok']);self.assertEqual(before,self.frozen())
 def test_editable_job_actions_leave_recovery_history_native_save_and_output_gate_unchanged(self):
  j=self.b.session.project;j.operations[0].recipe_metadata={};self.b.session._record_history();self.b.sync_recovery(force=True)
  self.assertTrue(self.b.recovery.path(self.b.recovery.identity).exists());before=self.frozen();r=self.ok('guided_readiness');self.assertTrue(r['editable'])
  for verb in ('stock','verify','turret','operation','library','locate','compare'):
   self.assertTrue(self.action(r,verb)['ok']);self.assertEqual(before,self.frozen())
  saved=self.root/'job.camjob.json';reply=self.b.handle(dict(command='project.save',params={'path':str(saved)},revision=self.b.revision));self.assertTrue(reply['ok'])
  from cam_core.model import CamProject
  self.assertEqual(CamProject.load(saved).to_dict(),j.to_dict())
  response=self.call('export',token=r['token'],reviewed=True,name='not-approved');self.assertFalse(response['ok'])
  self.assertEqual(j.to_dict(),self.b.session.project.to_dict())

 def test_empty_and_malformed_job_records_explain_instead_of_green(self):
  self.b.session.project.operations=[];r=self.ok('guided_readiness');self.assertFalse(any(r['counts'].values()))
  self.fixture();self.b.session.project.operations[0].recipe_metadata={'qualification':'production_approved','thickness_min_in':'bad'}
  r=self.ok('guided_readiness');self.assertEqual(r['operations'][0]['status'],'blocked')

if __name__=='__main__':unittest.main()

import copy,json,time,unittest
import test_operation_recipe as attachment
from cam_core.units import unit_scale
from cam_core.model import CamProject,Line,Point,OffsetSide
from cam_core.second_tool_recipe import FIELDS
from manufacturing_support import supported,PROFILE_FIELDS,PROFILE_ADVANCED,preflight_bundle,serialize_motion

class RouterRecipePresetTests(unittest.TestCase):
 setUp=attachment.OperationRecipeTests.setUp
 fixture=attachment.OperationRecipeTests.fixture
 frozen=attachment.OperationRecipeTests.frozen
 call=attachment.OperationRecipeTests.call
 request=attachment.OperationRecipeTests.request
 choices=attachment.OperationRecipeTests.choices
 choice=attachment.OperationRecipeTests.choice
 draft=attachment.OperationRecipeTests.draft
 apply=attachment.OperationRecipeTests.apply
 command=attachment.OperationRecipeTests.command
 def preset(self,units='in',parameters=None):
  self.fixture(units);f=unit_scale('in',units);j=self.b.session.project;op=j.operations[0]
  op.recipe_metadata={};op.tab_count=4;op.tab_width=.2*f;op.tab_height=.05*f;op.geometry_selector={'tag':'original'}
  self.cut=dict(entry_mode='ramp',entry_side='automatic_waste',lead_in=.3,lead_out=.1,cut_intent='through',target_depth=.999,breakthrough=.02,safe_z=.3,pass_depths=[.1,.1],finishing_pass=True,allowance=.01,finish_axial_stock=.01,finish_feed=90,finish_spindle_rpm=16000,helix_feed=20)
  if parameters is not None:self.cut=parameters
  data=self.b.manufacturing_tools.read();data['tools'][0]['recipes'][0].update(cut_parameters=copy.deepcopy(self.cut),entry_strategies=['ramp','plunge','helical']);self.b.manufacturing_tools.write(data)
  self.b.session._record_history();self.b.sync_recovery(force=True)
 def motion(self,p=None,ack=True):
  p=p or self.choice();r=self.request('attachment_prepare',choice=p,reviewed=True,holding_reviewed=ack)['manufacturing'];deadline=time.monotonic()+25
  while r['status']=='running' and time.monotonic()<deadline:
   time.sleep(.03);r=self.request('poll')['manufacturing']
  self.assertEqual(r['status'],'operation_ready',r);return r
 def test_units_real_motion_preserve_holding_history_recovery_and_native(self):
  for units in ('in','mm','cm'):
   self.preset(units);f=unit_scale('in',units);before=self.frozen();j=self.b.session.project;opid=j.operations[0].id;tool=copy.deepcopy(j.tool_library.tools[0].to_dict());p=self.choice();review=self.draft(p)
   self.assertTrue(review['requiresHoldingReview']);self.assertEqual(before,self.frozen());changes={x['field']:x for x in review['changes']};self.assertAlmostEqual(changes['target_depth']['after'],.52*f);self.assertAlmostEqual(changes['finish_feed']['after'],1.5*f)
   self.assertFalse(changes['tab_count']['changed']);self.assertFalse(self.call('attachment_prepare',choice=p,reviewed=True)['ok']);self.assertEqual(before,self.frozen())
   motion=self.motion(p);candidate=self.b.manufacturing.group_candidate;paths,validation=preflight_bundle(copy.deepcopy(candidate),lambda _:None);self.assertFalse(validation['blocking']);self.assertEqual(motion['motions'],serialize_motion(candidate,paths));self.assertEqual(before,self.frozen())
   index=self.b.session._history_index;self.apply(motion);job=self.b.session.project;op=job.operations[0]
   self.assertEqual(op.id,opid);self.assertEqual(op.entity_ids,['body']);self.assertEqual(op.geometry_selector,{'tag':'original'});self.assertEqual(job.tool_library.tools[0].to_dict(),tool);self.assertEqual(op.tab_count,4);self.assertTrue(supported(job));self.assertAlmostEqual(op.helix_feed_override,f/3);self.assertEqual(op.recipe_metadata['cut_parameters'],self.cut)
   self.assertEqual(self.b.session._history_index,index+1);saved=copy.deepcopy(job.to_dict());self.assertEqual(self.b.recovery.read(self.b.recovery.identity)[1].to_dict(),saved)
   self.command('history.undo');self.assertEqual(self.b.session.project.to_dict(),before[0]);self.command('history.redo');self.assertEqual(self.b.session.project.to_dict(),saved)
   path=self.root/f'preset-{units}.camjob.json';self.command('project.save',path=str(path));self.command('project.open',path=str(path));self.assertEqual(CamProject.load(path).to_dict(),self.b.session.project.to_dict())
   self.assertFalse(self.call('export',token=motion['token'],reviewed=True,name='not-approved')['ok'])
 def test_requested_entry_matches_scope_without_changing_turret_recipe_loading(self):
  self.preset(parameters={'entry_mode':'ramp','lead_in':.5});data=self.b.manufacturing_tools.read();data['tools'][0]['recipes'][0]['entry_strategies']=['ramp'];self.b.manufacturing_tools.write(data)
  self.assertEqual(self.b.session.project.operations[0].entry_mode,'plunge');choice=self.choice();r=self.draft(choice);self.assertEqual(next(c for c in r['changes'] if c['field']=='entry_mode')['after'],'ramp')
  station=self.request('catalog_matches',station=1,process_role='outside_profile');row=next(x for x in station['matches'] if x['id']=='custom-guided');self.assertFalse(row['eligible']);self.assertIn('Contains operation-specific settings; use the operation editor.',row['reasons'])
 def test_tab_removal_requires_separate_ack_and_cancel_keeps_original(self):
  self.preset(parameters={'tabs_enabled':False,'tab_count':0,'tab_width':0,'tab_height':0,'remove_tabs_after':False})
  before=self.frozen();p=self.choice();r=self.draft(p);self.assertTrue(r['requiresHoldingReview']);self.assertTrue(any(c['field']=='tabs_enabled' and c['before'] is True and c['after'] is False for c in r['changes']))
  self.assertFalse(self.call('attachment_prepare',choice=p,reviewed=True,holding_reviewed=False)['ok']);motion=self.motion(p);self.request('cancel');self.assertFalse(self.call('operation_apply',token=motion['token'],reviewed=True)['ok']);self.assertEqual(before,self.frozen())
 def test_unknown_malformed_staged_or_invalid_helix_fields_are_excluded(self):
  cases=[{'future_feature':True},{'target_depth':True},{'pass_depths':[True]},{'pass_depths':'0.1'},{'manual_tab_positions':[.5,.5]},{'tab_cleanup_retain':2},{'helix_min_diameter':.1},{'helix_finish_revolutions':1.5},{'finishing_pass':1},{'cut_intent':'future'},{'finish_feed':0},{'tabs_enabled':True,'tab_count':0,'tab_mode':'even'},{'second_tool':{'enabled':True}}]
  for cut in cases:
   self.preset(parameters=cut);before=self.frozen();r=self.choices();row=next(x for x in r['matches'] if x['id']=='custom-guided');self.assertFalse(row['eligible'],cut);p=dict(operation_id=r['operation_id'],token=r['token'],id=row['id'],recipe=row['recipe']);self.assertFalse(self.call('attachment_review',**p)['ok']);self.assertEqual(before,self.frozen())
 def test_disabled_second_tool_and_zero_schema_fields_preserve_without_enabling_groups(self):
  inactive=dict(schema='second-tool-settings-v1',kind='profile',enabled=False,tool=None,values={k:None for k in FIELDS['profile']},strategy='Contour rest / retain skin',retain_tabs=2)
  self.preset(parameters={'second_tool':inactive,'tab_cleanup_retain':None,'helix_min_diameter':0,'helix_ramp_angle':0,'helix_clearance':0,'helix_finish_revolutions':0})
  r=self.draft();self.assertEqual(r['recipe']['cut_parameters']['second_tool'],inactive);self.assertFalse(r['requiresHoldingReview']);self.assertEqual(len(self.b.session.project.operations),1)
 def test_stock_library_and_post_edit_readiness_invalidation(self):
  self.preset();p=self.choice();r=self.motion(p);before=self.frozen();d=self.b.manufacturing_tools.read();d['tools'][0]['recipes'][0]['cut_parameters']['lead_in']=.7;self.b.manufacturing_tools.write(d);self.assertFalse(self.call('operation_apply',token=r['token'],reviewed=True)['ok']);self.assertEqual(before,self.frozen())
  self.preset();self.apply(self.motion());job=self.b.session.project;job.operations[0].lead_in+=.1
  r=self.request('guided_readiness')['review']['operations'][0];self.assertTrue(any(c['key']=='preset' and not c['passed'] for c in r['checks']))
 def test_cut_line_rejects_closed_holding_and_supports_partial_pass_recipe(self):
  self.preset(parameters={'entry_mode':'ramp','lead_in':.5,'cut_intent':'partial','target_depth':.2,'pass_depths':[.1,.1],'tabs_enabled':False});j=self.b.session.project;op=j.operations[0];op.tab_count=0;op.tab_width=op.tab_height=0;op.side=OffsetSide.ON;op.recipe_metadata={};j.entities=[Line(start=Point(1,1),end=Point(5,1),id='body')]
  d=self.b.manufacturing_tools.read();d['tools'][0]['recipes'][0]['process_roles']=['any'];self.b.manufacturing_tools.write(d)
  self.apply(self.motion());op=self.b.session.project.operations[0];self.assertEqual(op.cut_intent,'partial');self.assertEqual(op.target_depth,.2);self.assertEqual(op.pass_depths,[.1,.1]);self.assertTrue(supported(self.b.session.project))
  d=self.b.manufacturing_tools.read();d['tools'][0]['recipes'][0]['cut_parameters']['finishing_pass']=True;self.b.manufacturing_tools.write(d);self.assertFalse(self.call('attachment_review',**self.choice())['ok'])

if __name__=='__main__':unittest.main()

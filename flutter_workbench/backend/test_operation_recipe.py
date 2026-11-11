import copy,json,time,unittest
from pathlib import Path
import test_guided_readiness as readiness
from manufacturing_support import supported,PROFILE_FIELDS,PROFILE_ADVANCED,serialize_motion,preflight_bundle
from cam_core.model import CamProject,Line,Point,OffsetSide
from cam_core.units import unit_scale
from operation_recipe import attached

class OperationRecipeTests(unittest.TestCase):
 setUp=readiness.GuidedReadinessTests.setUp
 fixture=readiness.GuidedReadinessTests.fixture
 frozen=readiness.GuidedReadinessTests.frozen
 call=readiness.GuidedReadinessTests.call
 def request(self,verb,**p):
  r=self.call(verb,**p);self.assertTrue(r['ok'],r);json.dumps(r,allow_nan=False);return r
 def choices(self):return self.request('attachment_list',operation_id=self.b.session.project.operations[0].id)['review']
 def choice(self):
  r=self.choices();row=next(x for x in r['matches'] if x['eligible']);return dict(operation_id=r['operation_id'],token=r['token'],id=row['id'],recipe=row['recipe'])
 def draft(self,choice=None):
  p=choice or self.choice();return self.request('attachment_review',**p)['review']
 def motion(self,choice=None):
  choice=choice or self.choice();r=self.request('attachment_prepare',choice=choice,reviewed=True)['manufacturing'];deadline=time.monotonic()+25
  while r['status']=='running' and time.monotonic()<deadline:
   time.sleep(.03);r=self.request('poll')['manufacturing']
  self.assertEqual(r['status'],'operation_ready',r);return r
 def apply(self,r):return self.request('operation_apply',token=r['token'],reviewed=True,release_reviewed=True)
 def command(self,cmd,**p):
  r=self.b.handle(dict(command=cmd,params=p,revision=self.b.revision));self.assertTrue(r['ok'],r);return r
 def test_private_review_units_and_single_operation_scope(self):
  for units in ('in','mm','cm'):
   self.fixture(units);f=unit_scale('in',units);j=self.b.session.project;op=j.operations[0]
   op.recipe_metadata['custom_import_note']={'retain':'exact'};op.feed_rate_override=3*f
   op.stepdown=.2*f;op.pass_depths=[.1*f,.2*f];op.tab_count=4;op.tab_width=.2*f;op.tab_height=.05*f
   other=copy.deepcopy(op);other.id='other';other.sequence_role='engrave';other.recipe_metadata={};other.feed_rate_override=None;j.operations.append(other)
   self.b.session._record_history();before=self.frozen();draft=self.draft()
   self.assertEqual(before,self.frozen());self.assertEqual(draft['recipe']['_flutter_attachment']['previous_recipe'],op.recipe_metadata)
   changes={c['field']:c for c in draft['changes']};self.assertEqual(changes['feed_rate']['after'],2*f);self.assertEqual(changes['stepdown']['after'],.125*f)
   for k in ('entry_mode','target_depth','pass_depths','tab_count','tab_width','tab_height'):self.assertFalse(changes[k]['changed'])
   self.assertEqual(draft['recipe']['qualification'],'production_approved')
 def test_actual_motion_apply_undo_redo_recovery_and_native_roundtrip(self):
  for units in ('in','mm','cm'):
   self.fixture(units);j=self.b.session.project;op=j.operations[0];op.recipe_metadata={};j.tool_library.tools[0].feed_rate=1*unit_scale('in',units)
   self.b.session.selected_ids={'body'};self.b.session._record_history();self.b.sync_recovery(force=True);before=self.frozen();index=self.b.session._history_index
   r=self.motion();self.assertGreater(r['motionCount'],0);self.assertEqual(before,self.frozen());self.assertFalse(self.call('export',token=r['token'],reviewed=True,name='no-output')['ok'])
   candidate=self.b.manufacturing.group_candidate;paths,validation=preflight_bundle(copy.deepcopy(candidate),lambda _:None)
   self.assertFalse(validation['blocking']);self.assertEqual(r['motions'],serialize_motion(candidate,paths))
   self.apply(r);now=self.b.session.project;self.assertEqual([t.to_dict() for t in now.tool_library.tools],[t.to_dict() for t in j.tool_library.tools]);self.assertEqual(now.entities,j.entities)
   self.assertTrue(supported(now));self.assertTrue(attached(now.operations[0]));self.assertEqual(self.b.session._history_index,index+1);self.assertEqual(self.b.session.selected_ids,{'body'})
   saved=copy.deepcopy(now.to_dict());self.assertEqual(self.b.recovery.read(self.b.recovery.identity)[1].to_dict(),saved)
   self.command('history.undo');self.assertEqual(self.b.session.project.to_dict(),before[0]);self.command('history.redo');self.assertEqual(self.b.session.project.to_dict(),saved)
   dest=self.root/f'attached-{units}.camjob.json';self.command('project.save',path=str(dest));self.command('project.open',path=str(dest));self.assertEqual(self.b.session.project.to_dict(),CamProject.load(dest).to_dict());self.assertTrue(supported(self.b.session.project))
 def test_imported_metadata_retained_and_followup_editor_downgrades_changed_settings(self):
  old=copy.deepcopy(self.b.session.project.operations[0].recipe_metadata);r=self.motion();self.apply(r);op=self.b.session.project.operations[0]
  self.assertEqual(op.recipe_metadata['_flutter_attachment']['previous_recipe'],old)
  params={k:op.to_dict().get(k,getattr(op,k)) for k in PROFILE_FIELDS|PROFILE_ADVANCED};params.update(id=op.id,lead_in=.1)
  response=self.request('operation_prepare',command='profile_save',params=params)['manufacturing'];deadline=time.monotonic()+25
  while response['status']=='running' and time.monotonic()<deadline:
   time.sleep(.03);response=self.request('poll')['manufacturing']
  self.assertEqual(response['status'],'operation_ready',response);self.apply(response)
  changed=self.b.session.project.operations[0];self.assertEqual(changed.recipe_metadata['qualification'],'prototype');self.assertEqual(changed.recipe_metadata['_flutter_attachment']['previous_recipe'],old)
  self.assertEqual(changed.feed_rate_override,2);self.assertTrue(supported(self.b.session.project))
  self.command('history.undo');self.command('history.undo');self.assertEqual(self.b.session.project.operations[0].recipe_metadata,old)
 def test_stale_job_catalog_seed_and_invalid_choice_no_mutation(self):
  for kind in ('job','catalog','seed','index','token','shape'):
   self.fixture();p=self.choice()
   if kind=='job':self.b.session.project.operations[0].safe_z=.3
   elif kind=='catalog':
    d=self.b.manufacturing_tools.read();d['tools'][0]['recipes'][0]['feed']=121;self.b.manufacturing_tools.write(d)
   elif kind=='seed':
    path=self.root/'seed.json';path.write_text('[]');self.b.manufacturing_tools.seed=path
   elif kind=='index':p['recipe']=False
   elif kind=='shape':p['settings']={}
   else:p['token']='bad'
   before=self.frozen();self.assertFalse(self.call('attachment_review',**p)['ok']);self.assertEqual(before,self.frozen())
 def test_apply_revalidates_current_library_stock_and_acknowledgment(self):
  r=self.motion();before=self.frozen();self.assertFalse(self.call('operation_apply',token=r['token'],reviewed=False)['ok']);self.assertEqual(before,self.frozen())
  d=self.b.manufacturing_tools.read();d['tools'][0]['recipes'][0]['qualification']='retired';self.b.manufacturing_tools.write(d)
  self.assertFalse(self.call('operation_apply',token=r['token'],reviewed=True)['ok']);self.assertEqual(before,self.frozen())
  self.fixture();r=self.motion();self.b.session.project.metadata['job_setup']['material_thickness']=.6;before=self.frozen();self.assertFalse(self.call('operation_apply',token=r['token'],reviewed=True)['ok']);self.assertEqual(before,self.frozen())
 def test_disabled_linked_unsupported_identity_and_unverified_stock_rejected(self):
  for kind in ('disabled','depends','dependent','retention','center','identity','stock','special'):
   self.fixture();j=self.b.session.project;op=j.operations[0]
   if kind=='disabled':op.enabled=False
   elif kind=='depends':op.depends_on_ids=['x']
   elif kind=='dependent':
    other=copy.deepcopy(op);other.id='child';other.depends_on_ids=[op.id];j.operations.append(other)
   elif kind=='retention':op.profile_retention={'group_id':'x'}
   elif kind=='center':op.small_center_cleanout_ids=['x']
   elif kind=='identity':j.tool_library.tools[0].metadata={}
   elif kind=='stock':j.metadata['job_setup'].pop('flutter_stock_verification')
   else:op.recipe_metadata={};op.tab_release_plan={"unknown":True}
   before=self.frozen();r=self.call('attachment_list',operation_id=op.id)
   if r['ok']:
    rows=[x for x in r['review']['matches'] if x['eligible']]
    if rows:
     p=dict(operation_id=op.id,token=r['review']['token'],id=rows[0]['id'],recipe=rows[0]['recipe']);self.assertFalse(self.call('attachment_review',**p)['ok'],kind)
   self.assertEqual(before,self.frozen())
 def test_cut_line_partial_depth_and_prototype_are_not_promoted(self):
  j=self.b.session.project;op=j.operations[0];op.recipe_metadata={};op.side=OffsetSide.ON;op.cut_intent='partial';op.target_depth=.2;j.entities=[Line(start=Point(1,1),end=Point(5,1),id='body')]
  d=self.b.manufacturing_tools.read();recipe=d['tools'][0]['recipes'][0];recipe.update(qualification='prototype',process_roles=['any']);self.b.manufacturing_tools.write(d)
  r=self.motion();self.apply(r);now=self.b.session.project.operations[0];self.assertEqual(now.recipe_metadata['qualification'],'prototype');self.assertEqual(now.target_depth,.2);self.assertEqual(now.cut_intent,'partial');self.assertTrue(supported(self.b.session.project))
 def test_cancel_and_failed_prepare_revoke_previous_motion(self):
  r=self.motion();before=self.frozen();self.request('cancel');self.assertFalse(self.call('operation_apply',token=r['token'],reviewed=True)['ok']);self.assertEqual(before,self.frozen())
  r=self.motion();self.assertFalse(self.call('attachment_prepare',choice=self.choice(),reviewed=False)['ok']);self.assertFalse(self.call('operation_apply',token=r['token'],reviewed=True)['ok']);self.assertEqual(before,self.frozen())

 def test_attached_profile_cannot_bypass_review_or_silently_enter_second_tool_group(self):
  r=self.motion();self.apply(r);op=self.b.session.project.operations[0];before=self.frozen()
  params={k:op.to_dict().get(k,getattr(op,k)) for k in PROFILE_FIELDS};params['id']=op.id
  self.assertFalse(self.call('profile_save',**params)['ok']);self.assertEqual(before,self.frozen())
  result=self.request('group_prepare',id=op.id,settings={})['manufacturing'];deadline=time.monotonic()+15
  while result['status']=='running' and time.monotonic()<deadline:
   time.sleep(.03);result=self.request('poll')['manufacturing']
  self.assertEqual(result['status'],'failed');self.assertIn('complete-group requalification',result['error']);self.assertEqual(before,self.frozen())

if __name__=='__main__':unittest.main()

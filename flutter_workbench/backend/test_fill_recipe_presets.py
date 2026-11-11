import copy,time,unittest
import test_operation_recipe as attachment
from cam_core.model import IslandFillOperation,FillStrategy,CamProject,Rectangle,Point
from cam_core.units import unit_scale
from cam_core.second_tool_recipe import FIELDS
from manufacturing_support import supported,preflight_bundle,serialize_motion,FILL_FIELDS,FILL_ADVANCED

class FillRecipeTests(unittest.TestCase):
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
 def preset(self,units='in',strategy='concentric',settings=None):
  self.fixture(units);f=unit_scale('in',units);j=self.b.session.project
  j.entities[0].corner_b=Point(2*f,2*f)
  j.entities.append(Rectangle(id='island',corner_a=Point(1.4*f,1.4*f),corner_b=Point(1.6*f,1.6*f)))
  j.operations=[IslandFillOperation('Cleanout',1,['body'],.2*f,.25*f,stepdown=.125*f,stepover=.1*f,island_entity_ids=['island'],geometry_selector={'tag':'preserve'})]
  self.fill=dict(strategy=strategy,cut_intent='partial',target_depth=.25,safe_z=.3,pass_mode='equal_count',pass_count=2,pass_depths=[],bottom_finish_pass=True,bottom_finish_depth=.01,bottom_finish_feed_ipm=60,corner_tags=True,entry_mode='plunge',ramp_length=.3,helix_feed_ipm=20,helix_diameter=.15,helix_min_diameter=.05,helix_pitch=.02,helix_ramp_angle=3,helix_finish_revolutions=1,helix_clearance=0,helix_fallback='error',dwell_seconds=0,keep_tool_down=True,stay_down_max_distance=.2,stay_down_max_engagement=.1,stay_down_feed_percent=80,speed_max_full_depth_engagement=.05,speed_link_distance=.15,speed_center_feed_percent=60,speed_fallback='depth_step',spacing_mode='overlap',overlap_percent=60)
  if settings is not None:self.fill=settings
  d=self.b.manufacturing_tools.read();d['tools'][0]['recipes'][0].update(process_roles=['pocket_fill'],entry_strategies=['plunge','ramp','helical'],stepover=.1,fill_parameters=copy.deepcopy(self.fill));self.b.manufacturing_tools.write(d)
  self.b.session.selected_ids={'body','island'};self.b.session._record_history();self.b.sync_recovery(force=True)
 def motion(self,p=None):
  p=p or self.choice();r=self.request('attachment_prepare',choice=p,reviewed=True,holding_reviewed=True)['manufacturing'];deadline=time.monotonic()+35
  while r['status']=='running' and time.monotonic()<deadline:
   time.sleep(.03);r=self.request('poll')['manufacturing']
  self.assertEqual(r['status'],'operation_ready',r);return r
 def test_units_strategies_motion_history_recovery_and_native(self):
  for units,strategy in [('in','concentric'),('mm','speed_cleanout'),('cm','concentric')]:
   self.preset(units,strategy);f=unit_scale('in',units);before=self.frozen();j=self.b.session.project;tool=j.tool_library.tools[0].to_dict();opid=j.operations[0].id
   p=self.choice();r=self.draft(p);changes={v['field']:v for v in r['changes']}
   self.assertTrue(r['requiresHoldingReview']);self.assertAlmostEqual(changes['bottom_finish_feed_override']['after'],f);self.assertAlmostEqual(changes['stepover']['after'],.1*f);self.assertTrue(changes['corner_tags']['after'])
   self.assertFalse(self.call('attachment_prepare',choice=p,reviewed=True)['ok']);self.assertEqual(self.frozen(),before)
   motion=self.motion(p);candidate=self.b.manufacturing.group_candidate;paths,v=preflight_bundle(copy.deepcopy(candidate),lambda _:None);self.assertFalse(v['blocking']);self.assertEqual(motion['motions'],serialize_motion(candidate,paths));self.assertEqual(self.frozen(),before)
   self.assertFalse(self.call('export',token=motion['token'],reviewed=True,name='refused')['ok']);index=self.b.session._history_index;self.apply(motion)
   j=self.b.session.project;op=j.operations[0];self.assertTrue(supported(j));self.assertEqual(op.id,opid);self.assertEqual(op.island_entity_ids,['island']);self.assertEqual(op.geometry_selector,{'tag':'preserve'});self.assertEqual(op.strategy,FillStrategy(strategy));self.assertEqual(op.recipe_metadata['fill_parameters'],self.fill);self.assertEqual(j.tool_library.tools[0].to_dict(),tool)
   self.assertEqual(self.b.session.selected_ids,{'body','island'});self.assertEqual(self.b.session._history_index,index+1);saved=copy.deepcopy(j.to_dict());self.assertEqual(self.b.recovery.read(self.b.recovery.identity)[1].to_dict(),saved)
   self.command('history.undo');self.assertEqual(self.b.session.project.to_dict(),before[0]);self.command('history.redo');self.assertEqual(self.b.session.project.to_dict(),saved)
   path=self.root/f'fill-{units}.camjob.json';self.command('project.save',path=str(path));self.command('project.open',path=str(path));self.assertEqual(CamProject.load(path).to_dict(),self.b.session.project.to_dict());self.assertTrue(supported(self.b.session.project))
 def test_through_depth_and_disabled_second_tool_frozen(self):
  disabled=dict(schema='second-tool-settings-v1',kind='fill',enabled=False,tool=None,spacing='Overlap percentage',values={k:None for k in FIELDS['fill']})
  self.preset(settings=dict(cut_intent='through',target_depth=9,breakthrough=.015,second_tool=disabled));r=self.draft();changes={x['field']:x for x in r['changes']};self.assertAlmostEqual(changes['target_depth']['after'],.515);self.assertEqual(r['recipe']['fill_parameters']['second_tool'],disabled);self.assertTrue(r['requiresHoldingReview'])
 def test_invalid_and_cross_family_presets_leave_document_unchanged(self):
  bad=[{'unknown':1},{'pass_count':True},{'bottom_finish_pass':1},{'helix_ramp_angle':'bad'},{'pass_depths':[0]}, {'helix_fallback':'waste_plunge'}, {'strategy':'parallel'}, {'spacing_mode':'overlap'}, {'second_tool':{'enabled':True}}]
  for settings in bad:
   self.preset(settings=settings)
   if settings=={'spacing_mode':'overlap'}:
    d=self.b.manufacturing_tools.read();d['tools'][0]['recipes'][0].pop('stepover');self.b.manufacturing_tools.write(d)
   before=self.frozen();rows=self.choices()['matches'];self.assertFalse(any(r['eligible'] for r in rows),settings);self.assertEqual(self.frozen(),before)
  self.preset();d=self.b.manufacturing_tools.read();d['tools'][0]['recipes'][0]['cut_parameters']={'lead_in':.1};self.b.manufacturing_tools.write(d);self.assertFalse(any(r['eligible'] for r in self.choices()['matches']))
 def test_current_catalog_stock_and_stale_apply_guards(self):
  self.preset();p=self.choice();self.b.session.project.operations[0].stepover=.09;before=self.frozen();self.assertFalse(self.call('attachment_review',**p)['ok']);self.assertEqual(self.frozen(),before)
  self.preset();r=self.motion();d=self.b.manufacturing_tools.read();d['tools'][0]['recipes'][0]['fill_parameters']['corner_tags']=False;self.b.manufacturing_tools.write(d);before=self.frozen();self.assertFalse(self.call('operation_apply',token=r['token'],reviewed=True,release_reviewed=True)['ok']);self.assertEqual(self.frozen(),before)
 def test_linked_and_second_tool_sources_remain_protected(self):
  for kind in ('dependent','linked','disabled'):
   self.preset();op=self.b.session.project.operations[0]
   if kind=='linked':op.profile_retention={'group_id':'protected'}
   elif kind=='disabled':op.enabled=False
   else:
    child=copy.deepcopy(op);child.id='child';child.depends_on_ids=[op.id];self.b.session.project.operations.append(child)
   before=self.frozen();self.assertFalse(self.call('attachment_list',operation_id=op.id)['ok']);self.assertEqual(before,self.frozen())
 def test_followup_edit_preserves_recipe_fields_and_downgrades_qualification(self):
  self.preset();self.apply(self.motion());op=self.b.session.project.operations[0]
  params={k:op.to_dict().get(k,getattr(op,k)) for k in FILL_FIELDS|FILL_ADVANCED};params.update(id=op.id,safe_z=.35)
  # Older native forms omit these values; they must survive their next edit.
  for k in ('cut_intent','breakthrough','corner_tags'):params.pop(k)
  self.assertFalse(self.call('fill_save',**params)['ok'])
  r=self.request('operation_prepare',command='fill_save',params=params)['manufacturing'];deadline=time.monotonic()+35
  while r['status']=='running' and time.monotonic()<deadline:
   time.sleep(.03);r=self.request('poll')['manufacturing']
  self.assertEqual(r['status'],'operation_ready',r);self.apply(r);changed=self.b.session.project.operations[0]
  self.assertEqual(changed.recipe_metadata['qualification'],'prototype');self.assertTrue(changed.corner_tags);self.assertEqual(changed.cut_intent,'partial');self.assertEqual(changed.recipe_metadata['fill_parameters'],self.fill);self.assertTrue(supported(self.b.session.project))
 def test_readiness_detects_preset_drift_without_mutation(self):
  self.preset();self.apply(self.motion());r=self.request('guided_readiness')['review'];self.assertEqual(r['operations'][0]['status'],'ready',r)
  self.b.session.project.operations[0].stepover=.09;before=self.frozen();r=self.request('guided_readiness')['review'];self.assertEqual(r['operations'][0]['status'],'blocked');self.assertTrue(any(c['key']=='preset' and not c['passed'] for c in r['operations'][0]['checks']));self.assertEqual(before,self.frozen())
 def test_helical_motion_and_rejected_ramp_preserve_engine_limits(self):
  self.preset();d=self.b.manufacturing_tools.read();d['tools'][0]['recipes'][0]['fill_parameters']['entry_mode']='helical';self.b.manufacturing_tools.write(d)
  before=self.frozen();r=self.motion();self.assertGreater(r['motionCount'],0);self.assertEqual(self.frozen(),before);self.apply(r)
  self.assertEqual(self.b.session.project.operations[0].entry_mode,'helical')
  self.preset();d=self.b.manufacturing_tools.read();d['tools'][0]['recipes'][0]['fill_parameters']['entry_mode']='ramp';self.b.manufacturing_tools.write(d);before=self.frozen()
  r=self.request('attachment_prepare',choice=self.choice(),reviewed=True,holding_reviewed=True)['manufacturing'];deadline=time.monotonic()+35
  while r['status']=='running' and time.monotonic()<deadline:
   time.sleep(.03);r=self.request('poll')['manufacturing']
  self.assertEqual(r['status'],'failed',r);self.assertIn('pitch/angle limit',r['error']);self.assertEqual(self.frozen(),before)
 def test_followup_depth_intent_resolves_before_core_generation(self):
  from manufacturing_support import mutate
  self.preset();op=self.b.session.project.operations[0];before=self.frozen()
  params={k:op.to_dict().get(k,getattr(op,k)) for k in FILL_FIELDS|FILL_ADVANCED};params.update(id=op.id,cut_intent='partial',target_depth=.6)
  with self.assertRaisesRegex(ValueError,'Partial'):mutate(self.b.session,'fill_save',params,reviewed_draft=True)
  params.update(cut_intent='through',target_depth=0,breakthrough=.015)
  from cam_core.automation import AutomationSession
  candidate=AutomationSession(project=copy.deepcopy(self.b.session.project));mutate(candidate,'fill_save',params,reviewed_draft=True)
  self.assertAlmostEqual(candidate.project.operations[0].target_depth,.515);self.assertEqual(candidate.project.operations[0].cut_intent,'through');self.assertEqual(self.frozen(),before)

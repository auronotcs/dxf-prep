import copy,math,unittest
import test_manufacturing_advanced as advanced
import test_router_recipe_presets as presets
from cam_core.automation import AutomationSession
from cam_core.model import Point,Polyline,OffsetSide
from cam_core.units import unit_scale
from manufacturing_support import mutate,PROFILE_FIELDS,PROFILE_ADVANCED,supported
from profile_helix import FIELDS

class ProfileHelixTests(unittest.TestCase):
 setUp=advanced.Base.setUp
 tearDown=advanced.Base.tearDown
 run_command=advanced.Base.run_command
 fixture=advanced.Base.fixture
 generate=advanced.Base.generate
 prepared=advanced.AdvancedManufacturingTests.prepared
 apply=advanced.AdvancedManufacturingTests.apply
 parity=advanced.AdvancedManufacturingTests.parity
 def settings(self,units='in'):
  self.fixture(units);f=unit_scale('in',units)
  return dict(self.params,id=self.b.session.project.operations[0].id,side='inside',entry_mode='helical',lead_in=.5*f,
    helix_diameter=.5*f,helix_min_diameter=.2*f,helix_pitch=.08*f,helix_ramp_angle=3,helix_clearance=.0001*f,helix_finish_revolutions=2,helix_fallback='strict_error')
 def draft(self,p,status='operation_ready'):
  return self.prepared('operation_prepare',dict(command='profile_save',params=p),status)
 def test_real_helix_units_angle_floor_turns_cnc_and_document_lifecycle(self):
  for units in ('in','mm'):
   p=self.settings(units);f=unit_scale('in',units);before=copy.deepcopy(self.b.session.project.to_dict());r=self.draft(p)
   self.assertEqual(before,self.b.session.project.to_dict());self.apply(r);op=self.b.session.project.operations[0]
   for key in FIELDS:self.assertEqual(getattr(op,key),p[key])
   self.assertEqual(self.b.recovery.read(self.b.recovery.identity)[1].to_dict(),self.b.session.project.to_dict())
   paths=self.parity('profile-helix-'+units)
   descents=0;floor_arcs=0
   for block in paths[op.id].blocks:
    previous=None
    for m in block.motions:
     if previous is not None and m.center is not None:
      drop=previous.z-m.end.z;radius=Point(previous.x,previous.y).distance_to(m.center)
      if drop>1e-9:
       descents+=1
       # The core emits quarter-arc helix moves; both angle and pitch bind.
       self.assertLessEqual(drop,.08*f/4+1e-8*f)
       self.assertLessEqual(drop,radius*math.pi/2*math.tan(math.radians(3))+1e-8*f)
      elif abs(drop)<1e-9 and m.end.z<0:floor_arcs+=1
     previous=m.end
   self.assertGreater(descents,0);self.assertGreaterEqual(floor_arcs,8)
 def test_reviewed_followup_preserves_omitted_controls_and_stale_draft_cannot_apply(self):
  self.apply(self.draft(self.settings()));op=self.b.session.project.operations[0]
  p={k:copy.deepcopy(op.to_dict().get(k,getattr(op,k))) for k in PROFILE_FIELDS|PROFILE_ADVANCED if k not in FIELDS};p.update(id=op.id,name='Renamed helix')
  saved={k:getattr(op,k) for k in FIELDS};r=self.draft(p);self.apply(r)
  self.assertEqual(saved,{k:getattr(self.b.session.project.operations[0],k) for k in FIELDS})
  self.assertFalse(self.b.handle(dict(command='manufacturing.profile_save',params=p,revision=self.b.revision))['ok'])
  r=self.draft(p);self.run_command('manufacturing.setup',material='Acrylic')
  self.assertFalse(self.b.handle(dict(command='manufacturing.operation_apply',params=dict(token=r['token'],reviewed=True),revision=self.b.revision))['ok'])
  self.assertFalse(self.b.handle(dict(command='manufacturing.export',params=dict(token=r['token'],reviewed=True,name='no'),revision=self.b.revision))['ok'])
 def test_invalid_helix_values_and_cut_line_are_atomic(self):
  p=self.settings();before=copy.deepcopy(self.b.session.project.to_dict());history=self.b.session._history_index
  for changes in ({'helix_min_diameter':.6},{'helix_ramp_angle':90},{'helix_ramp_angle':float('nan')},{'helix_clearance':-.1},{'helix_clearance':True},{'helix_finish_revolutions':1.5},{'helix_finish_revolutions':True},{'helix_finish_revolutions':-1}):
   with self.assertRaises(ValueError):mutate(AutomationSession(project=copy.deepcopy(self.b.session.project)),'profile_save',dict(p,**changes),reviewed_draft=True)
   self.assertEqual(before,self.b.session.project.to_dict());self.assertEqual(history,self.b.session._history_index)
  self.draft(dict(p,helix_min_diameter=.6),'failed')
  # A positive clearance can deliberately rule out a tangent orbit; never waive it.
  self.draft(dict(p,helix_clearance=.02),'failed')
  self.run_command('manufacturing.cancel');self.assertEqual(before,self.b.session.project.to_dict())
  from cam_core.model import Line
  job=copy.deepcopy(self.b.session.project);job.operations[0].side=OffsetSide.ON;job.entities=[Line(id=p['entity_ids'][0],start=Point(2,2),end=Point(8,2))]
  with self.assertRaisesRegex(ValueError,'Cut Line'):mutate(AutomationSession(project=job),'profile_save',dict(p,side='on',entry_mode='ramp'),reviewed_draft=True)
 def test_relocated_entry_details_reach_the_motion_review(self):
  self.fixture();points=[Point(2*math.sin(i*math.pi/80),5+2*math.cos(i*math.pi/80)) for i in range(81)]+[Point(x,y) for x,y in ((0,0),(10,0),(10,10),(0,10))]
  job=self.b.session.project;job.entities=[Polyline(id=self.params['entity_ids'][0],points=points,closed=True)]
  p=dict(self.params,id=job.operations[0].id,target_depth=.1,start_index=10,entry_mode='helical',helix_diameter=.2,helix_min_diameter=.05,helix_pitch=.05,helix_fallback='waste_plunge')
  r=self.draft(p);details=[b['details'] for b in r['blockReview']]
  moved=next(d for d in details if d.get('entry_adjustment_kind')=='straight_interior')
  self.assertEqual(moved['entry_mode_actual'],'helical');self.assertNotEqual(moved['entry_original_start'],moved['entry_effective_start']);self.assertIn('own contour',moved['entry_rejection_reason'])
 def test_cut_recipe_helix_conversion_preservation_and_readiness(self):
  x=presets.RouterRecipePresetTests();x.setUp();self.addCleanup(x.doCleanups)
  cut=dict(entry_mode='helical',lead_in=.5,helix_diameter=.5,helix_min_diameter=.2,helix_pitch=.08,helix_ramp_angle=3,helix_clearance=.0001,helix_finish_revolutions=2,helix_fallback='strict_error',tabs_enabled=False)
  for units in ('in','mm','cm'):
   x.preset(units,cut);f=unit_scale('in',units);x.b.session.project.operations[0].side=OffsetSide.INSIDE
   data=x.b.manufacturing_tools.read();data['tools'][0]['recipes'][0]['process_roles']=['any'];x.b.manufacturing_tools.write(data)
   review=x.draft();changes={c['field']:c['after'] for c in review['changes']}
   self.assertEqual(changes['helix_ramp_angle'],3);self.assertEqual(changes['helix_finish_revolutions'],2);self.assertAlmostEqual(changes['helix_min_diameter'],.2*f);self.assertAlmostEqual(changes['helix_clearance'],.0001*f)
   motion=x.motion();self.assertGreater(motion['blockCount'],0);self.assertTrue(motion['blockReview']);x.apply(motion);op=x.b.session.project.operations[0]
   self.assertEqual(op.recipe_metadata['cut_parameters'],cut);self.assertTrue(supported(x.b.session.project))
   p={k:copy.deepcopy(op.to_dict().get(k,getattr(op,k))) for k in PROFILE_FIELDS|PROFILE_ADVANCED if k not in FIELDS};p.update(id=op.id,name='Reviewed follow-up')
   # Existing recipe records and non-default helix fields survive routine edits.
   session=AutomationSession(project=copy.deepcopy(x.b.session.project));mutate(session,'profile_save',p,reviewed_draft=True)
   edited=session.project.operations[0]
   self.assertEqual({k:getattr(op,k) for k in FIELDS},{k:getattr(edited,k) for k in FIELDS});self.assertEqual(edited.recipe_metadata['cut_parameters'],cut)
   x.b.session.project.operations[0].helix_ramp_angle=4
   checks=x.request('guided_readiness')['review']['operations'][0]['checks'];self.assertTrue(any(c['key']=='preset' and not c['passed'] for c in checks))

if __name__=='__main__':unittest.main()

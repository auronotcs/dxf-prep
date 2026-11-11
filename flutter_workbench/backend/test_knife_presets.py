import copy,json
from unittest.mock import patch
from pathlib import Path
from test_manufacturing_special import SpecialManufacturingTests as Base
from cam_core.model import DragKnifeOperation,Tool,ToolKind,Point,KeepoutZone
from knife_presets import KnifePresets
from manufacturing_support import supported

class KnifePresetTests(Base):
 def knife_fixture(self,units='in'):
  self.empty_fixture(units);f=25.4 if units=='mm' else 1;job=self.b.session.project
  job.tool_library.tools.append(Tool(8,'Knife',.1*f,kind=ToolKind.DRAG_KNIFE,spindle_mode='disabled',feed_rate=f,plunge_rate=.25*f))
  self.knife=dict(kind='drag_knife',name='Knife',tool_station=8,entity_ids=[job.entities[0].id],target_depth=.1*f,safe_z=.25*f,
    blade_offset=.05*f,swivel_depth=.005*f,swivel_angle=20,approach_height=.05*f,alignment_length=.15*f,curve_tolerance=.001*f,
    passes=1,reverse_paths=False,setup_verified=True,outside_corner_loops=True,loop_radius=.2*f,loop_feed_percent=50,
    edge_aware_corners=False,corner_feed_ipm=20,lead_in_feed_ipm=25,feed_rate_override=1.2*f,plunge_rate_override=.3*f)
  job.operations=[DragKnifeOperation(**{k:v for k,v in self.knife.items() if k!='kind'})];self.freeze()
 def listing(self):return self.run_command('manufacturing.knife_list')
 def action(self,action,listing=None,**p):
  listing=listing or self.listing()
  return self.run_command('manufacturing.knife_'+action,version=listing['version'],fingerprint=listing['fingerprint'],**p)
 def save(self):return self.action('save',operation_id=self.b.session.project.operations[0].id,name='Plywood knife')
 def test_saved_presets_canonical_rates_and_private_reviewed_load_in_both_units(self):
  for units in ('in','mm'):
   self.knife_fixture(units);self.b.knife_presets.path=self.root/('recipes-'+units+'.json')
   before=copy.deepcopy(self.b.session.project.to_dict());revision=self.b.revision
   saved=self.save();record=saved['recipes'][0]
   self.assertAlmostEqual(record['values']['feed_rate_override'],72);self.assertAlmostEqual(record['values']['blade_offset'],.05)
   self.assertEqual(record['qualification'],'prototype');self.assertNotIn('setup_verified',record['values'])
   resolved=self.action('resolve',saved,index=0,station=8)['values'];f=25.4 if units=='mm' else 1
   self.assertAlmostEqual(resolved['feed_rate_override'],1.2*f);self.assertEqual(resolved['corner_feed_ipm'],20)
   self.assertFalse(resolved['setup_verified']);self.assertEqual(before,self.b.session.project.to_dict());self.assertEqual(revision,self.b.revision)
   self.special(dict(self.knife,**resolved,id=self.b.session.project.operations[0].id),'failed')
 def test_import_atomic_duplicate_invalid_and_source_unchanged(self):
  self.knife_fixture();saved=self.save();data=self.b.knife_presets.read();data['recipes'][0]['name']='Imported'
  data['recipes'][0]['qualification']='tested';source=self.root/'source.json';source.write_text(json.dumps(data));raw=source.read_bytes()
  imported=self.action('import',saved,path=str(source));self.assertEqual(len(imported['recipes']),2)
  self.assertEqual(imported['recipes'][1]['qualification'],'prototype');self.assertEqual(raw,source.read_bytes())
  stable=self.b.knife_presets.path.read_bytes()
  for payload in (data,{'schema':'wrong','recipes':[]},dict(data,recipes=[dict(data['recipes'][0],name='New',values={})])):
   source.write_text(json.dumps(payload))
   result=self.b.handle(dict(command='manufacturing.knife_import',params=dict(version=imported['version'],fingerprint=imported['fingerprint'],path=str(source)),revision=self.b.revision))
   self.assertFalse(result['ok']);self.assertEqual(stable,self.b.knife_presets.path.read_bytes())
 def test_scope_stale_job_library_missing_knife_and_atomic_failure(self):
  self.knife_fixture();saved=self.save();stable=self.b.knife_presets.path.read_bytes()
  with patch.object(Path,'replace',side_effect=OSError('Write denied')):
   with self.assertRaises(OSError):self.b.knife_presets.command('knife_save',dict(version=saved['version'],fingerprint=saved['fingerprint'],name='Another',operation_id=self.b.session.project.operations[0].id),self.b.session.project)
  self.assertEqual(stable,self.b.knife_presets.path.read_bytes())
  with self.b.knife_presets.locked():
   with self.assertRaisesRegex(ValueError,'Another drawing'):KnifePresets(self.b.knife_presets.path).command('knife_save',{},self.b.session.project)
  for field,value in [('material','Acrylic'),('material_thickness',.75)]:
   original=self.b.session.project.metadata['job_setup'][field];self.b.session.project.metadata['job_setup'][field]=value
   self.assertFalse(self.listing()['recipes'][0]['compatible'])
   with self.assertRaisesRegex(ValueError,'changed'):self.b.knife_presets.command('knife_resolve',dict(version=saved['version'],fingerprint=saved['fingerprint'],index=0,station=8),self.b.session.project)
   self.b.session.project.metadata['job_setup'][field]=original
  self.b.session.project.tool_library.tools[-1].name='Different knife'
  self.assertFalse(self.listing()['recipes'][0]['compatible'])
 def test_loops_feeds_actual_motion_save_history_and_output_units(self):
  for units in ('in','mm'):
   self.knife_fixture(units);job=self.b.session.project;opid=job.operations[0].id
   draft=self.special(dict(self.knife,id=opid));self.apply(draft)
   self.assertTrue(supported(self.b.session.project));self.parity('knife-loops-'+units)
   op=self.b.session.project.operations[0];self.assertEqual(op.corner_feed_ipm,20);self.assertTrue(op.outside_corner_loops)
   f=25.4 if units=='mm' else 1
   self.apply(self.special(dict(self.knife,id=opid,feed_rate_override=1.5*f,corner_feed_ipm=None,lead_in_feed_ipm=None)))
   self.assertAlmostEqual(self.b.session.project.operations[0].feed_rate_override,1.5*f)
   self.assertIsNone(self.b.session.project.operations[0].corner_feed_ipm)
 def test_edge_aware_review_required_for_apply_and_output(self):
  self.knife_fixture();draft=self.special(dict(self.knife,id=self.b.session.project.operations[0].id,edge_aware_corners=True))
  self.assertTrue(draft['requiresKnifeReview'])
  args=dict(token=draft['token'],reviewed=True)
  self.assertFalse(self.b.handle(dict(command='manufacturing.operation_apply',params=args,revision=self.b.revision))['ok'])
  self.run_command('manufacturing.operation_apply',**args,knife_reviewed=True)
  ready=self.generate();self.assertTrue(ready['requiresKnifeReview'])
  params=dict(token=ready['token'],reviewed=True,name='edge-knife')
  self.assertFalse(self.b.handle(dict(command='manufacturing.export',params=params,revision=self.b.revision))['ok'])
  self.run_command('manufacturing.export',**params,knife_reviewed=True)
 def test_management_draft_surfaces_existing_knife_review_requirement(self):
  self.knife_fixture();op=self.b.session.project.operations[0];op.edge_aware_corners=True
  draft=self.workflow('sequence',dict(id=op.id,sequence_role='automatic',sequence_priority=1,sequence_locked=False,depends_on_ids=[]))
  self.assertTrue(draft['requiresKnifeReview'])
  params=dict(token=draft['token'],reviewed=True)
  before=copy.deepcopy(self.b.session.project.to_dict())
  self.assertFalse(self.b.handle(dict(command='manufacturing.management_apply',params=params,revision=self.b.revision))['ok'])
  self.assertEqual(before,self.b.session.project.to_dict())
  self.run_command('manufacturing.management_apply',**params,knife_reviewed=True)
  self.assertEqual(self.b.session.project.operations[0].sequence_priority,1)
 def test_invalid_loop_and_feed_rejected_without_document_mutation(self):
  self.knife_fixture();before=copy.deepcopy(self.b.session.project.to_dict())
  for settings in ({'loop_radius':.01},{'corner_feed_ipm':0},{'lead_in_feed_ipm':True},{'edge_aware_corners':'yes'}):
   self.special(dict(self.knife,**settings,id=self.b.session.project.operations[0].id),'failed')
   self.assertEqual(before,self.b.session.project.to_dict())
 def test_actual_plate_edge_fallback_is_reported_and_does_not_bypass_clamps(self):
  for units in ('in','mm'):
   self.knife_fixture(units);f=25.4 if units=='mm' else 1
   self.run_command('manufacturing.setup',width=10*f,height=10*f)
   job=self.b.session.project;job.entities[0].corner_b=Point(9.85*f,6*f)
   params=dict(self.knife,id=job.operations[0].id)
   self.special(params,'failed')
   draft=self.special(dict(params,edge_aware_corners=True))
   self.assertTrue(any(b['details'].get('edge_swivels') for b in draft['blockReview']))
   job.keepouts=[KeepoutZone(name='Fixture',clearance_z=.5*f,points=[Point(x*f,y*f) for x,y in [(9.88,1.96),(9.98,1.96),(9.98,2.04),(9.88,2.04)]])]
   self.special(dict(params,edge_aware_corners=True),'failed')

for name in dir(Base):
 if name.startswith('test_'):setattr(KnifePresetTests,name,None)

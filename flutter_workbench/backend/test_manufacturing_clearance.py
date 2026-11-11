import copy
from test_knife_presets import KnifePresetTests as Base
from cam_core.model import Point,KeepoutZone,Rectangle
from cam_core.drag_knife import generate_drag_knife

class ClearanceTests(Base):
 def edge(self,units='in'):
  self.knife_fixture(units);f=25.4 if units=='mm' else 1
  self.run_command('manufacturing.setup',width=10*f,height=10*f)
  job=self.b.session.project;job.entities[0].corner_b=Point(9.85*f,6*f)
  return job,dict(self.knife,id=job.operations[0].id)
 def test_blocked_loop_keeps_locatable_geometry_and_retry_parameters_both_units(self):
  for units in ('in','mm'):
   job,params=self.edge(units);before=copy.deepcopy(job.to_dict())
   result=self.special(params,'failed');rows=result['knifeFindings'];self.assertTrue(rows)
   f=rows[0];self.assertFalse(f['accepted']);self.assertEqual(f['entity_id'],job.entities[0].id)
   self.assertTrue(f['outline']);self.assertTrue(f['source']);self.assertEqual(f['units'],units)
   self.assertEqual(result['retryOperation'],dict(command='special_save',params=params));self.assertEqual(before,job.to_dict())
   self.assertFalse(self.b.handle(dict(command='manufacturing.operation_apply',params=dict(token=result.get('token'),reviewed=True),revision=self.b.revision))['ok'])
   self.run_command('manufacturing.cancel');self.assertNotIn('knifeFindings',self.b.manufacturing.result)
 def test_accepted_swivels_deduplicate_passes_and_remain_explicitly_reviewed(self):
  job,p=self.edge();result=self.special(dict(p,edge_aware_corners=True,passes=2))
  findings=result['knifeFindings'];self.assertEqual(len(findings),2)
  self.assertTrue(all(f['accepted'] and f['fallback_envelope'] for f in findings))
  self.assertTrue(result['requiresKnifeReview']);self.assertFalse(job.operations[0].edge_aware_corners)
 def test_waste_lead_failure_is_preserved_without_partial_motion_or_job_change(self):
  self.knife_fixture();job=self.b.session.project;op=job.operations[0]
  generated=generate_drag_knife(job,op);x,y=generated.blocks[0].metadata['waste_plunge']
  job.keepouts=[KeepoutZone(name='Lead clamp',clearance_z=.5,points=[Point(x-.01,y-.01),Point(x+.01,y-.01),Point(x+.01,y+.01),Point(x-.01,y+.01)])]
  before=copy.deepcopy(job.to_dict());result=self.special(dict(self.knife,id=op.id),'failed')
  self.assertIn('lead',result['knifeFindings'][0]['message'].lower());self.assertTrue(result['knifeFindings'][0]['obstacles'])
  self.assertNotIn('motions',result);self.assertEqual(before,job.to_dict())
 def test_output_blocked_and_accepted_findings_use_plate_local_frame(self):
  for units in ('in','mm'):
   job,p=self.edge(units);f=25.4 if units=='mm' else 1
   # A distant source origin must not appear in the plate-local finding.
   from cam_core.plates import Plate
   job.metadata['plates']=[Plate(1,100*f,0,10*f,10*f).to_dict(),Plate(2,200*f,0,10*f,10*f).to_dict()]
   entity=job.entities[0];entity.corner_a=Point(entity.corner_a.x+100*f,entity.corner_a.y);entity.corner_b=Point(entity.corner_b.x+100*f,entity.corner_b.y)
   other=Rectangle(id='second-knife',corner_a=Point(202*f,2*f),corner_b=Point(209.85*f,6*f))
   job.entities.append(other);job.operations[0].entity_ids.append(other.id)
   result=self.generate('blocked');finding=result['knifeFindings'][0]
   self.assertEqual({v['plate_number'] for v in result['knifeFindings']},{1,2})
   self.assertEqual(finding['plate_number'],1);self.assertEqual(finding['frame'],'Plate-local')
   self.assertLess(finding['point'][0],11*f)
   self.assertFalse(self.b.handle(dict(command='manufacturing.export',params=dict(token=result.get('token'),reviewed=True,name='blocked-clearance'),revision=self.b.revision))['ok'])
   self.b.session.project.operations[0].edge_aware_corners=True;result=self.generate()
   self.assertTrue(result['knifeFindings']);self.assertTrue(all(v['accepted'] for v in result['knifeFindings']))

for name in dir(Base):
 if name.startswith('test_'):setattr(ClearanceTests,name,None)

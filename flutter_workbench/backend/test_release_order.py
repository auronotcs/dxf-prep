import copy
from test_manufacturing_special import SpecialManufacturingTests as Base
from cam_core.model import Rectangle,Point
from manufacturing_special import release


class ReleaseOrderTests(Base):
 def release_fixture(self,units='in',locked=False):
  self.fixture(units);f=25.4 if units=='mm' else 1
  job=self.b.session.project;op=job.operations[0]
  job.entities.append(Rectangle(id='second-body',corner_a=Point(12*f,2*f),corner_b=Point(18*f,6*f)))
  op.entity_ids.append('second-body');op.tab_count=4;op.tab_width=.5*f;op.tab_height=.1*f
  if locked:op.manual_contour_order=list(op.entity_ids)
  self.freeze();return job,op

 def test_graphical_order_actual_motion_history_save_and_output_both_units(self):
  for units in ('in','mm'):
   job,op=self.release_fixture(units);before=copy.deepcopy(job.to_dict())
   draft=self.workflow('release_audit',dict(id=op.id,plate_number=1));first=draft['details']['final_release']
   order=list(reversed(first['part_order']))
   draft=self.workflow('release_audit',dict(id=op.id,plate_number=1,part_order=order,fingerprint=first['fingerprint']))
   audit=draft['details']['final_release'];self.assertEqual(audit['part_order'],order)
   self.assertNotEqual(first['warning_key'],audit['warning_key']);self.assertEqual(before,self.b.session.project.to_dict())
   self.assertFalse(self.b.handle(dict(command='manufacturing.management_apply',params=dict(token=draft['token'],reviewed=True,release_reviewed=True),revision=self.b.revision))['ok'])
   p=dict(id=op.id,plate_number=1,part_order=order,fingerprint=audit['fingerprint'],audit_key=audit['warning_key'],confirmed_parts=audit['parts'],evidence_note='SYNTHETIC independently fixed bodies',waste_warning_review=audit['warning_key'])
   stage=self.workflow('final_release',p)
   self.assertTrue(stage['requiresReleaseReview']);self.apply_workflow(stage)
   plan=self.b.session.project.operations[-1].release_plan
   self.assertEqual(plan['audit']['events'][0]['part_ids'],[order[0]])
   self.parity('graphical-release-'+units)
   path=self.root/('ordered-'+units+'.camjob.json');saved=copy.deepcopy(self.b.session.project.to_dict())
   self.run_command('project.save',path=str(path));self.run_command('project.open',path=str(path))
   self.assertEqual(saved,self.b.session.project.to_dict())

 def test_partial_duplicate_stale_and_old_order_acknowledgments_rejected(self):
  job,op=self.release_fixture();before=copy.deepcopy(job.to_dict())
  _,first=release(copy.deepcopy(job),dict(id=op.id,plate_number=1),False)
  base=dict(id=op.id,plate_number=1,fingerprint=first['fingerprint'])
  for order in ([],first['parts'][:1],[first['parts'][0]]*2,['missing',first['parts'][0]],'bad',None):
   with self.assertRaises(ValueError):release(copy.deepcopy(job),dict(base,part_order=order),False)
  with self.assertRaisesRegex(ValueError,'changed'):release(copy.deepcopy(job),dict(base,fingerprint='stale',part_order=first['parts']),False)
  order=list(reversed(first['part_order']));_,second=release(copy.deepcopy(job),dict(base,part_order=order),False)
  for key in ('',first['warning_key']):
   p=dict(base,part_order=order,audit_key=key,confirmed_parts=first['parts'],evidence_note='SYNTHETIC',waste_warning_review=second['warning_key'])
   with self.assertRaisesRegex(ValueError,'audit changed'):release(copy.deepcopy(job),p,True)
  self.assertEqual(before,self.b.session.project.to_dict())

 def test_locked_manual_order_cannot_be_rearranged(self):
  for units in ('in','mm'):
   job,op=self.release_fixture(units,locked=True)
   _,audit=release(copy.deepcopy(job),dict(id=op.id,plate_number=1),False)
   self.assertTrue(audit['order_locked']);self.assertEqual(audit['part_order'],op.entity_ids)
   with self.assertRaisesRegex(ValueError,'locked/manual'):
    release(copy.deepcopy(job),dict(id=op.id,plate_number=1,part_order=list(reversed(audit['part_order']))),False)


for name in dir(Base):
 if name.startswith('test_'):setattr(ReleaseOrderTests,name,None)

import copy
from test_manufacturing_special import SpecialManufacturingTests as Base
from cam_core.model import Point,Rectangle
from manufacturing_support import supported

class SmallCenterTests(Base):
 def centers_fixture(self,units='in'):
  self.fixture(units);f=25.4 if units=='mm' else 1;job=self.b.session.project
  job.entities[0].corner_b=Point(2.5*f,2.5*f)
  from cam_core.model import OffsetSide
  op=job.operations[0];op.side=OffsetSide.INSIDE;op.target_depth=.1*f;op.stepdown=.05*f
  self.freeze();return self.b.session.project.operations[0]
 def info(self,op):return self.run_command('manufacturing.small_centers',id=op.id)['manufacturing']
 def plan(self,op,ids,status='management_ready',info=None):
  info=info or self.info(op)
  return self.workflow('small_centers',dict(id=op.id,ids=ids,fingerprint=info['fingerprint']),status)
 def test_center_selection_history_preservation_output_and_clear_both_units(self):
  for units in ('in','mm'):
   op=self.centers_fixture(units);info=self.info(op);self.assertTrue(info['rows'][0]['eligible'],info)
   d=self.plan(op,op.entity_ids);self.assertEqual(d['details']['small_centers']['selected'],op.entity_ids)
   self.apply_workflow(d);self.assertTrue(supported(self.b.session.project));paths=self.parity('centers-'+units)
   blocks=paths[op.id].blocks;self.assertEqual(blocks[0].metadata['role'],'small_center_cleanout')
   self.assertTrue(any(b.metadata['role']!='small_center_cleanout' for b in blocks))
   f=25.4 if units=='mm' else 1
   params=dict(self.params,id=op.id,side='inside',target_depth=.1*f,stepdown=.05*f)
   self.apply(self.draft(params));self.assertEqual(self.b.session.project.operations[0].small_center_cleanout_ids,op.entity_ids)
   op=self.b.session.project.operations[0];self.apply_workflow(self.plan(op,[]));self.assertEqual(self.b.session.project.operations[0].small_center_cleanout_ids,[])
 def test_invalid_stale_duplicate_and_non_target_centers_are_atomic(self):
  op=self.centers_fixture();info=self.info(op);before=copy.deepcopy(self.b.session.project.to_dict())
  for ids in ([op.entity_ids[0],op.entity_ids[0]],['missing'],'bad',None):
   self.plan(op,ids,'failed');self.assertEqual(before,self.b.session.project.to_dict())
  self.draft(dict(self.params,id=op.id,side='inside',target_depth=.1,stepdown=.05,small_center_cleanout_ids=None),'failed')
  self.assertEqual(before,self.b.session.project.to_dict())
  self.b.session.project.operations[0].name='Changed';self.plan(op,op.entity_ids,'failed',info)
 def test_large_outside_and_protected_artwork_are_ineligible(self):
  op=self.centers_fixture();job=self.b.session.project
  job.entities[0].corner_b=Point(6,6);self.assertFalse(self.info(op)['rows'][0]['eligible'])
  job.entities[0].corner_b=Point(2.5,2.5)
  job.entities.append(Rectangle(id='protected',corner_a=Point(2.2,2.2),corner_b=Point(2.3,2.3)))
  self.assertFalse(self.info(op)['rows'][0]['eligible']);self.plan(op,op.entity_ids,'failed')
  job.entities.pop();from cam_core.model import OffsetSide
  op.side=OffsetSide.OUTSIDE;self.assertFalse(self.info(op)['rows'][0]['eligible'])
 def test_imported_valid_centers_are_supported_but_second_tool_conversion_cannot_drop_them(self):
  op=self.centers_fixture();self.apply_workflow(self.plan(op,op.entity_ids));op=self.b.session.project.operations[0]
  self.assertTrue(supported(self.b.session.project))
  self.run_command('manufacturing.tool_save',station=2,name='Smaller',diameter=.125,spindle_rpm=18000,feed_rate=1,plunge_rate=.25,max_stepdown=.04,cutting_edge_length=.75)
  self.prepared('group_prepare',dict(id=op.id,settings=dict(station=2,feed_rate=1,plunge_rate=.25,spindle_rpm=18000,stepdown=.04,safe_z=.25,skin=.025,ignore_stock_less_than=.001)), 'failed')
  self.assertEqual(self.b.session.project.operations[0].small_center_cleanout_ids,op.entity_ids)

for name in dir(Base):
 if name.startswith('test_'):setattr(SmallCenterTests,name,None)

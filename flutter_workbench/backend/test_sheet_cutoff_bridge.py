import copy
from test_manufacturing_advanced import AdvancedManufacturingTests as Base
from cam_core.model import Point,Rectangle,Line,RouterOffsetOperation,OffsetSide
from cam_core.plates import make_automatic_cutoff_line
from cam_core.automation import _project_plates


class SheetCutoffBridgeTests(Base):
 def prepare(self,**p):
  return self.run_command('design.cutoff_preview',**dict(dict(scope='selected',plate_number=1,orientation='vertical',clearance=.5),**p))['preview']
 def reject(self,command,**p):
  before=copy.deepcopy(self.b.session.project.to_dict());index=self.b.session._history_index
  result=self.b.handle(dict(command=command,params=p,revision=self.b.revision));self.assertFalse(result['ok'],result)
  self.assertEqual(before,self.b.session.project.to_dict());self.assertEqual(index,self.b.session._history_index)
  return result
 def simple(self,units='in'):
  self.run_command('project.new',units=units);f=25.4 if units=='mm' else 1
  self.run_command('plate.define',plates=[dict(number=i+1,x=i*20*f,y=0,width=20*f,height=10*f,name=f'Sheet {i+1}') for i in range(3)])
  self.run_command('geometry.add_rectangle',x=1*f,y=1*f,width=7*f,height=5*f)
  self.run_command('geometry.add_rectangle',x=21*f,y=1*f,width=9*f,height=7*f)
  self.b.session.project.entities[1].layer='Hidden';self.run_command('layer.set',name='Hidden',visible=False)
  return f
 def test_multi_sheet_preview_units_hidden_geometry_and_one_undo_save(self):
  for units in ('in','mm'):
   f=self.simple(units);before=copy.deepcopy(self.b.session.project.to_dict());selected=set(self.b.session.selected_ids);index=self.b.session._history_index
   p=self.prepare(scope='all',orientation='horizontal',clearance=.5*f)
   self.assertEqual([x['action'] for x in p['rows']],['Add','Add','Skipped'])
   self.assertEqual([x['line']['start']['y']/f for x in p['rows'][:2]],[6.5,8.5])
   self.assertEqual(before,self.b.session.project.to_dict());self.assertEqual(selected,self.b.session.selected_ids)
   self.run_command('design.cutoff_apply',token=p['token']);self.assertEqual(index+1,self.b.session._history_index)
   after=copy.deepcopy(self.b.session.project.to_dict());ids={x['line']['id'] for x in p['rows'][:2]}
   self.assertEqual(ids,self.b.session.selected_ids);self.assertFalse(self.b.session.project.operations)
   self.run_command('history.undo');self.assertEqual(before,self.b.session.project.to_dict())
   self.run_command('history.redo');self.assertEqual(after,self.b.session.project.to_dict())
   path=self.root/(units+'-cutoff.camjob.json');self.run_command('project.save',path=str(path));self.run_command('project.open',path=str(path));self.assertEqual(after,self.b.session.project.to_dict())
   p=self.prepare(plate_number=2,clearance=.25*f);self.assertEqual(len(p['rows']),1);self.assertEqual(p['rows'][0]['action'],'Replace')
   self.assertIn(p['rows'][0]['line']['id'],ids);self.assertAlmostEqual(p['rows'][0]['line']['start']['x']/f,30.25)

 def test_invalid_stale_superseded_and_empty_drafts_are_atomic(self):
  self.simple()
  for p in (dict(clearance=-1),dict(clearance=True),dict(clearance=float('nan')),dict(orientation='diagonal'),dict(scope='bad'),dict(plate_number=999)):
   self.reject('design.cutoff_preview',**dict(dict(scope='selected',plate_number=1,orientation='vertical',clearance=.5),**p))
  one=self.prepare();two=self.prepare(orientation='horizontal');self.reject('design.cutoff_apply',token=one['token'])
  self.run_command('selection.clear');self.reject('design.cutoff_apply',token=two['token'])
  p=self.prepare(plate_number=3);self.assertIsNone(p['token']);self.reject('design.cutoff_apply',token=None)

 def test_locked_layers_and_full_sheet_skips_never_unlock_or_mutate(self):
  self.simple();p=self.prepare();self.run_command('design.cutoff_apply',token=p['token'])
  self.run_command('layer.set',name='Cutoff',locked=True)
  before=copy.deepcopy(self.b.session.project.to_dict());p=self.prepare(scope='all');self.assertIsNone(p['token']);self.assertIn('locked',p['rows'][0]['message']);self.assertEqual(before,self.b.session.project.to_dict())
  self.run_command('layer.set',name='Cutoff',locked=False)
  p=self.prepare(clearance=99);self.assertIsNone(p['token']);self.assertIn('not enough',p['rows'][0]['message'])

 def test_attached_cutline_stable_identity_invalidates_output_and_retains_motion_parity(self):
  for units in ('in','mm'):
   self.fixture(units);f=25.4 if units=='mm' else 1;job=self.b.session.project
   line=make_automatic_cutoff_line(job.entities,_project_plates(job)[0],.5*f);job.entities.append(line)
   op=RouterOffsetOperation('Sheet cutoff',1,[line.id],.5*f,.25*f,side=OffsetSide.ON,stepdown=.125*f)
   job.operations=[op];self.b.snapshot();self.b.session._reset_history();self.b.history_labels=['Fixture']
   old=self.generate();before=copy.deepcopy(job.to_dict());p=self.prepare(orientation='horizontal',clearance=.5*f)
   self.assertEqual(p['affected_operations'],[dict(id=op.id,name=op.name)]);self.assertEqual(p['rows'][0]['line']['id'],line.id)
   self.run_command('design.cutoff_apply',token=p['token']);self.assertEqual(self.b.manufacturing.result['status'],'idle')
   self.assertEqual(self.b.session.project.operations[0].to_dict(),op.to_dict())
   self.reject('manufacturing.export',token=old['token'],reviewed=True,name='stale')
   self.parity('cutoff-'+units)

 def test_dependent_operations_and_cabinet_jobs_remain_protected(self):
  self.fixture();job=self.b.session.project;line=make_automatic_cutoff_line(job.entities,_project_plates(job)[0],.5);job.entities.append(line)
  op=job.operations[0];op.entity_ids=[line.id];op.side=OffsetSide.ON;op.geometry_selector={'kind':'layer','layer':'Cutoff'};self.b.snapshot()
  self.reject('design.cutoff_preview',scope='all',orientation='horizontal',clearance=.5)
  self.b.session.project.operations[0].geometry_selector={};self.b.session.project.metadata['cabinet_tab_plans']={'protected':{}}
  self.reject('design.cutoff_preview',scope='all',orientation='horizontal',clearance=.5)


for name in dir(Base):
 if name.startswith('test_'):setattr(SheetCutoffBridgeTests,name,None)

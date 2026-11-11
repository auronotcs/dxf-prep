import copy,json
from test_manufacturing_management import ManufacturingManagementTests as Base
from cam_core.model import Point,Polyline,Rectangle,Circle
from cam_core.automation import AutomationSession
from cam_core.profile_group_edit import resolve_profile_group_edit
from manufacturing_support import execute,supported
from manufacturing_linked import info

class LinkedTests(Base):
    def freeze(self):
        from uuid import uuid4
        path=self.root/(uuid4().hex+'.camjob.json');self.run_command('project.save',path=str(path));self.run_command('project.open',path=str(path))
    def linked_fixture(self,units='in',dxf=False):
        self.group_fixture(units);f=25.4 if units=='mm' else 1
        job=self.b.session.project;first=job.entities[0];first.layer='CUT'
        other=copy.deepcopy(first);other.id='other';other.points=[Point(p.x+12*f,p.y) for p in other.points];job.entities.append(other)
        if dxf:
            original=copy.deepcopy(job);original.operations=[];session=AutomationSession(project=original)
            self.original_dxf=self.root/'original.dxf';execute(session,'project.export_dxf',dict(path=str(self.original_dxf)))
            self.run_command('project.open',path=str(self.original_dxf));job=self.b.session.project
            self.run_command('manufacturing.setup',material='Plywood',material_thickness=.5*f,safe_z=.25*f,width=96*f,height=48*f)
            for station,diameter in ((1,.25),(2,.125)):
                self.run_command('manufacturing.tool_save',station=station,name='Cutter',diameter=diameter*f,spindle_rpm=18000,feed_rate=f,plunge_rate=.25*f,max_stepdown=.04*f,cutting_edge_length=.75*f)
            self.run_command('manufacturing.profile_save',**dict(self.params,entity_ids=[job.entities[0].id],side='inside'))
        self.freeze();self.first,self.other=[e.id for e in self.b.session.project.entities]
        d=self.prepared('group_prepare',dict(id=self.b.session.project.operations[0].id,settings=self.settings(f),source={}), 'group_ready');self.apply(d,True)
        self.ids=[o.id for o in self.b.session.project.operations]
    def linked_params(self,**p):
        data=self.run_command('manufacturing.linked_info',id=self.ids[-1])['manufacturing']
        return dict(id=self.ids[-1],fingerprint=data['fingerprint'],**p)
    def group_source(self):return resolve_profile_group_edit(self.b.session.project,self.ids[0]).source
    def test_replace_group_targets_units_identity_undo_and_output(self):
        for units in ('in','mm'):
            self.linked_fixture(units);gid=self.b.session.project.operations[0].profile_retention['group_id']
            d=self.workflow('group_targets',self.linked_params(entity_ids=[self.other],reset_controls=False));self.apply_workflow(d)
            self.assertEqual(self.group_source().entity_ids,[self.other]);self.assertTrue(supported(self.b.session.project))
            self.assertEqual([o.id for o in self.b.session.project.operations],self.ids)
            self.assertEqual(self.b.session.project.operations[0].profile_retention['group_id'],gid);self.parity('linked-targets-'+units)
    def test_group_rule_bind_refresh_and_clear(self):
        self.linked_fixture();d=self.workflow('group_rule',self.linked_params(mode='bind',reset_controls=False));self.apply_workflow(d)
        self.assertEqual(set(self.group_source().entity_ids),{self.first,self.other});self.assertTrue(self.group_source().geometry_selector)
        job=self.b.session.project;e=copy.deepcopy(job.entities[0]);e.id='third';e.points=[Point(p.x+24,p.y) for p in e.points];job.entities.append(e);self.freeze()
        self.apply_workflow(self.workflow('group_rule',self.linked_params(mode='refresh',reset_controls=False)))
        self.assertIn('third',self.group_source().entity_ids);self.parity('linked-rule')
        self.apply_workflow(self.workflow('group_rule',self.linked_params(mode='clear',reset_controls=False)))
        self.assertFalse(self.group_source().geometry_selector);self.assertEqual(len(self.group_source().entity_ids),3)
    def test_group_contour_order_and_mixed_sides(self):
        self.linked_fixture();self.apply_workflow(self.workflow('group_targets',self.linked_params(entity_ids=[self.first,self.other],reset_controls=False)))
        self.apply_workflow(self.workflow('group_contours',self.linked_params(entity_sides={self.other:'outside'},manual_contour_order=[self.other,self.first])))
        self.assertEqual(self.group_source().manual_contour_order,[self.other,self.first]);self.assertEqual(self.group_source().entity_sides,{self.other:'outside'});self.parity('linked-contours')
        self.workflow('group_targets',self.linked_params(entity_ids=[self.first],reset_controls=False),'failed')
        self.apply_workflow(self.workflow('group_targets',self.linked_params(entity_ids=[self.first],reset_controls=True)))
        self.assertFalse(self.group_source().manual_contour_order)
    def test_whole_group_sequence_and_cycle_guard(self):
        self.linked_fixture();job=self.b.session.project
        source=self.group_source();prior=copy.deepcopy(source);prior.id='prior';prior.name='Prior independent pocket';prior.entity_ids=[self.other];prior.target_depth=.1;prior.depends_on_ids=[]
        job.operations.insert(0,prior);self.freeze()
        self.apply_workflow(self.workflow('group_sequence',self.linked_params(depends_on_ids=['prior'],sequence_role='inside_profile',sequence_priority=4,sequence_locked=False)))
        for op in self.b.session.project.operations:
            if op.id in self.ids:self.assertIn('prior',op.depends_on_ids);self.assertEqual(op.sequence_priority,4)
        self.parity('linked-sequence')
        self.workflow('group_sequence',self.linked_params(depends_on_ids=[self.ids[0]],sequence_role='automatic',sequence_priority=0,sequence_locked=False),'failed')
        job=self.b.session.project;job.operations[0].depends_on_ids=[self.ids[-1]];self.freeze()
        self.workflow('group_sequence',self.linked_params(depends_on_ids=['prior'],sequence_role='automatic',sequence_priority=0,sequence_locked=False),'failed')
    def test_linked_dxf_revision_rules_units_and_persistence(self):
        for units in ('in','mm'):
            self.linked_fixture(units,dxf=True);f=25.4 if units=='mm' else 1
            self.apply_workflow(self.workflow('group_rule',self.linked_params(mode='bind',reset_controls=False)))
            job=copy.deepcopy(self.b.session.project);job.operations=[]
            added=copy.deepcopy(job.entities[0]);added.id='added';added.points=[Point(p.x+24*f,p.y) for p in added.points];job.entities.append(added)
            revision=self.root/'revision.dxf';execute(AutomationSession(project=job),'project.export_dxf',dict(path=str(revision)))
            d=self.workflow('revision',dict(path=str(revision)));self.assertEqual(len(d['details']['linked_changes']),1);self.apply_workflow(d)
            self.assertEqual(len(self.group_source().entity_ids),3);self.assertEqual([o.id for o in self.b.session.project.operations],self.ids);self.parity('linked-revision-'+units)
            path=self.root/'revised.camjob.json';before=copy.deepcopy(self.b.session.project.to_dict());self.run_command('project.save',path=str(path));self.run_command('project.open',path=str(path));self.assertEqual(self.b.session.project.to_dict(),before)
    def test_stale_cancel_hidden_and_downstream_contracts(self):
        self.linked_fixture();p=self.linked_params(entity_ids=[self.other],reset_controls=False)
        self.run_command('manufacturing.setup',material='Acrylic');self.workflow('group_targets',p,'failed')
        d=self.workflow('group_targets',self.linked_params(entity_ids=[self.other],reset_controls=False));self.run_command('manufacturing.cancel')
        self.assertFalse(self.b.handle(dict(command='manufacturing.management_apply',params=dict(token=d['token'],reviewed=True),revision=self.b.revision))['ok'])
        self.b.session.project.entities[-1].metadata['annotation_hidden']=True;self.freeze()
        self.workflow('group_targets',self.linked_params(entity_ids=[self.other],reset_controls=False),'failed')
        self.workflow('group_targets',self.linked_params(entity_ids=[],reset_controls=False),'failed')
        self.workflow('group_rule',self.linked_params(mode='bind',reset_controls=False),'failed')

    def test_tabbed_and_hybrid_membership_replans_actual_support(self):
        from test_manufacturing_advanced import AdvancedManufacturingTests as Advanced
        for strategy in ('Tabbed profile + rest / leave X tabs','Clear waste + tabbed finish'):
            source=Advanced.group_fixture(self);job=self.b.session.project
            other=copy.deepcopy(job.entities[0]);other.id='other';other.points=[Point(p.x+12,p.y) for p in other.points];job.entities.append(other);self.freeze()
            settings=Advanced.settings(self,1,strategy)
            self.apply(self.prepared('group_prepare',dict(id=self.b.session.project.operations[0].id,settings=settings,source=source),'group_ready'),True)
            self.ids=[o.id for o in self.b.session.project.operations];gid=self.b.session.project.operations[0].profile_retention['group_id']
            d=self.workflow('group_targets',self.linked_params(entity_ids=['other'],reset_controls=False));self.apply_workflow(d)
            self.assertTrue(all(o.profile_retention['group_id']==gid for o in self.b.session.project.operations));self.assertTrue(supported(self.b.session.project))
            self.assertTrue(d['details']['linked_changes'][0]['kept_stage_ids']);self.parity('linked-'+strategy.split()[0])

    def test_disappearing_referenced_stage_refused_and_stale_apply(self):
        self.linked_fixture();job=self.b.session.project
        # Only the small cutter fits this hole, so main/rest roles disappear.
        job.entities.append(Circle(id='plain',center=Point(24,3),radius=.09))
        context=resolve_profile_group_edit(job,self.ids[0]);prior=copy.deepcopy(context.source);prior.id='dependent';prior.entity_ids=[self.other];prior.target_depth=.1;prior.depends_on_ids=[self.ids[-1]];job.operations.append(prior);self.freeze()
        failure=self.workflow('group_targets',self.linked_params(entity_ids=['plain'],reset_controls=False),'failed')
        self.assertIn('disappearing stage',failure['error'])
        self.run_command('manufacturing.operation_delete',id='dependent')
        d=self.workflow('group_targets',self.linked_params(entity_ids=[self.other],reset_controls=False));self.run_command('manufacturing.setup',material='Wood')
        before=copy.deepcopy(self.b.session.project.to_dict())
        self.assertFalse(self.b.handle(dict(command='manufacturing.management_apply',params=dict(token=d['token'],reviewed=True),revision=self.b.revision))['ok']);self.assertEqual(self.b.session.project.to_dict(),before)

    def test_two_group_revision_and_unbound_removed_source_guard(self):
        self.linked_fixture(dxf=True);job=self.b.session.project
        source=copy.deepcopy(self.group_source());source.id='second-source';source.entity_ids=[self.other];job.operations.append(source);self.freeze()
        self.apply(self.prepared('group_prepare',dict(id='second-source',settings=self.settings(),source={}), 'group_ready'),True)
        before_ids=[o.id for o in self.b.session.project.operations]
        revised=copy.deepcopy(self.b.session.project);revised.operations=[]
        # Unchanged source identities plus a harmless new independent contour.
        added=copy.deepcopy(revised.entities[0]);added.id='new';added.points=[Point(p.x+24,p.y) for p in added.points];revised.entities.append(added)
        path=self.root/'two-group-revision.dxf';execute(AutomationSession(project=revised),'project.export_dxf',dict(path=str(path)))
        d=self.workflow('revision',dict(path=str(path)));self.assertEqual(len(d['details']['linked_changes']),2);self.apply_workflow(d);self.assertEqual([o.id for o in self.b.session.project.operations],before_ids);self.parity('two-linked-revision')
        revised.entities=revised.entities[1:];missing=self.root/'missing-source.dxf';execute(AutomationSession(project=revised),'project.export_dxf',dict(path=str(missing)))
        self.workflow('revision',dict(path=str(missing)),'failed')

for name in dir(Base):
    if name.startswith('test_') and name not in LinkedTests.__dict__:setattr(LinkedTests,name,None)

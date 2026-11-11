import copy
import json
from pathlib import Path
from test_manufacturing_optimization_tabs import OptimizationTabsTests as Base
from cam_core.model import Point,Rectangle,OffsetSide
from cam_core.automation import AutomationSession
from manufacturing_support import execute,supported


class ManufacturingManagementTests(Base):
    def workflow(self,action,params=None,status='management_ready'):
        return self.prepared('management_prepare',dict(action=action,params=params or {}),status)

    def apply_workflow(self,d):
        before=copy.deepcopy(self.b.session.project.to_dict());index=self.b.session._history_index
        command='manufacturing.management_apply'
        self.assertFalse(self.b.handle(dict(command=command,params=dict(token=d['token'],reviewed=False),revision=self.b.revision))['ok'])
        self.run_command(command,token=d['token'],reviewed=True,release_reviewed=True)
        self.assertEqual(index+1,self.b.session._history_index)
        after=copy.deepcopy(self.b.session.project.to_dict())
        self.run_command('history.undo');self.assertEqual(before,self.b.session.project.to_dict())
        self.run_command('history.redo');self.assertEqual(after,self.b.session.project.to_dict())

    def test_mixed_compensation_manual_order_and_containment_units_output(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            job=self.b.session.project;outer=job.entities[0].id
            job.entities.append(Rectangle(id='hole',corner_a=Point(3*f,3*f),corner_b=Point(5*f,5*f)))
            op=job.operations[0];op.entity_ids.append('hole');self.freeze()
            p=dict(id=op.id,entity_sides={'hole':'inside',outer:'outside'},manual_contour_order=['hole',outer])
            self.apply_workflow(self.workflow('profile_controls',p));paths=self.parity('mixed-'+units)
            blocks=next(iter(paths.values())).blocks
            self.assertEqual(list(dict.fromkeys(b.metadata['entity_id'] for b in blocks)),['hole',outer])
            self.assertEqual({b.metadata['side'] for b in blocks},{'inside','outside'})
            self.workflow('profile_controls',dict(p,manual_contour_order=[outer,'hole']),'failed')
            self.workflow('profile_controls',dict(p,manual_contour_order=['hole','hole']),'failed')
            self.workflow('profile_controls',dict(p,entity_sides={'missing':'inside'}),'failed')
            # Ordinary profile editing cannot drop per-contour settings.
            self.apply(self.draft(dict(self.params,id=op.id,entity_ids=[outer,'hole'],target_depth=.4*f)))
            self.assertEqual(self.b.session.project.operations[0].entity_sides,p['entity_sides'])

    def test_independent_contour_manual_order_survives_optimizer(self):
        self.fixture();job=self.b.session.project;first=job.entities[0].id
        job.entities.append(Rectangle(id='far',corner_a=Point(12,2),corner_b=Point(15,5)))
        op=job.operations[0];op.entity_ids.append('far');self.freeze()
        self.apply_workflow(self.workflow('profile_controls',dict(id=op.id,entity_sides={},manual_contour_order=['far',first])))
        d=self.prepared('optimization_prepare',dict(enabled=True),'optimization_ready');self.reviewed_apply(d,True)
        paths=self.parity('manual-order-optimized')
        self.assertEqual(list(dict.fromkeys(b.metadata['entity_id'] for b in next(iter(paths.values())).blocks)),['far',first])

    def test_sequence_dependencies_atomic_cycles_and_editor_preservation(self):
        self.fixture();job=self.b.session.project;first=job.operations[0]
        second=copy.deepcopy(first);second.id='second';second.name='Second';job.operations.append(second);self.freeze()
        p=dict(id=second.id,sequence_role='finish_cutoff',sequence_priority=5,sequence_locked=False,depends_on_ids=[first.id])
        self.apply_workflow(self.workflow('sequence',p));self.parity('sequence')
        self.workflow('sequence',dict(p,id=first.id,depends_on_ids=[second.id]),'failed')
        self.workflow('sequence',dict(p,sequence_priority=1.5),'failed')
        self.workflow('sequence',dict(p,depends_on_ids=[first.id,first.id]),'failed')
        self.apply(self.draft(dict(self.params,id=second.id,target_depth=.4)))
        edited=next(o for o in self.b.session.project.operations if o.id==second.id)
        self.assertEqual(edited.depends_on_ids,[first.id]);self.assertEqual(edited.sequence_role,'finish_cutoff')

    def test_geometry_selector_review_expansion_clear_and_hidden_guard(self):
        self.fixture();job=self.b.session.project;opid=job.operations[0].id
        job.entities.append(Rectangle(id='extra',corner_a=Point(12,2),corner_b=Point(15,5)));self.freeze()
        d=self.workflow('associate',dict(id=opid,enabled=True))
        self.assertEqual(len(d['details']['geometry_changes'][0]['after']),2)
        self.apply_workflow(d);self.parity('associate')
        self.assertTrue(self.b.session.project.operations[0].geometry_selector)
        self.apply_workflow(self.workflow('associate',dict(id=opid,enabled=False)))
        self.assertFalse(self.b.session.project.operations[0].geometry_selector)
        self.b.session.project.entities[-1].metadata['annotation_hidden']=True;self.freeze()
        self.workflow('associate',dict(id=opid,enabled=True),'failed')

    def test_revised_dxf_draft_units_history_output_and_no_source_ownership_guard(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            self.workflow('revision',dict(path=str(self.root/'missing.dxf')),'failed')
            # Roundtrip through DXF establishes real source provenance.
            source=AutomationSession(project=copy.deepcopy(self.b.session.project));source.project.operations=[]
            path=self.root/(units+'-source.dxf');execute(source,'project.export_dxf',dict(path=str(path)))
            self.run_command('project.open',path=str(path))
            eid=self.b.session.project.entities[0].id
            self.run_command('manufacturing.setup',material='Plywood',material_thickness=.5*f,safe_z=.25*f,width=96*f,height=48*f)
            self.run_command('manufacturing.tool_save',station=1,name='Quarter',diameter=.25*f,spindle_rpm=18000,feed_rate=2*f,plunge_rate=.5*f,max_stepdown=.125*f,cutting_edge_length=.75*f)
            self.run_command('manufacturing.profile_save',**dict(self.params,entity_ids=[eid]))
            opid=self.b.session.project.operations[0].id
            self.apply_workflow(self.workflow('associate',dict(id=opid,enabled=True)))
            source.project.entities.append(Rectangle(id='new',corner_a=Point(12*f,2*f),corner_b=Point(16*f,6*f)))
            revision=self.root/(units+'-revision.dxf');execute(source,'project.export_dxf',dict(path=str(revision)))
            d=self.workflow('revision',dict(path=str(revision)));self.apply_workflow(d)
            self.assertEqual(len(self.b.session.project.operations[0].entity_ids),2)
            self.parity('revision-'+units)

    def test_import_linked_group_replans_saved_inputs_not_untrusted_seal(self):
        self.group_fixture();opid=self.b.session.project.operations[0].id
        d=self.prepared('group_prepare',dict(id=opid,settings=self.settings(),source={}), 'group_ready');self.apply(d,True)
        ids=[o.id for o in self.b.session.project.operations]
        self.b.session.project.metadata.pop('flutter_manufacturing_groups',None)
        # Imported jobs are view-only until the draft has regenerated and passed checks.
        self.assertFalse(supported(self.b.session.project))
        imported=self.root/'imported.camjob.json';imported.write_text(json.dumps(self.b.session.project.to_dict()))
        self.run_command('project.open',path=str(imported))
        d=self.workflow('import_groups');self.assertEqual(d['details']['rebuilt_groups'],1)
        self.apply_workflow(d);self.assertTrue(supported(self.b.session.project))
        self.assertEqual(ids,[o.id for o in self.b.session.project.operations]);self.parity('imported-group')
        self.b.session.project.operations[0].profile_retention['member_ids'].append('missing')
        self.workflow('import_groups',status='failed')

    def test_retained_cleanup_per_part_units_stale_and_keep_all(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            opid=self.b.session.project.operations[0].id
            p=dict(self.params,id=opid,cut_intent='through',breakthrough=.015*f,tab_count=4,tab_width=.5*f,tab_height=.1*f)
            self.apply(self.draft(p))
            d=self.workflow('retained_cleanup',dict(id=opid,count=2,plate=1))
            self.assertTrue(all(n>=2 for n in d['details']['retained_cleanup']['retained'].values()))
            self.apply_workflow(d);self.parity('retained-cleanup-'+units)
            self.workflow('retained_cleanup',dict(id=opid,count=0,plate=1),'failed')
            self.run_command('manufacturing.setup',material_thickness=.6*f)
            self.generate(expected='failed')
            self.run_command('history.undo')
            self.apply_workflow(self.workflow('keep_tabs',dict(id=opid)))
            self.assertFalse(self.b.session.project.operations[0].remove_tabs_after)
            self.assertFalse(self.b.session.project.operations[0].tab_release_plan)
            self.parity('keep-tabs-'+units)

for name in list(Base.__dict__):
    if name.startswith('test_'):setattr(ManufacturingManagementTests,name,None)

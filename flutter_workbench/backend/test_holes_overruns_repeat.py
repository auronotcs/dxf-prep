import copy
from types import SimpleNamespace
from test_manufacturing_management import ManufacturingManagementTests as Base
from cam_core.model import Point, Rectangle, CamProject
from cam_core.plates import Plate, PatchworkArea
from cam_core.automation import _project_plates
from cam_core.closed_plate_overrun import approved_plate, KEY
from cam_core.units import convert_project_units
from cam_core.toolpath import Motion, MotionKind, ToolPoint, ToolpathBlock, GeneratedToolpath
from manufacturing_keepouts import prepare_keepout,fixture_issues,localize_all_keepouts,display
from manufacturing_overruns import review,prepare

class HolesOverrunsRepeatTests(Base):
    def hole(self,f=1,**extra):
        return dict(dict(change='add',kind='hole_exclusion',name='Damaged stock',shape='rectangle',x=12*f,y=12*f,width=f,height=f,clearance_z=.5*f,extra_margin=.25*f,plate_number=1),**extra)
    def direct(self,command,**params):
        return self.b.handle(dict(command=command,params=params,revision=self.b.revision))
    def reject(self,command,**params):
        before=copy.deepcopy(self.b.session.project.to_dict());index=self.b.session._history_index
        result=self.direct(command,**params);self.assertFalse(result['ok'],result)
        self.assertEqual(before,self.b.session.project.to_dict());self.assertEqual(index,self.b.session._history_index)
    def repeat(self,**p):
        return self.run_command('design.repeat_preview',**dict(dict(columns=2,rows=1,spacing='gap',x=.25,y=.25,direction_x=1,direction_y=1,target_layer=None,plate_number=1),**p))['preview']

    def test_hole_review_edit_toggle_delete_history_units_roundtrip(self):
        for units,f in [('in',1.0),('mm',25.4)]:
            self.fixture(units)
            d=self.workflow('keepouts',self.hole(f));self.apply_workflow(d)
            z=self.b.session.project.keepouts[0];identity=z.id
            self.assertEqual(z.kind,'hole_exclusion');self.assertEqual(z.extra_margin,.25*f)
            self.apply_workflow(self.workflow('keepouts',dict(change='edit',id=identity,dx=f,extra_margin=.5*f)))
            z=self.b.session.project.keepouts[0];self.assertEqual(z.kind,'hole_exclusion');self.assertEqual(z.extra_margin,.5*f)
            self.assertAlmostEqual(z.points[0].x,13*f)
            self.parity('hole-'+units)
            path=self.root/(units+'-hole.camjob.json');self.run_command('project.save',path=str(path));before=copy.deepcopy(self.b.session.project.to_dict())
            self.run_command('project.open',path=str(path));self.assertEqual(before,self.b.session.project.to_dict())
            self.apply_workflow(self.workflow('keepouts',dict(change='toggle',id=identity,enabled=False)))
            self.apply_workflow(self.workflow('keepouts',dict(change='delete',id=identity)))
            self.assertFalse(self.b.session.project.keepouts)

    def test_hole_margin_holder_curves_transition_and_local_copy(self):
        self.fixture();job=self.b.session.project;op=job.operations[0];tool=job.tool_library.tools[0]
        prepare_keepout(job,self.hole(x=5,y=5,width=.1,height=.1,extra_margin=1))
        def check(motions):return fixture_issues(job,{op.id:GeneratedToolpath([ToolpathBlock(op.id,1,motions)])})
        line=[Motion(MotionKind.RAPID,ToolPoint(3,4.2,0)),Motion(MotionKind.LINEAR,ToolPoint(7,4.2,0))]
        self.assertTrue(any(i['code']=='fixture.envelope_collision' for i in check(line)))
        tool.holder_diameter=2;tool.stickout=.6
        line=[Motion(MotionKind.RAPID,ToolPoint(3,3.5,-.2)),Motion(MotionKind.LINEAR,ToolPoint(7,3.5,-.2))]
        self.assertTrue(any('holder envelope' in i['message'] for i in check(line)))
        arcs=[Motion(MotionKind.RAPID,ToolPoint(2,3,0)),Motion(MotionKind.ARC_CW,ToolPoint(8,3,0),center=Point(5,3))]
        self.assertTrue(any(i['code']=='fixture.envelope_collision' for i in check(arcs)))
        item=SimpleNamespace(plate=Plate(2,10,20,20,20),result=SimpleNamespace(project=copy.deepcopy(job)))
        localize_all_keepouts(job,[item]);local=item.result.project.keepouts[0]
        self.assertEqual(local.extra_margin,1);self.assertEqual(local.kind,'hole_exclusion');self.assertEqual(local.points[0],Point(-5,-15));self.assertIsNone(local.patchwork_id)
        converted=convert_project_units(job,'mm');self.assertAlmostEqual(converted.keepouts[0].extra_margin,25.4)

    def test_link_relink_follow_detach_orphan_and_save(self):
        self.fixture();job=self.b.session.project
        a=PatchworkArea(id='piece',plate_number=1,x=10,y=10,width=10,height=10,name='Scrap')
        job.metadata['patchwork_areas']=[a.to_dict()];prepare_keepout(job,self.hole());z=job.keepouts[0]
        prepare_keepout(job,dict(change='link',id=z.id,patchwork_id='piece'))
        job.metadata['patchwork_areas'][0]['x']=20
        view,issues=display(job);self.assertFalse(issues);self.assertEqual(view[0]['points'][0]['x'],22)
        self.assertEqual(job.keepouts[0].points[0].x,12)
        prepare_keepout(job,dict(change='edit',id=z.id,dx=1));self.assertEqual(job.keepouts[0].points[0].x,23)
        job.metadata['patchwork_areas'].append(dict(a.to_dict(),id='other',x=20))
        prepare_keepout(job,dict(change='link',id=z.id,patchwork_id='other'));self.assertEqual(job.keepouts[0].points[0].x,23)
        item=SimpleNamespace(plate=Plate(1,0,0,96,48),result=SimpleNamespace(project=copy.deepcopy(job)))
        localize_all_keepouts(job,[item]);self.assertIsNone(item.result.project.keepouts[0].patchwork_id)
        job.metadata['patchwork_areas']=[]
        self.assertTrue(display(job)[1]);self.assertEqual(fixture_issues(job,{})[0]['code'],'hole.patchwork_link')
        with self.assertRaises(ValueError):localize_all_keepouts(job,[item])
        job.metadata['patchwork_areas']=[dict(a.to_dict(),id='replacement',x=20)]
        prepare_keepout(job,dict(change='link',id=z.id,patchwork_id='replacement'));self.assertEqual(job.keepouts[0].points[0].x,23)
        prepare_keepout(job,dict(change='link',id=z.id,patchwork_id=None));self.assertEqual(job.keepouts[0].points[0].x,23)
        self.assertFalse(display(job)[1]);self.assertIsNone(job.keepouts[0].patchwork_anchor)

    def test_invalid_holes_preserve_old_fixture_and_reject_nonrectangles(self):
        self.fixture();job=self.b.session.project
        for p in (self.hole(extra_margin=-1),self.hole(extra_margin=float('nan')),self.hole(plate_number=True),self.hole(plate_number=999),self.hole(x=200),self.hole(width=0)):
            before=copy.deepcopy(job.to_dict())
            with self.assertRaises(ValueError):prepare_keepout(job,p)
            self.assertEqual(before,job.to_dict())
        p=self.hole();p.pop('kind');p.pop('extra_margin');p.pop('plate_number');prepare_keepout(job,p)
        self.assertNotIn('kind',job.keepouts[0].to_dict());self.assertNotIn('extra_margin',job.keepouts[0].to_dict())
        prepare_keepout(job,dict(change='edit',id=job.keepouts[0].id,dx=1));self.assertEqual(job.keepouts[0].kind,'fixture')

    def test_closed_overrun_approval_stale_revoke_and_units(self):
        for units,f in [('in',1.0),('mm',25.4)]:
            self.fixture(units);job=self.b.session.project;e=job.entities[0];e.corner_a=Point(-.1*f,2*f);self.freeze();job=self.b.session.project;e=job.entities[0]
            info=review(job);row=info['rows'][0]
            p=dict(id=e.id,plate_number=1,token=info['token'],reviewed=True)
            before=copy.deepcopy(job.to_dict());self.run_command('manufacturing.closed_overrun_assign',**p)
            job=self.b.session.project;e=job.entities[0];self.assertIsNotNone(approved_plate(e,_project_plates(job),units))
            after=copy.deepcopy(job.to_dict());self.run_command('history.undo');self.assertEqual(before,self.b.session.project.to_dict());self.run_command('history.redo');self.assertEqual(after,self.b.session.project.to_dict())
            self.reject('manufacturing.closed_overrun_assign',**p)
            job=self.b.session.project;e=job.entities[0];e.corner_a=Point(-.11*f,2*f)
            self.assertTrue(review(job)['rows'][0]['stale']);self.assertIsNone(approved_plate(e,_project_plates(job),units))
            p.update(token=review(job)['token'],plate_number=None);self.run_command('manufacturing.closed_overrun_assign',**p)
            self.assertNotIn(KEY,self.b.session.project.entities[0].metadata)

    def test_closed_overrun_complete_output_requires_separate_ack(self):
        self.fixture();job=self.b.session.project;e=job.entities[0];e.corner_a=Point(-.1,2);self.freeze();job=self.b.session.project;e=job.entities[0]
        self.run_command('manufacturing.closed_overrun_assign',id=e.id,plate_number=1,token=review(job)['token'],reviewed=True)
        d=self.generate();self.assertTrue(d['openPathOvertravel']);local=self.b.manufacturing.prepared[0].result.project
        self.assertAlmostEqual(local.entities[0].corner_a.x,-.1)
        self.reject('manufacturing.export',token=d['token'],reviewed=True,name='no-ack')
        # Failed export invalidates nothing in the source; generate a fresh reviewed output.
        d=self.generate();out=self.run_command('manufacturing.export',token=d['token'],reviewed=True,overtravel_reviewed=True,allow_negative_xy=True,name='overrun')['manufacturing']
        self.assertTrue(out['paths'])

    def test_repeat_units_gap_distance_direction_history_save(self):
        for units,f in [('in',1.0),('mm',25.4)]:
            self.run_command('project.new',units=units)
            self.run_command('geometry.add_rectangle',x=5*f,y=5*f,width=2*f,height=f)
            before=copy.deepcopy(self.b.session.project.to_dict());index=self.b.session._history_index
            p=self.repeat(columns=3,rows=2,x=.5*f,y=.25*f,direction_y=-1)
            self.assertEqual(p['count'],5);self.assertAlmostEqual(p['boxes'][1][0],7.5*f);self.assertEqual(before,self.b.session.project.to_dict())
            self.run_command('design.repeat_apply',token=p['token'],reviewed=True)
            self.assertEqual(index+1,self.b.session._history_index);after=copy.deepcopy(self.b.session.project.to_dict())
            self.assertEqual(len(set(e.id for e in self.b.session.project.entities)),6)
            self.run_command('history.undo');self.assertEqual(before,self.b.session.project.to_dict());self.run_command('history.redo');self.assertEqual(after,self.b.session.project.to_dict())
            path=self.root/(units+'-repeat.camjob.json');self.run_command('project.save',path=str(path));self.run_command('project.open',path=str(path));self.assertEqual(after,self.b.session.project.to_dict())
            self.run_command('selection.set',entity_ids=[self.b.session.project.entities[0].id]);p=self.repeat(spacing='distance',x=f,y=f,direction_x=-1)
            self.assertAlmostEqual(p['boxes'][1][0],4*f);self.assertTrue(p['overlap'])

    def test_repeat_stale_hidden_overlap_bounds_groups_layers_and_generated_guard(self):
        self.run_command('project.new');self.run_command('geometry.add_rectangle',x=95,y=1,width=1,height=1)
        first=self.b.session.project.entities[0];first.metadata['group_id']='original'
        self.run_command('layer.set',name='Copies',visible=True,locked=False)
        p=self.repeat(target_layer='Copies');self.assertTrue(p['outside']);self.assertEqual(p['geometry'][0]['layer'],'Copies');self.assertNotEqual(p['geometry'][0]['metadata']['group_id'],'original')
        self.reject('design.repeat_apply',token=p['token'],reviewed=False)
        self.run_command('selection.clear');self.reject('design.repeat_apply',token=p['token'],reviewed=True)
        self.run_command('selection.set',entity_ids=[first.id]);self.b.session.project.entities[0].metadata['dr_pan_owner']='source'
        self.reject('design.repeat_preview',columns=2,rows=1,x=.2,y=.2,plate_number=1)

    def test_pan_handoff_verifies_marks_holes_preserves_view_document_and_refuses_stale(self):
        from cam_core.dr_pans import generate_dr_pan,DrPanSettings,dr_pan_bend_marks
        from cam_core.model import Circle
        from assembly_review import dr_pans
        self.run_command('project.new')
        pan=generate_dr_pan(Rectangle(corner_a=Point(0,0),corner_b=Point(24,36)),DrPanSettings())
        for e in [pan.outline,*pan.references]:e.metadata['group_id']='pan-group'
        marks=dr_pan_bend_marks(pan.outline,pan.references,.1)
        hole=Circle(center=Point(3,4),radius=.1,metadata=dict(dr_pan_hole=True,dr_pan_owner=pan.references[0].metadata['dr_pan_owner'],group_id=pan.outline.metadata['group_id']))
        self.b.session.project.entities=[pan.outline,*pan.references,*marks,hole];self.b.snapshot()
        before=copy.deepcopy(self.b.session.project.to_dict());index=self.b.session._history_index
        info=self.run_command('design.pan_review')['review'];row=info['rows'][0]
        self.assertEqual(set(row['targets']['marks']),{e.id for e in marks});self.assertEqual(row['targets']['holes'],[hole.id])
        self.run_command('design.pan_select',token=info['token'],id=pan.outline.id,kind='marks')
        self.assertEqual(self.b.session.selected_ids,{e.id for e in marks});self.assertEqual(before,self.b.session.project.to_dict());self.assertEqual(index,self.b.session._history_index)
        self.b.session.project.entities[-1].center=Point(5,4)
        self.reject('design.pan_select',token=info['token'],id=pan.outline.id,kind='holes')
        job=self.b.session.project;job.entities.remove(next(e for e in job.entities if e.id==marks[0].id))
        info=dr_pans(job);self.assertTrue(info['rows'][0]['error']);self.assertFalse(info['rows'][0]['editable']['marks'])

    def test_cabinet_private_review_units_missing_changed_registration_no_mutation(self):
        from cam_core.model import Polyline,Circle
        from cam_core.cabinet_tab_mounting import CabinetTabPlan,CabinetTab,LetterPlane,source_fingerprint,with_saved_plan,with_applied_review
        for units,f in [('in',1.0),('mm',25.4)]:
            self.run_command('project.new',units=units);job=self.b.session.project
            source=Polyline(id='back',points=[Point(x*f,y*f) for x,y in [(0,0),(8,0),(8,7),(0,7)]],closed=True)
            hole=Circle(id='hole',center=Point(4*f,4*f),radius=.125*f);job.entities=[source,hole]
            plane=LetterPlane('back',('back','hole'),source_fingerprint(job,('back','hole')),2*f,0,Point(10*f,20*f),30,.3*f)
            plan=CabinetTabPlan('example',units,.25*f,.063*f,.02*f,.01*f,(plane,),(CabinetTab('pair','back',3*f,2*f,.5*f),))
            self.b.session.project=with_saved_plan(job,plan);self.b.snapshot();before=copy.deepcopy(self.b.session.project.to_dict());selection=set(self.b.session.selected_ids)
            r=self.run_command('design.cabinet_review')['review'];self.assertTrue(r['rows'][0]['valid']);self.assertEqual(len(r['rows'][0]['geometry']),3)
            self.assertEqual(before,self.b.session.project.to_dict());self.assertEqual(selection,self.b.session.selected_ids)
            self.reject('geometry.delete')
            self.b.session.project.entities[0].points[0]=Point(-f,0)
            before=copy.deepcopy(self.b.session.project.to_dict());r=self.run_command('design.cabinet_review')['review']
            self.assertFalse(r['rows'][0]['valid']);self.assertFalse(r['rows'][0]['geometry']);self.assertEqual(before,self.b.session.project.to_dict())
            self.b.session.project.entities[0].points[0]=Point(0*f,0*f)
            self.b.session.project=with_applied_review(self.b.session.project,plan)
            self.b.session.project.entities[-1].points[0]=Point(999,999)
            before=copy.deepcopy(self.b.session.project.to_dict());r=self.run_command('design.cabinet_review')['review'];self.assertFalse(r['rows'][0]['valid']);self.assertEqual(before,self.b.session.project.to_dict())

    def test_linked_hole_final_release_and_stock_history(self):
        from manufacturing_special import release
        self.fixture();job=self.b.session.project;op=job.operations[0]
        op.tab_count=4;op.tab_width=.5;op.tab_height=.1
        a=PatchworkArea(id='scrap',plate_number=1,x=10,y=10,width=10,height=10)
        job.metadata['patchwork_areas']=[a.to_dict()]
        prepare_keepout(job,self.hole(x=12,y=12,width=.1,height=.1,extra_margin=.2))
        z=job.keepouts[0];prepare_keepout(job,dict(change='link',id=z.id,patchwork_id='scrap'))
        # Physical hole moves into the profile; final-release prerequisite motion must reject it.
        job.metadata['patchwork_areas'][0].update(x=0,y=0)
        before=copy.deepcopy(job.to_dict())
        with self.assertRaisesRegex(ValueError,'patchwork stock'):
            release(job,dict(id=op.id,plate_number=1),False)
        from cam_core.patchwork_holes import resolved_hole_project
        detached=copy.deepcopy(resolved_hole_project(job))
        detached.keepouts[0].patchwork_id=None;detached.keepouts[0].patchwork_anchor=None
        detached.metadata.pop('patchwork_areas')
        with self.assertRaisesRegex(ValueError,'hole|Hole|keepout|Damaged'):
            release(detached,dict(id=op.id,plate_number=1),False)
        self.assertEqual(before,job.to_dict())
        # Stock movement updates a hole atomically with setup and survives Undo.
        self.run_command('project.new');job=self.b.session.project
        job.metadata['patchwork_areas']=[a.to_dict()];prepare_keepout(job,self.hole())
        prepare_keepout(job,dict(change='link',id=job.keepouts[0].id,patchwork_id='scrap'))
        self.b.snapshot();self.b.session._reset_history();self.b.history_labels=['Fixture']
        before=copy.deepcopy(job.to_dict())
        self.run_command('plate.update',number=1,x=5,y=0,reflow=False)
        self.assertEqual(self.b.session.project.keepouts[0].points[0].x,17)
        self.run_command('history.undo');self.assertEqual(before,self.b.session.project.to_dict())

    def test_offset_plate_overrun_localization_and_stock_bound_approval(self):
        from cam_core.output_workflow import prepare_plate_outputs
        self.fixture();job=self.b.session.project
        job.metadata['plates']=[Plate(2,100,50,96,48).to_dict()]
        e=job.entities[0];e.corner_a=Point(99.9,52);e.corner_b=Point(108,56)
        prepare(job,dict(id=e.id,plate_number=2,token=review(job)['token'],reviewed=True))
        item=prepare_plate_outputs(job,_project_plates(job),{2:self.root/'local.cnc'})[0]
        self.assertFalse(item.result.crossing_entity_ids)
        local=item.result.project;self.assertAlmostEqual(local.entities[0].corner_a.x,-.1)
        self.assertIsNotNone(approved_plate(local.entities[0],_project_plates(local),job.units))
        job.metadata['plates'][0]['width']=95
        self.assertIsNone(approved_plate(e,_project_plates(job),job.units));self.assertTrue(review(job)['rows'][0]['stale'])

for name in dir(Base):
    if name.startswith('test_'):setattr(HolesOverrunsRepeatTests,name,None)

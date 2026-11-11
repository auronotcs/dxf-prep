import copy
from types import SimpleNamespace
from test_manufacturing_management import ManufacturingManagementTests as Base
from manufacturing_keepouts import prepare_keepout, fixture_issues, localize_all_keepouts, validate_points
from cam_core.model import Point, Circle, Polyline, KeepoutZone
from cam_core.toolpath import Motion, MotionKind, ToolPoint, ToolpathBlock, GeneratedToolpath


class KeepoutTests(Base):
    def rectangle(self, f=1, **overrides):
        return dict(dict(change='add',name='Clamp',shape='rectangle',x=12*f,y=12*f,width=2*f,height=f,clearance_z=.5*f),**overrides)

    def test_keepout_units_history_save_output_edit_toggle_delete(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            before=copy.deepcopy(self.b.session.project.to_dict())
            d=self.workflow('keepouts',self.rectangle(f))
            self.assertEqual(before,self.b.session.project.to_dict());self.assertEqual(len(d['keepouts']),1)
            self.apply_workflow(d);z=self.b.session.project.keepouts[0];identity=z.id
            self.assertTrue(self.b.snapshot()['manufacturing']['keepoutsEditable'])
            self.parity('keepout-'+units)
            self.apply_workflow(self.workflow('keepouts',dict(change='edit',id=identity,name='Moved clamp',dx=f,dy=f,clearance_z=.6*f)))
            z=self.b.session.project.keepouts[0];self.assertEqual(z.id,identity);self.assertAlmostEqual(z.points[0].x,13*f)
            d=self.workflow('keepouts',dict(change='toggle',id=identity,enabled=False));self.assertTrue(d['details']['keepout_change']['protection_removed']);self.apply_workflow(d)
            self.assertFalse(self.b.session.project.keepouts[0].enabled)
            self.apply_workflow(self.workflow('keepouts',dict(change='toggle',id=identity,enabled=True)))
            self.apply_workflow(self.workflow('keepouts',dict(change='delete',id=identity)))
            self.assertEqual(self.b.session.project.keepouts,[])

    def test_invalid_shapes_hidden_source_and_private_collision(self):
        self.fixture();job=self.b.session.project;before=copy.deepcopy(job.to_dict())
        for p in [self.rectangle(clearance_z=float('nan')),self.rectangle(x=float('inf'))]:
            r=self.b.handle(dict(command='manufacturing.management_prepare',params=dict(action='keepouts',params=p),revision=self.b.revision))
            self.assertFalse(r['ok']);self.assertEqual(before,job.to_dict())
        for p in [self.rectangle(width=0),self.rectangle(id='invented'),dict(change='delete',id='missing')]:
            self.workflow('keepouts',p,'failed')
        job=self.b.session.project
        job.entities.append(Polyline(id='crossed',closed=True,points=[Point(10,10),Point(12,12),Point(10,12),Point(12,10)]))
        self.freeze()
        p=dict(change='add',shape='selection',entity_id='crossed',name='Bad',clearance_z=.5)
        self.workflow('keepouts',p,'failed')
        job=self.b.session.project
        job.entities[-1].metadata['annotation_hidden']=True;self.workflow('keepouts',p,'failed')
        job=self.b.session.project
        job.entities=[e for e in job.entities if e.id!='crossed']
        # Cutter centers miss this zone but the cutter's edge contacts it.
        d=self.workflow('keepouts',self.rectangle(x=1.7,y=3,width=.1,height=.1),'failed')
        self.assertIn('envelope',d['error']);self.assertEqual(job.keepouts,[])

    def test_selected_circle_copied_no_live_binding_and_stale_draft(self):
        self.fixture();job=self.b.session.project
        job.entities.append(Circle(id='clamp',center=Point(15,15),radius=1));self.freeze()
        d=self.workflow('keepouts',dict(change='add',shape='selection',entity_id='clamp',name='Round clamp',clearance_z=.5))
        self.apply_workflow(d);zone=self.b.session.project.keepouts[0]
        self.assertEqual(len(zone.points),96);self.assertGreater(zone.points[0].x,16)
        p=self.rectangle(name='Second');d=self.workflow('keepouts',p)
        self.run_command('manufacturing.setup',material='Acrylic')
        self.assertFalse(self.b.handle(dict(command='manufacturing.management_apply',params=dict(token=d['token'],reviewed=True),revision=self.b.revision))['ok'])
        d=self.workflow('keepouts',p);self.run_command('manufacturing.cancel')
        self.assertFalse(self.b.handle(dict(command='manufacturing.management_apply',params=dict(token=d['token'],reviewed=True),revision=self.b.revision))['ok'])

    def test_conservative_arc_holder_and_disabled_clearance(self):
        self.fixture();job=self.b.session.project;op=job.operations[0];tool=job.tool_library.tools[0]
        prepare_keepout(job,self.rectangle(x=4.9,y=7.8,width=.2,height=.2))
        def issues(motions):
            return fixture_issues(job,{op.id:GeneratedToolpath([ToolpathBlock(op.id,1,motions)])})
        # Semicircle rises through the fixture; the endpoint chord is y=5.
        motions=[Motion(MotionKind.RAPID,ToolPoint(2,5,0)),Motion(MotionKind.ARC_CW,ToolPoint(8,5,0),center=Point(5,5))]
        self.assertTrue(any(v['code']=='fixture.envelope_collision' for v in issues(motions)))
        tool.holder_diameter=2;tool.stickout=.6
        line=[Motion(MotionKind.RAPID,ToolPoint(4,7,-.2)),Motion(MotionKind.LINEAR,ToolPoint(6,7,-.2))]
        found=issues(line);self.assertTrue(any('holder envelope' in i['message'] for i in found));self.assertFalse(any('cutter envelope' in i['message'] for i in found))
        tool.stickout=2;self.assertEqual(issues(line),[])
        job.keepouts[0].enabled=False;self.assertEqual(issues(motions),[])

    def test_generate_blocks_existing_collision_and_plate_edge_localization(self):
        self.fixture();job=self.b.session.project
        prepare_keepout(job,self.rectangle(x=1.7,y=3,width=.1,height=.1));self.freeze()
        d=self.generate('blocked');self.assertTrue(d['validation']['blocking'])
        self.assertFalse(self.b.handle(dict(command='manufacturing.export',params=dict(token=d.get('token'),reviewed=True,name='blocked'),revision=self.b.revision))['ok'])
        # Even a keepout completely outside a plate must survive localization.
        item=SimpleNamespace(plate=SimpleNamespace(x=10,y=20),result=SimpleNamespace(project=copy.deepcopy(job)))
        localize_all_keepouts(job,[item]);z=item.result.project.keepouts[0]
        self.assertEqual(z.id,job.keepouts[0].id);self.assertAlmostEqual(z.points[0].x,-8.3);self.assertAlmostEqual(z.points[0].y,-17)

    def test_blank_document_can_author_and_cancel_without_tools(self):
        self.run_command('project.new',units='in')
        d=self.workflow('keepouts',self.rectangle());self.assertEqual(d['motionCount'],0)
        self.apply_workflow(d)

    def test_touching_polygon_and_between_block_transit(self):
        with self.assertRaisesRegex(ValueError,'simple closed'):
            validate_points([Point(0,0),Point(4,0),Point(4,4),Point(2,0),Point(0,4)])
        self.fixture();job=self.b.session.project;op=job.operations[0]
        prepare_keepout(job,self.rectangle(x=4,y=4,width=1,height=1))
        blocks=[ToolpathBlock(op.id,1,[Motion(MotionKind.RAPID,ToolPoint(2,4.5,.25))]),ToolpathBlock(op.id,1,[Motion(MotionKind.RAPID,ToolPoint(8,4.5,.25))])]
        findings=fixture_issues(job,{op.id:GeneratedToolpath(blocks)})
        self.assertTrue(any(i['code']=='fixture.envelope_collision' for i in findings))
        self.assertTrue(any(i['code']=='fixture.holder_missing' for i in findings))

    def test_outside_sheet_holder_collision_survives_real_output_preparation(self):
        self.fixture();job=self.b.session.project;e=job.entities[0]
        e.corner_a=Point(.5,2);e.corner_b=Point(6.5,6)
        tool=job.tool_library.tools[0];tool.holder_diameter=2;tool.stickout=1
        prepare_keepout(job,self.rectangle(x=-.5,y=3,width=.2,height=.2,clearance_z=.6))
        self.freeze();d=self.generate('blocked')
        self.assertTrue(any('holder envelope' in v['message'] for v in d['validation']['issues']))
        self.assertEqual(len(self.b.manufacturing.prepared[0].result.project.keepouts),1)


for name in dir(Base):
    if name.startswith('test_') and name not in KeepoutTests.__dict__:setattr(KeepoutTests,name,None)

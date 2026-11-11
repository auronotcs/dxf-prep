import copy
from test_manufacturing_advanced import AdvancedManufacturingTests as Base
from cam_core.model import Circle, Point, Rectangle
from cam_core.toolpath import MotionKind

class ManufacturingMotionTests(Base):
    def freeze_fixture(self):
        # Load the synthetic geometry through document persistence so history has a real baseline.
        if self.b.session.project.operations: self.b.session.project.operations[0].entity_ids=[e.id for e in self.b.session.project.entities]
        path=self.root/'motion-fixture.camjob.json'
        self.run_command('project.save',path=str(path));self.run_command('project.open',path=str(path))
    def draft(self,command,params):
        return self.prepared('operation_prepare',dict(command=command,params=params),'operation_ready')
    def refused(self,command,params):
        return self.prepared('operation_prepare',dict(command=command,params=params),'failed')
    def test_sliver_cleanup_final_depth_units_link_limits_and_output(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            self.run_command('manufacturing.operation_delete',id=self.b.session.project.operations[0].id)
            eid=self.b.session.project.entities[0].id
            self.b.session.project.entities=[Rectangle(id=eid,corner_a=Point(2*f,2*f),corner_b=Point(6*f,6*f))]
            self.freeze_fixture()
            params=dict(name='Short loops',tool_station=1,entity_ids=[eid],island_entity_ids=[],target_depth=.4*f,safe_z=.25*f,stepdown=.1*f,stepover=.125*f,sliver_cleanup=True,sliver_max_path_length=4*f,sliver_link_distance=.1875*f)
            preview=self.draft('fill_save',params)
            review=preview['blockReview'];self.assertEqual(review[-1]['details']['role'],'sliver_cleanup');self.assertAlmostEqual(review[-1]['details']['depth'],.4*f)
            self.apply(preview);paths=self.parity('sliver-'+units)
            cleanup=next(iter(paths.values())).blocks[-1]
            self.assertGreater(cleanup.metadata['loop_count'],1);self.assertGreater(cleanup.metadata['linked_count'],0)
            params['id']=self.b.session.project.operations[0].id
            for changes in (dict(sliver_link_distance=.251*f),dict(sliver_max_path_length=0),dict(sliver_cleanup='yes')):
                self.refused('fill_save',dict(params,**changes))
            self.assertFalse(self.b.handle(dict(command='manufacturing.fill_save',params=params,revision=self.b.revision))['ok'])
    def test_curve_feed_schedules_actual_arc_feed_units_and_rejects_bad_values(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1;eid=self.b.session.project.entities[0].id
            self.b.session.project.entities=[Circle(id=eid,center=Point(2*f,2*f),radius=.1875*f)]
            self.freeze_fixture()
            p=dict(self.params,id=self.b.session.project.operations[0].id,target_depth=.125*f,side='inside',curve_feed_mode='wall_compensation',curve_feed_min_percent=25,curve_feed_transition_distance=.5*f)
            d=self.draft('profile_save',p);self.assertGreater(d['blockReview'][0]['details']['curve_feed_adjusted_motions'],0);self.apply(d)
            paths=self.parity('curve-'+units);arcs=[m for m in next(iter(paths.values())).motions if m.kind in (MotionKind.ARC_CW,MotionKind.ARC_CCW) and m.end.z<0]
            self.assertTrue(arcs);self.assertTrue(all(abs(m.feed-2*f/3)<1e-7 for m in arcs))
            for change in (dict(curve_feed_min_percent=101),dict(curve_feed_transition_distance=0),dict(curve_feed_mode='unknown')): self.refused('profile_save',dict(p,**change))
            # Group rebuilding cannot erase the newly supported optimization fields.
            result=self.prepared('group_prepare',dict(id=p['id'],settings=self.settings(),source={}), 'failed')
            self.assertIn('Group conversion',result['error'])
    def test_contour_order_uses_core_containment_and_literal_override(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            entities=[Circle(id=n,center=Point(x*f,3*f),radius=r*f) for n,x,r in [('a_outer',3,2),('b_outer',11,2),('c_inner',11,1),('d_inner',3,1)]]
            self.b.session.project.entities=entities
            self.freeze_fixture()
            p=dict(self.params,id=self.b.session.project.operations[0].id,entity_ids=[e.id for e in entities],target_depth=.1*f,stepdown=.1*f)
            for mode,expected in [('inside_first',['d_inner','c_inner','a_outer','b_outer']),('part_by_part',['d_inner','a_outer','c_inner','b_outer']),('source_order',['a_outer','b_outer','c_inner','d_inner'])]:
                d=self.draft('profile_save',dict(p,contour_order=mode))
                ids=[b['details']['entity_id'] for b in d['blockReview']]
                if mode=='inside_first': self.assertEqual(set(ids[:2]),{'d_inner','c_inner'});self.assertEqual(set(ids[2:]),{'a_outer','b_outer'})
                else: self.assertEqual(ids,expected)
                self.apply(d);self.parity(mode+'-'+units)
            self.refused('profile_save',dict(p,contour_order='bad'))
    def test_common_line_exact_pair_no_match_and_incompatible_inputs(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            entities=[Rectangle(id='left',corner_a=Point(2*f,2*f),corner_b=Point(6*f,5*f)),Rectangle(id='right',corner_a=Point(6.25*f,2*f),corner_b=Point(10.25*f,5*f))]
            self.b.session.project.entities=entities
            self.freeze_fixture()
            p=dict(self.params,id=self.b.session.project.operations[0].id,entity_ids=['left','right'],target_depth=.125*f,common_line_mode='paired_rectangles',common_line_tolerance=.001*f)
            d=self.draft('profile_save',p);self.assertEqual(d['blockCount'],1);self.assertEqual(d['blockReview'][0]['details']['role'],'common_line_pair')
            self.assertIn('toolpath.common_line_release',[i['code'] for i in d['validation']['issues']]);self.apply(d)
            direct=self.parity('common-'+units);block=next(iter(direct.values())).blocks[0];self.assertAlmostEqual(block.metadata['common_line_length'],3*f);self.assertEqual(block.metadata['common_line_lifts_saved'],1)
            for change in (dict(side='inside'),dict(lead_in=.1*f),dict(allowance=.01*f),dict(common_line_tolerance=0)): self.refused('profile_save',dict(p,**change))
            right=self.b.session.project.entities[1];right.corner_a=Point(6.5*f,2*f);right.corner_b=Point(10.5*f,5*f)
            d=self.draft('profile_save',p);self.assertEqual(d['blockCount'],2);self.assertIn('toolpath.common_line_no_match',[i['code'] for i in d['validation']['issues']])
    def test_drill_equal_pecks_feeds_return_units_roundtrip_and_invalid_inputs(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            self.run_command('manufacturing.operation_delete',id=self.b.session.project.operations[0].id)
            self.b.session.project.entities=[Circle(id='hole',center=Point(2*f,2*f),radius=.125*f)]
            self.freeze_fixture()
            p=dict(name='Equal pecks',entity_ids=['hole'],tool_station=1,target_depth=.188*f,safe_z=.5*f,peck_definition='count',peck_count=5,peck_depth=None,peck_mode='chip_break',retract_height=.005*f,reentry_clearance=.02*f,surface_feed_override=2.5*f,plunge_rate_override=5*f,retract_feed_override=5*f,controlled_retract=True,return_to_surface=True,spindle_rpm_override=16000,dwell_seconds=0,top_dwell_seconds=0)
            d=self.draft('drill_save',p);self.assertEqual(len(d['drillSchedule']),5);self.assertAlmostEqual(d['drillSchedule'][-1],.188*f);self.apply(d)
            direct=self.parity('drill-cycle-'+units);motions=next(iter(direct.values())).blocks[0].motions
            self.assertEqual([round(m.end.z/f,4) for m in motions],[.5,0,-.0376,-.0326,-.0752,-.0702,-.1128,-.1078,-.1504,-.1454,-.188,0,.5])
            self.assertTrue(all(m.kind==MotionKind.LINEAR for m in motions[2:-1]));self.assertTrue(all(abs(m.feed-5*f)<1e-8 for m in motions[2:-1]))
            p['id']=self.b.session.project.operations[0].id
            for change in (dict(peck_count=0),dict(peck_count=2.5),dict(controlled_retract='yes'),dict(retract_feed_override=-1),dict(spindle_rpm_override=1.2)):
                self.refused('drill_save',dict(p,**change))
            self.assertFalse(self.b.handle(dict(command='manufacturing.drill_save',params=p,revision=self.b.revision))['ok'])
            unsafe=self.refused('drill_save',dict(p,controlled_retract=False));self.assertIn('rapids below the stock surface',unsafe['error'])
            r=self.refused('drill_save',dict(p,top_dwell_seconds=.1));self.assertIn('no qualified dwell',r['error'])

for name in list(Base.__dict__):
    if name.startswith('test_'): setattr(ManufacturingMotionTests,name,None)

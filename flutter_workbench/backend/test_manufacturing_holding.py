from test_manufacturing_advanced import AdvancedManufacturingTests as Base
from cam_core.model import Line, Polyline, Point
from manufacturing_support import preflight_bundle, supported


class ManufacturingHoldingTests(Base):
    def draft(self, params, status='operation_ready'):
        return self.prepared('operation_prepare',dict(command='profile_save',params=params),status)

    def freeze(self):
        path=self.root/'holding-fixture.camjob.json'
        self.run_command('project.save',path=str(path)); self.run_command('project.open',path=str(path))

    def test_stock_depth_intent_units_stale_stock_review_and_output(self):
        for units in ('in','mm'):
            self.fixture(units); f=25.4 if units=='mm' else 1
            p=dict(self.params,id=self.b.session.project.operations[0].id,cut_intent='through',breakthrough=.015*f,target_depth=.1*f)
            d=self.draft(p);self.assertAlmostEqual(d['operation']['target_depth'],.515*f)
            self.apply(d);self.parity('through-'+units)
            self.run_command('manufacturing.setup',material_thickness=.6*f)
            with self.assertRaisesRegex(ValueError,'stock thickness changed'):
                preflight_bundle(self.b.session.project,lambda _:None)
            d=self.draft(p);self.assertAlmostEqual(d['operation']['target_depth'],.615*f)
            self.apply(d);self.parity('through-revised-'+units)
            p.update(cut_intent='partial',target_depth=.2*f,breakthrough=0)
            self.apply(self.draft(p));self.parity('partial-'+units)
            for change in (dict(target_depth=.6*f),dict(target_depth=.7*f),dict(cut_intent='through',breakthrough=-.1*f),dict(cut_intent='unknown')):
                self.draft(dict(p,**change),'failed')
            self.run_command('manufacturing.setup',material_thickness=.2*f)
            with self.assertRaisesRegex(ValueError,'partial depth'):
                preflight_bundle(self.b.session.project,lambda _:None)

    def test_onion_skin_actual_roughing_and_final_pass_with_tabs_and_units(self):
        for units in ('in','mm'):
            self.fixture(units); f=25.4 if units=='mm' else 1
            p=dict(self.params,id=self.b.session.project.operations[0].id,stepdown=.2*f,onion_skin=.1*f)
            d=self.draft(p);self.assertEqual([round(b['details']['depth']/f,4) for b in d['blockReview']],[.2,.4,.5])
            self.apply(d);paths=self.parity('skin-'+units)
            self.assertAlmostEqual(min(m.end.z for m in next(iter(paths.values())).motions),-.5*f)
            p.update(tab_count=2,tab_width=.5*f,tab_height=.05*f,finishing_pass=True,allowance=.02*f)
            self.apply(self.draft(p));paths=self.parity('skin-tabs-finish-'+units)
            last=next(iter(paths.values())).blocks[-1]
            self.assertEqual(last.metadata['role'],'finish')
            heights={round(m.end.z/f,4) for m in last.motions}
            self.assertTrue({-.5,-.45} <= heights)
            for skin in (-.01*f,.5*f,.6*f):self.draft(dict(p,onion_skin=skin),'failed')

    def test_automatic_tabs_modes_retained_width_height_and_guarded_release(self):
        for units in ('in','mm'):
            self.fixture(units); f=25.4 if units=='mm' else 1
            p=dict(self.params,id=self.b.session.project.operations[0].id,cut_intent='through',breakthrough=.015*f,tab_count=2,tab_width=.5*f,tab_height=.1*f,tab_spacing=4*f,corner_tab_offset=.5*f,female_tab_min_perimeter=0)
            for mode in ('even','minimum_and_spacing','minimum_avoid_corners','support_aware','corners_and_spacing'):
                d=self.draft(dict(p,tab_mode=mode));self.apply(d);paths=self.parity(mode+'-'+units)
                self.assertFalse(self.b.session.project.operations[0].remove_tabs_after)
                last=next(iter(paths.values())).blocks[-1]
                heights={round(m.end.z/f,4) for m in last.motions}
                self.assertTrue({-.515,-.4} <= heights,(mode,heights))
                self.assertAlmostEqual(last.metadata['finished_tab_width'],.5*f)
                self.assertAlmostEqual(last.metadata['raised_tab_span'],.75*f)
                self.assertAlmostEqual(d['blockReview'][-1]['details']['raised_tab_span'],.75*f)
            for change in (dict(tab_count=1.2),dict(tab_count=-1),dict(tab_height=.5*f),dict(tab_width=0),dict(tab_mode='manual'),dict(tab_release_plan={"unported":True}),dict(tab_mode='minimum_and_spacing',tab_spacing=0),dict(common_line_mode='paired_rectangles')):
                self.draft(dict(p,**change),'failed')
            self.assertFalse(self.b.handle(dict(command='manufacturing.profile_save',params=dict(self.params,id=p['id']),revision=self.b.revision))['ok'])
            rejected=self.prepared('group_prepare',dict(id=p['id'],settings=self.settings(),source={}), 'failed')
            self.assertIn('Group conversion',rejected['error'])

    def test_open_cut_line_offsets_ramp_increments_units_and_ignored_settings_rejected(self):
        for units in ('in','mm'):
            self.fixture(units); f=25.4 if units=='mm' else 1
            self.run_command('manufacturing.operation_delete',id=self.b.session.project.operations[0].id)
            eid=self.params['entity_ids'][0]
            self.b.session.project.entities=[Line(id=eid,start=Point(2*f,2*f),end=Point(6*f,2*f))]
            self.freeze()
            p=dict(self.params,target_depth=.3*f,pass_depths=[.1*f,.15*f],entry_mode='ramp',side='on')
            for side,y in [('on',2),('left',2.125),('right',1.875)]:
                if self.b.session.project.operations:p['id']=self.b.session.project.operations[0].id
                d=self.draft(dict(p,side=side));self.assertEqual([round(b['details']['depth']/f,4) for b in d['blockReview']],[.1,.25,.3])
                self.apply(d);paths=self.parity('open-'+side+'-'+units)
                motions=next(iter(paths.values())).motions
                self.assertTrue(all(abs(m.end.y/f-y)<1e-7 for m in motions))
                self.assertAlmostEqual(min(m.end.z for m in motions),-.3*f)
            for change in (dict(side='inside'),dict(onion_skin=.01*f),dict(tab_count=2,tab_width=.5*f,tab_height=.1*f),dict(finishing_pass=True),dict(curve_feed_mode='wall_compensation'),dict(entry_mode='helical',helix_diameter=.2*f,helix_pitch=.05*f)):
                self.draft(dict(p,**change),'failed')
            self.run_command('manufacturing.operation_delete',id=self.b.session.project.operations[0].id)
            p.pop('id',None)
            self.b.session.project.entities=[Polyline(id=eid,points=[Point(2*f,2*f),Point(6*f,2*f),Point(6*f,4*f)],closed=False)]
            self.freeze();self.apply(self.draft(p));self.parity('open-polyline-'+units)
            self.assertTrue(supported(self.b.session.project))

    def test_closed_on_contour_centerline_units_and_group_guard(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            p=dict(self.params,id=self.b.session.project.operations[0].id,side='on',target_depth=.1*f,stepdown=.1*f)
            self.apply(self.draft(p));paths=self.parity('closed-on-'+units)
            points=[m.end for m in next(iter(paths.values())).motions if m.end.z<0]
            self.assertEqual({(round(v.x/f,5),round(v.y/f,5)) for v in points},{(2,2),(8,2),(8,6),(2,6)})
            r=self.prepared('group_prepare',dict(id=p['id'],settings=self.settings(),source={}), 'failed')
            self.assertIn('inside/outside',r['error'])
            self.draft(dict(p,side='left'),'failed')
            self.draft(dict(p,common_line_mode='paired_rectangles'),'failed')

for name in list(Base.__dict__):
    if name.startswith('test_'):setattr(ManufacturingHoldingTests,name,None)

import copy
from test_manufacturing_holding import ManufacturingHoldingTests as Base
from cam_core.model import Circle, Point
from manufacturing_support import supported


class OptimizationTabsTests(Base):
    def reviewed_apply(self,draft,optimization=False):
        command='manufacturing.optimization_apply' if optimization else 'manufacturing.operation_apply'
        before=copy.deepcopy(self.b.session.project.to_dict());index=self.b.session._history_index
        if draft.get('requiresReleaseReview'):
            rejected=self.b.handle(dict(command=command,params=dict(token=draft['token'],reviewed=True),revision=self.b.revision))
            self.assertFalse(rejected['ok']);self.assertIn('release',rejected['error'])
        self.run_command(command,token=draft['token'],reviewed=True,release_reviewed=True)
        self.assertEqual(self.b.session._history_index,index+1)
        after=copy.deepcopy(self.b.session.project.to_dict())
        self.run_command('history.undo');self.assertEqual(before,self.b.session.project.to_dict())
        self.run_command('history.redo');self.assertEqual(after,self.b.session.project.to_dict())
        self.assertTrue(supported(self.b.session.project))

    def test_cycle_comparison_units_output_history_cancel_and_stale(self):
        for units in ('in','mm'):
            self.fixture(units)
            self.run_command('manufacturing.profile_save',**dict(self.params,id=self.b.session.project.operations[0].id,side='on'))
            d=self.prepared('optimization_prepare',dict(enabled=True,depth_linking=True),'optimization_ready')
            self.assertFalse(d['settings']['minimum_retract']);self.assertFalse(d['settings']['arc_fitting'])
            self.assertGreater(sum(map(len,d['baselineMotions'])),d['motionCount'])
            self.reviewed_apply(d,True);self.parity('optimized-'+units)
            for p in (dict(arc_fitting=True),dict(minimum_retract=True),dict(rapid_xy_ipm=-1),dict(profile_travel_strategy='unknown')):
                self.prepared('optimization_prepare',p,'failed')
            d=self.prepared('optimization_prepare',dict(enabled=False),'optimization_ready')
            self.assertEqual(d['motions'],d['baselineMotions'])
            self.reviewed_apply(d,True);self.parity('original-'+units)
            d=self.prepared('optimization_prepare',dict(enabled=True),'optimization_ready')
            self.run_command('manufacturing.cancel')
            self.assertFalse(self.b.handle(dict(command='manufacturing.optimization_apply',params=dict(token=d['token'],reviewed=True),revision=self.b.revision))['ok'])
            d=self.prepared('optimization_prepare',dict(enabled=True),'optimization_ready')
            self.run_command('manufacturing.setup',material='Acrylic')
            self.assertFalse(self.b.handle(dict(command='manufacturing.optimization_apply',params=dict(token=d['token'],reviewed=True),revision=self.b.revision))['ok'])

    def test_manual_tabs_percent_centers_units_floor_and_output(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            p=dict(self.params,id=self.b.session.project.operations[0].id,cut_intent='through',breakthrough=.015*f,tab_mode='manual',manual_tab_positions=[.125,.625],tab_count=0,tab_width=.5*f,tab_height=.1*f)
            self.apply(self.draft(p));paths=self.parity('manual-'+units)
            self.assertEqual(self.b.session.project.operations[0].manual_tab_positions,[.125,.625])
            last=next(iter(paths.values())).blocks[-1]
            self.assertTrue({-.515,-.4} <= {round(m.end.z/f,4) for m in last.motions})
            self.assertAlmostEqual(last.metadata['raised_tab_span'],.75*f)
            for positions in ([],[-.1],[1],[.2,.2]):
                self.draft(dict(p,manual_tab_positions=positions),'failed')

    def test_aligned_tabs_shared_bands_units_output_and_eligibility(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            first=self.b.session.project.entities[0]
            other=copy.deepcopy(first);other.id='second';other.corner_a=Point(other.corner_a.x+8*f,other.corner_a.y);other.corner_b=Point(other.corner_b.x+8*f,other.corner_b.y)
            self.b.session.project.entities.append(other);self.freeze()
            p=dict(self.params,id=self.b.session.project.operations[0].id,entity_ids=[first.id,other.id],tab_mode='aligned',tab_count=4,tab_width=.3*f,tab_height=.1*f,tab_spacing=4*f)
            self.apply(self.draft(p));paths=self.parity('aligned-'+units)
            blocks=next(iter(paths.values())).blocks
            raised={eid:{round(m.end.y/f,4) for b in blocks if b.metadata.get('entity_id')==eid for m in b.motions if abs(m.end.z+.4*f)<1e-7} for eid in p['entity_ids']}
            self.assertTrue(raised[first.id] & raised[other.id],raised)
            for change in (dict(entry_mode='ramp'),dict(lead_in=.1*f),dict(side='inside'),dict(remove_tabs_after=True,tab_cleanup_order='after_each_part')):
                self.draft(dict(p,**change),'failed')

    def test_tab_cleanup_explicit_release_review_units_actual_spans_and_output(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            p=dict(self.params,id=self.b.session.project.operations[0].id,cut_intent='through',breakthrough=.015*f,tab_mode='manual',manual_tab_positions=[.125,.625],tab_width=.5*f,tab_height=.1*f,remove_tabs_after=True,tab_cleanup_order='end_of_operation',tab_cleanup_min_perimeter=0)
            d=self.draft(p);self.assertTrue(d['requiresReleaseReview'])
            self.reviewed_apply(d);paths=self.parity('cleanup-'+units)
            blocks=next(iter(paths.values())).blocks
            cleanup=[b for b in blocks if b.metadata.get('role')=='tab_cleanup']
            self.assertEqual(len(cleanup),1)
            self.assertTrue(all(b.metadata.get('role')=='tab_cleanup' for b in blocks[-1:]))
            self.assertTrue(all(abs(min(m.end.z for m in b.motions)+.515*f)<1e-7 for b in cleanup))
            d=self.prepared('optimization_prepare',dict(enabled=True),'optimization_ready');self.reviewed_apply(d,True)
            optimized=self.parity('cleanup-optimized-'+units)
            self.assertEqual(next(iter(optimized.values())).blocks[-1].metadata.get('role'),'tab_cleanup')
            d=self.draft(dict(p,tab_cleanup_min_perimeter=100*f));self.reviewed_apply(d)
            paths=self.parity('cleanup-threshold-'+units)
            self.assertFalse(any(b.metadata.get('role')=='tab_cleanup' for b in next(iter(paths.values())).blocks))

    def test_preferred_start_vertex_and_circle_quadrant_units_preserved(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            p=dict(self.params,id=self.b.session.project.operations[0].id,side='on',target_depth=.1*f,stepdown=.1*f)
            for kind in ('rectangle','circle'):
                if kind=='circle':
                    self.b.session.project.entities=[Circle(id=p['entity_ids'][0],center=Point(5*f,4*f),radius=f)];self.freeze()
                starts=[]
                for index in (0,1):
                    self.apply(self.draft(dict(p,start_index=index)))
                    self.assertEqual(self.b.session.project.operations[0].start_index,index)
                    paths=self.parity(kind+str(index)+'-'+units)
                    motion=next(m for m in next(iter(paths.values())).motions if m.end.z<0)
                    starts.append((round(motion.end.x/f,5),round(motion.end.y/f,5)))
                self.assertNotEqual(*starts)
            for index in (-1,1.5):self.draft(dict(p,start_index=index),'failed')

for name in list(Base.__dict__):
    if name.startswith('test_'):setattr(OptimizationTabsTests,name,None)

import copy
import unittest
from test_design_bridge import DesignBridgeTests
from cam_core.model import Rectangle, Point, Line, RouterOffsetOperation
from cam_core.appearance import ObjectAppearance


class CleaveBridgeTests(unittest.TestCase):
    setUp = DesignBridgeTests.setUp
    run_command = DesignBridgeTests.run_command

    def test_finite_line_only_crosses_reached_shapes_and_undo_preserves_all(self):
        self.setup_drawing()
        self.run_command('geometry.add_rectangle', x=0, y=20, width=10, height=8)
        self.run_command('selection.set', entity_ids=[e.id for e in self.bridge.session.project.entities])
        before = self.bridge.session.project.to_dict()
        line = [dict(x=5,y=-1), dict(x=5,y=9)]
        r = self.bridge.handle(dict(command='design.preview_shapes', params=dict(kind='cleave',line=line), revision=self.bridge.revision))
        self.assertTrue(r['ok'], r.get('error'))
        p = r['preview']
        self.assertEqual(p['unaffectedCount'],1)
        self.assertAlmostEqual(p['areaBefore'],p['areaAfter'])
        self.assertEqual(self.bridge.session.project.to_dict(),before)
        self.run_command('design.apply_shapes',token=p['token'])
        self.assertEqual(len(self.bridge.session.project.entities),3)
        self.assertEqual(self.bridge.history_labels[-1], 'Cleave / split along drawn line')
        self.run_command('history.undo')
        self.assertEqual(self.bridge.session.project.to_dict(),before)

    def test_finite_line_incomplete_degenerate_and_conflicting_input_refused(self):
        self.setup_drawing()
        for line in ([dict(x=5,y=1),dict(x=5,y=7)], [dict(x=5,y=-1),dict(x=5,y=4)],
                     [dict(x=5,y=1),dict(x=5,y=1)], [dict(x=float('nan'),y=0),dict(x=5,y=9)]):
            self.reject('design.preview_shapes',kind='cleave',line=line)
        self.reject('design.preview_shapes',kind='cleave',guide_id=self.guide,line=[dict(x=5,y=-1),dict(x=5,y=9)])

    def setup_drawing(self, orientation='vertical', position=5, units='in'):
        self.run_command('project.new', units=units)
        self.run_command('geometry.add_rectangle', x=0, y=0, width=10, height=8)
        self.owner = self.bridge.session.project.entities[0].id
        self.run_command('selection.set', entity_ids=[self.owner])
        self.run_command('design.guide_add', orientation=orientation, position=position)
        self.guide = self.bridge.session.project.guides[0].id

    def preview(self):
        before = self.bridge.snapshot()
        r = self.bridge.handle(dict(command='design.preview_shapes', params=dict(kind='cleave', guide_id=self.guide), revision=self.bridge.revision))
        self.assertTrue(r['ok'], r.get('error'))
        self.assertEqual(self.bridge.snapshot(), before)
        return r['preview']

    def reject(self, command, **params):
        before = self.bridge.snapshot()
        r = self.bridge.handle(dict(command=command, params=params, revision=self.bridge.revision))
        self.assertFalse(r['ok'], r)
        self.assertEqual(self.bridge.snapshot(), before)
        return r['error']

    def test_axes_units_appearance_one_step_undo_and_persistence(self):
        for units in ('in', 'mm'):
            for orientation in ('horizontal', 'vertical'):
                self.setup_drawing(orientation, 3.25, units)
                project = self.bridge.session.project
                project.entities[0].appearance = ObjectAppearance.from_dict(dict(fill_enabled=True, stroke_enabled=True, stroke_width=.02))
                project.entities[0].metadata.update(group_id='assembly', plate_number=1)
                # Record this test setup so Undo has a comparable prior state.
                self.bridge.session._history[self.bridge.session._history_index] = copy.deepcopy(project.to_dict())
                before = copy.deepcopy(project.to_dict())
                history = self.bridge.session._history_index
                p = self.preview()
                self.assertAlmostEqual(p['areaBefore'], 80)
                self.assertAlmostEqual(p['areaAfter'], 80)
                self.assertEqual((p['leftCount'], p['rightCount']), (1,1))
                after = self.run_command('design.apply_shapes', token=p['token'])
                self.assertEqual(self.bridge.session._history_index, history+1)
                self.assertEqual(after['history']['states'][-1]['label'] if 'states' in after['history'] else self.bridge.history_labels[-1], 'Cleave / split along guide')
                self.assertEqual(after['guides'], before['guides'])
                self.assertNotIn(self.owner, [e['id'] for e in after['entities']])
                for e in after['entities']:
                    self.assertTrue(e['closed'])
                    self.assertEqual(e['appearance'], before['entities'][0]['appearance'])
                    self.assertEqual(e['metadata']['group_id'], 'assembly')
                expected = copy.deepcopy(self.bridge.session.project.to_dict())
                self.run_command('history.undo'); self.assertEqual(self.bridge.session.project.to_dict(), before)
                self.run_command('history.redo'); self.assertEqual(self.bridge.session.project.to_dict(), expected)
                path = self.folder/f'{units}-{orientation}.camjob.json'
                self.run_command('project.save', path=str(path)); self.run_command('project.open', path=str(path))
                self.assertEqual(self.bridge.session.project.to_dict(), expected)

    def test_compound_holes_expand_and_locked_or_hidden_members_refuse(self):
        self.setup_drawing()
        project = self.bridge.session.project
        project.entities[0].metadata['compound_id'] = 'letter'
        hole = Rectangle(corner_a=Point(2,2), corner_b=Point(8,6), layer='holes', metadata={'compound_id':'letter'})
        project.entities.append(hole)
        p = self.preview()
        self.assertEqual(p['expandedCount'], 1)
        self.assertEqual(p['sourceCount'], 2)
        self.assertAlmostEqual(p['areaBefore'], 56)
        self.assertAlmostEqual(p['areaAfter'], 56)
        self.run_command('layer.set', name='holes', locked=True)
        self.reject('design.preview_shapes', kind='cleave', guide_id=self.guide)
        self.run_command('layer.set', name='holes', locked=False, visible=False)
        self.reject('design.preview_shapes', kind='cleave', guide_id=self.guide)

    def test_unaffected_shapes_stay_and_invalid_crossings_are_nonmutating(self):
        self.setup_drawing()
        extra = Rectangle(corner_a=Point(20,0), corner_b=Point(25,5), metadata={'compound_id':'uncrossed'})
        inner = Rectangle(corner_a=Point(21,1), corner_b=Point(24,4), metadata={'compound_id':'uncrossed'})
        self.bridge.session.project.entities.extend([extra,inner])
        self.run_command('selection.set', entity_ids=[self.owner, extra.id])
        p = self.preview(); self.assertEqual(p['unaffectedCount'], 2)
        self.run_command('design.apply_shapes', token=p['token'])
        self.assertEqual(next(e.to_dict() for e in self.bridge.session.project.entities if e.id==extra.id), extra.to_dict())
        self.assertIn(extra.id, self.bridge.session.selected_ids)
        self.assertIn(inner.id, self.bridge.session.selected_ids)
        self.setup_drawing(position=0)
        self.assertIn('overlaps', self.reject('design.preview_shapes', kind='cleave', guide_id=self.guide))
        self.setup_drawing(position=30)
        self.reject('design.preview_shapes', kind='cleave', guide_id=self.guide)
        self.bridge.session.project.entities = [Line(start=Point(0,0), end=Point(10,8), id=self.owner)]
        self.reject('design.preview_shapes', kind='cleave', guide_id=self.guide)

    def test_dependencies_led_review_and_locked_dependent_guard(self):
        self.setup_drawing()
        project = self.bridge.session.project
        dependent = Rectangle(corner_a=Point(1,1), corner_b=Point(2,2), layer='generated', metadata={'mounting_owner_entity_id':self.owner})
        project.entities.append(dependent)
        project.metadata['led_plans'] = [{'id':'led-test','linked_entity_ids':[self.owner]}]
        p = self.preview()
        self.assertEqual(p['removedDependentIds'], [dependent.id])
        self.assertEqual(p['reviewedLedPlanIds'], ['led-test'])
        self.run_command('layer.set', name='generated', locked=True)
        self.assertIn('dependent', self.reject('design.preview_shapes', kind='cleave', guide_id=self.guide))
        self.run_command('layer.set', name='generated', locked=False)
        p = self.preview()
        before = copy.deepcopy(self.bridge.session.project.to_dict())
        self.run_command('design.apply_shapes', token=p['token'])
        self.assertNotIn(dependent.id, [e.id for e in self.bridge.session.project.entities])
        self.assertTrue(self.bridge.session.project.metadata['led_plans'][0]['geometry_review_required'])
        self.run_command('history.undo'); self.assertEqual(self.bridge.session.project.to_dict(), before)

    def test_stale_revision_selection_digest_and_invalid_preview_reject_apply(self):
        for mode in ('revision','selection','digest','invalid'):
            self.setup_drawing()
            p = self.preview()
            if mode == 'revision': self.run_command('design.guide_add', orientation='horizontal', position=3)
            elif mode == 'selection': self.bridge.session.selected_ids.clear()
            elif mode == 'digest': self.bridge.session.project.guides[0].position=4
            else: self.reject('design.preview_shapes', kind='cleave', guide_id='missing')
            self.reject('design.apply_shapes', token=p['token'])

    def test_hidden_guide_generated_reference_and_machining_refuse_locked_guide_allowed(self):
        self.setup_drawing()
        self.bridge.session.project.guides[0].locked=True
        self.preview()
        self.bridge.session.project.guides[0].visible=False
        self.reject('design.preview_shapes', kind='cleave', guide_id=self.guide)
        self.bridge.session.project.guides[0].visible=True
        for key in ('reference_only','mounting_generated'):
            self.bridge.session.project.entities[0].metadata={key:True}
            self.reject('design.preview_shapes', kind='cleave', guide_id=self.guide)
        self.bridge.session.project.entities[0].metadata={}
        self.bridge.session.project.operations.append(RouterOffsetOperation('Profile',1,[self.owner],.1,.2))
        self.reject('design.preview_shapes', kind='cleave', guide_id=self.guide)

    def test_linked_dimension_fallbacks_survive_split_undo_and_export(self):
        from cam_core.dxf import import_dxf
        self.setup_drawing()
        a=dict(kind='horizontal', start=dict(entity_id=self.owner,feature='rectangle:corner:0',fallback=dict(x=0,y=0)),
            end=dict(entity_id=self.owner,feature='rectangle:corner:2',fallback=dict(x=10,y=8)), offset=2,
            precision=3,text_height=.18,extension_offset=.1,layer='DIMENSIONS')
        r=self.bridge.handle(dict(command='design.dimension_preview',params=dict(annotation=a)))
        self.assertTrue(r['ok'],r)
        self.run_command('design.dimension_apply',token=r['preview']['token'])
        before=copy.deepcopy(self.bridge.snapshot()['dimensions'][0])
        p=self.preview();self.assertEqual(p['detachedDimensionCount'],1)
        state=self.run_command('design.apply_shapes',token=p['token'])
        d=state['dimensions'][0]
        self.assertFalse(d['resolved'])
        self.assertEqual(d['start'],before['start']);self.assertEqual(d['end'],before['end'])
        path=self.folder/'split.dxf';self.run_command('project.export_dxf',path=str(path))
        exported=import_dxf(path).project
        self.assertEqual(len(exported.entities),2);self.assertFalse(exported.guides)
        self.run_command('history.undo');self.assertTrue(self.bridge.snapshot()['dimensions'][0]['resolved'])

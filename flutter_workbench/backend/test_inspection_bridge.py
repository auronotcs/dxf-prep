import copy
import math
import unittest
from test_design_bridge import DesignBridgeTests
from cam_core.model import Circle, Line, Rectangle, Polyline, TextEntity, Point, RouterOffsetOperation
from cam_core.layout import entity_perimeter
from cam_core.workflow import entity_area


class InspectionBridgeTests(unittest.TestCase):
    setUp = DesignBridgeTests.setUp
    run_command = DesignBridgeTests.run_command

    def test_measurements_for_each_type_in_inches_and_mm(self):
        for units, f in [('in', 1), ('mm', 25.4)]:
            self.run_command('project.new', units=units)
            entities = [
                Line(start=Point(0, 0), end=Point(3*f, 4*f)),
                Circle(center=Point(7*f, 8*f), radius=2*f),
                Rectangle(corner_a=Point(4*f, 3*f), corner_b=Point(0, 0)),
                Polyline(points=[Point(0, 0), Point(3*f, 0), Point(3*f, 4*f)], closed=False),
                Polyline(points=[Point(0, 0), Point(3*f, 0), Point(3*f, 4*f)], closed=True),
                TextEntity(text='ABC\nDEF', height=f, rotation=37),
            ]
            self.bridge.session.project.entities = entities
            self.bridge.session.selected_ids = {e.id for e in entities}
            state = self.bridge.snapshot()
            data = state['selectionInspection']
            self.assertEqual(state['units'], units)
            self.assertEqual((data['lengthCount'], data['areaCount'], data['textCount']), (5, 3, 1))
            self.assertAlmostEqual(data['totalLength'], (5+4*math.pi+14+7+12)*f)
            self.assertAlmostEqual(data['contourAreaSum'], (4*math.pi+12+6)*f*f)
            for e, item in zip(entities, data['items']):
                self.assertEqual(item['id'], e.id)
                self.assertEqual(item['length'], None if isinstance(e, TextEntity) else entity_perimeter(e))
                self.assertEqual(item['area'], entity_area(e) if item['closed'] else None)
                self.assertEqual(item['approximateBounds'], isinstance(e, TextEntity))

    def test_empty_text_only_and_open_zero_length_are_distinct(self):
        empty = self.bridge.snapshot()['selectionInspection']
        self.assertEqual(empty['items'], [])
        self.assertIsNone(empty['totalLength'])
        self.assertIsNone(empty['contourAreaSum'])
        for e in [TextEntity(text='note'), Line(start=Point(1, 1), end=Point(1, 1)), Polyline(points=[])]:
            self.bridge.session.project.entities = [e]
            self.bridge.session.selected_ids = {e.id}
            data = self.bridge.snapshot()['selectionInspection']
            self.assertEqual(data['totalLength'], None if isinstance(e, TextEntity) else 0)
            self.assertIsNone(data['contourAreaSum'])

    def test_gross_contour_areas_do_not_claim_union_or_subtract_holes(self):
        outer = Rectangle(corner_b=Point(10, 10), metadata={'compound_id':'c'})
        hole = Rectangle(corner_a=Point(2, 2), corner_b=Point(4, 4), metadata={'compound_id':'c'})
        overlap = Rectangle(corner_a=Point(5, 5), corner_b=Point(15, 15))
        self.bridge.session.project.entities = [outer, hole, overlap]
        self.bridge.session.selected_ids = {outer.id, hole.id, overlap.id}
        data = self.bridge.snapshot()['selectionInspection']
        self.assertEqual(data['contourAreaSum'], 204)
        self.assertEqual(data['areaCount'], 3)

    def test_inspection_never_changes_document_selection_history_or_readonly_layers(self):
        e = Circle(layer='locked', radius=3)
        e.metadata['annotation_hidden'] = True
        s = self.bridge.session
        s.project.entities = [e]
        s.project.metadata['layer_settings'] = {'locked': {'locked':True, 'visible':False}}
        s.project.operations = [RouterOffsetOperation(name='Existing job', tool_station=1, entity_ids=[e.id], target_depth=.1, safe_z=.2)]
        s.selected_ids = {e.id}
        self.bridge.snapshot()
        before = copy.deepcopy(s.project.to_dict())
        history = s._history_index
        revision = self.bridge.revision
        state = self.bridge.snapshot()
        self.assertTrue(state['readOnly'])
        self.assertEqual(state['selectionInspection']['items'][0]['id'], e.id)
        self.assertEqual(s.project.to_dict(), before)
        self.assertEqual(s.selected_ids, {e.id})
        self.assertEqual(s._history_index, history)
        self.assertEqual(self.bridge.revision, revision)

    def test_measurements_follow_edits_undo_and_save_without_persisting_display_fields(self):
        state = self.run_command('geometry.add_rectangle', x=0, y=0, width=10, height=5)
        identity = state['entities'][0]['id']
        self.run_command('selection.set', entity_ids=[identity])
        before = self.bridge.snapshot()['selectionInspection']
        self.assertEqual(before['contourAreaSum'], 50)
        state = self.run_command('design.scale', sx=2, sy=3)
        self.assertEqual(state['selectionInspection']['contourAreaSum'], 300)
        self.run_command('history.undo')
        self.assertEqual(self.bridge.snapshot()['selectionInspection'], before)
        self.run_command('history.redo')
        path = self.folder/'inspector.camjob.json'
        self.run_command('project.save', path=str(path))
        self.assertNotIn('selectionInspection', path.read_text())
        self.run_command('project.open', path=str(path))
        state = self.run_command('selection.set', entity_ids=[identity])
        self.assertEqual(state['selectionInspection']['contourAreaSum'], 300)
        self.assertFalse(state['dirty'])


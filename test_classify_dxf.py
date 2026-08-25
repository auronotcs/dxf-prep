import unittest

import classify_dxf as dxf


class TranslateAnalysisTests(unittest.TestCase):
    def test_translate_moves_entities_segments_and_component_bounds(self):
        analysis = {
            "entities": [
                {
                    "type": "LINE",
                    "groups": [(10, "2"), (20, "3"), (11, "5"), (21, "7")],
                },
                {
                    "type": "LWPOLYLINE",
                    "groups": [(10, "2"), (20, "3"), (10, "5"), (20, "7")],
                },
            ],
            "segments": [
                {"p1": (2.0, 3.0), "p2": (5.0, 7.0)},
                {
                    "p1": (4.0, 5.0),
                    "p2": (6.0, 7.0),
                    "center": (5.0, 6.0),
                    "bbox": (4.0, 5.0, 6.0, 7.0),
                },
            ],
            "components": [{"bbox": (2.0, 3.0, 6.0, 7.0), "layer": dxf.LAYER_OUTER}],
        }

        dxf.translate_analysis(analysis, -1.5, -2.5)

        self.assertEqual(analysis["segments"][0]["p1"], (0.5, 0.5))
        self.assertEqual(analysis["segments"][1]["center"], (3.5, 3.5))
        self.assertEqual(analysis["segments"][1]["bbox"], (2.5, 2.5, 4.5, 4.5))
        self.assertEqual(analysis["components"][0]["bbox"], (0.5, 0.5, 4.5, 4.5))
        self.assertEqual(
            analysis["entities"][1]["groups"],
            [(10, "0.500000"), (20, "0.500000"), (10, "3.500000"), (20, "4.500000")],
        )


def rectangle_segments(bounds, color, first_entity):
    min_x, min_y, max_x, max_y = bounds
    points = [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]
    return [
        {
            "entity": first_entity + index,
            "type": "LINE",
            "color": color,
            "p1": points[index],
            "p2": points[(index + 1) % 4],
        }
        for index in range(4)
    ]


class TopologicalClassificationTests(unittest.TestCase):
    def test_nested_offset_pairs_ignore_arbitrary_colors(self):
        segments = []
        for index, (bounds, color) in enumerate(
            [
                ((0.0, 0.0, 10.0, 10.0), "6"),
                ((0.07, 0.07, 9.93, 9.93), "2"),
                ((3.0, 3.0, 7.0, 7.0), "4"),
                ((3.07, 3.07, 6.93, 6.93), "1"),
            ]
        ):
            segments.extend(rectangle_segments(bounds, color, index * 4))
        components = dxf.connected_components(segments)

        dxf.classify_components_topologically(components, segments)

        layers = {tuple(round(value, 2) for value in component["bbox"]): component["layer"] for component in components}
        self.assertEqual(layers[(0.0, 0.0, 10.0, 10.0)], dxf.LAYER_OUTER)
        self.assertEqual(layers[(0.07, 0.07, 9.93, 9.93)], dxf.LAYER_INNER)
        self.assertEqual(layers[(3.0, 3.0, 7.0, 7.0)], dxf.LAYER_INNER)
        self.assertEqual(layers[(3.07, 3.07, 6.93, 6.93)], dxf.LAYER_OUTER)

    def test_unpaired_nested_contour_is_profile_hole(self):
        segments = rectangle_segments((0.0, 0.0, 10.0, 10.0), "2", 0)
        segments.extend(rectangle_segments((3.0, 3.0, 7.0, 7.0), "6", 4))
        components = dxf.connected_components(segments)

        dxf.classify_components_topologically(components, segments)

        layers = {tuple(component["bbox"]): component["layer"] for component in components}
        self.assertEqual(layers[(0.0, 0.0, 10.0, 10.0)], dxf.LAYER_OUTER)
        self.assertEqual(layers[(3.0, 3.0, 7.0, 7.0)], dxf.LAYER_PROFILE_HOLES)

    def test_shared_exterior_can_have_multiple_partial_inset_partners(self):
        segments = rectangle_segments((0.0, 0.0, 10.0, 20.0), "5", 0)
        segments.extend(rectangle_segments((0.5, 0.5, 9.5, 9.5), "1", 4))
        segments.extend(rectangle_segments((0.5, 10.5, 9.5, 19.5), "6", 8))
        components = dxf.connected_components(segments)

        dxf.classify_components_topologically(components, segments)

        layers = {tuple(component["bbox"]): component["layer"] for component in components}
        self.assertEqual(layers[(0.0, 0.0, 10.0, 20.0)], dxf.LAYER_OUTER)
        self.assertEqual(layers[(0.5, 0.5, 9.5, 9.5)], dxf.LAYER_INNER)
        self.assertEqual(layers[(0.5, 10.5, 9.5, 19.5)], dxf.LAYER_INNER)

    def test_small_open_marking_ignores_color(self):
        segments = [{"entity": 0, "type": "LINE", "color": "5", "p1": (0.0, 0.0), "p2": (0.5, 0.5)}]
        components = dxf.connected_components(segments)

        dxf.classify_components_topologically(components, segments)

        self.assertEqual(components[0]["layer"], dxf.LAYER_ID_MARKING)

    def test_horizontal_semicircles_are_recognized_as_quarter_inch_drill(self):
        entities = [
            {
                "type": "ARC",
                "groups": [(62, "6"), (10, "5"), (20, "7"), (40, "0.125"), (50, "0"), (51, "180")],
            },
            {
                "type": "ARC",
                "groups": [(62, "2"), (10, "5"), (20, "7"), (40, "0.125"), (50, "180"), (51, "360")],
            },
        ]
        segments = dxf.build_segments(entities)
        components = dxf.connected_components(segments)

        dxf.classify_components_topologically(components, segments)

        self.assertEqual(components[0]["bbox"], (4.875, 6.875, 5.125, 7.125))
        self.assertEqual(components[0]["layer"], dxf.LAYER_DRILL)


if __name__ == "__main__":
    unittest.main()

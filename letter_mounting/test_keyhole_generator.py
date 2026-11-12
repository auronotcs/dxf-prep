import unittest
from pathlib import Path

from keyhole_generator import (
    anatomical_keyhole_targets,
    approximate_led_centerline_points,
    choose_electrical_diameter,
    choose_mount_features,
    desired_keyhole_count,
    effective_mount_spacing,
    estimate_channel_letter_mass,
    keyhole_template_geometry,
    maximum_boundary_reach,
    logo_indices_from_bounds,
    MountFeature,
    normalized_electrical_target,
    normalized_keyhole_targets,
    procedural_back_labels,
    robust_back_polygons,
    targeted_keyhole_layout,
    validate_mount_layout,
)
import ezdxf
from shapely.geometry import Point, box


class ElectricalDiameterSelectionTests(unittest.TestCase):
    def test_logo_preview_bounds_match_recovered_geometry(self):
        backs = [box(10, 0, 14, 8), box(0, 0, 8, 8)]
        self.assertEqual(logo_indices_from_bounds(backs, [(0.001, 0, 8, 8)]), {1})

    def test_explicit_override_wins(self):
        diameter, source = choose_electrical_diameter([(0, 0, 0.5)], 0.375)
        self.assertEqual(diameter, 0.375)
        self.assertEqual(source, "explicit override")

    def test_consistent_reference_size_is_preserved(self):
        features = [(0, 0, 0.499), (1, 1, 0.5), (2, 2, 0.501)]
        diameter, source = choose_electrical_diameter(features, None)
        self.assertAlmostEqual(diameter, 0.5)
        self.assertEqual(source, "reference artwork")

    def test_inconsistent_reference_falls_back_to_production_default(self):
        diameter, source = choose_electrical_diameter([(0, 0, 0.375), (1, 1, 0.5)], None)
        self.assertEqual(diameter, 0.375)
        self.assertEqual(source, "production default")


class ProceduralKeyholeCountTests(unittest.TestCase):
    def test_large_logo_uses_six_keyholes(self):
        self.assertEqual(desired_keyhole_count("SHAPE_1", box(0, 0, 58, 50)), 6)

    def test_narrow_letter_uses_two_keyholes(self):
        self.assertEqual(desired_keyhole_count("I", box(0, 0, 2.2, 10)), 2)

    def test_apostrophe_uses_two_mounts(self):
        self.assertEqual(desired_keyhole_count("'", box(0, 0, 3, 7)), 2)

    def test_tiny_detached_dot_still_uses_two_mounts(self):
        self.assertEqual(desired_keyhole_count("i", Point(0, 0).buffer(1.5)), 2)

    def test_tiny_back_uses_tool_footprint_spacing_exception(self):
        self.assertEqual(effective_mount_spacing(Point(0, 0).buffer(1.5), 2.7), 1.0)
        self.assertEqual(effective_mount_spacing(box(0, 0, 10, 10), 2.7), 2.7)

    def test_apostrophe_uses_dot_spacing_exception(self):
        self.assertEqual(effective_mount_spacing(box(0, 0, 3.7, 7.0), 2.7, "'"), 1.0)

    def test_small_standard_letter_uses_three_keyholes(self):
        self.assertEqual(desired_keyhole_count("R", box(0, 0, 7, 10)), 3)

    def test_medium_a_uses_three_keyholes(self):
        self.assertEqual(desired_keyhole_count("A", box(0, 0, 12, 14)), 3)

    def test_twenty_five_inch_standard_letter_stays_at_three_keyholes(self):
        self.assertEqual(desired_keyhole_count("A", box(0, 0, 20, 25)), 3)

    def test_thirty_six_inch_standard_letter_uses_four_keyholes(self):
        self.assertEqual(desired_keyhole_count("A", box(0, 0, 24, 36)), 4)

    def test_mass_can_raise_the_geometry_minimum(self):
        self.assertEqual(desired_keyhole_count("A", box(0, 0, 12, 14), 16.0, 5.0), 4)

    def test_channel_letter_mass_uses_supplied_construction_recipe(self):
        estimate = estimate_channel_letter_mass(box(0, 0, 10, 10), led_weight_per_foot=0.030)
        self.assertAlmostEqual(estimate.face_area_sq_in, 100.0)
        self.assertAlmostEqual(estimate.perimeter_in, 40.0)
        self.assertAlmostEqual(estimate.led_length_ft, 40.0 * 0.5 / 12.0)
        expected = (
            100.0 * (4.5 / 25.4) * 0.04335
            + 100.0 * 0.125 * 0.0975
            + 40.0 * 2.0 * 0.063 * 2.0 * 0.0975
            + (40.0 * 0.5 / 12.0) * 0.030
        )
        self.assertAlmostEqual(estimate.total_lb, expected)

    def test_electrical_target_uses_lower_right_terminal_of_e(self):
        self.assertEqual(normalized_electrical_target("E"), (0.84, 0.08))

    def test_electrical_target_uses_lower_left_terminal_of_s(self):
        self.assertEqual(normalized_electrical_target("S"), (0.18, 0.08))

    def test_a_uses_apex_and_both_legs(self):
        self.assertEqual(
            normalized_keyhole_targets("A", 3),
            [(0.50, 0.76), (0.18, 0.20), (0.82, 0.20)],
        )

    def test_serif_l_uses_its_foot_instead_of_three_stem_mounts(self):
        self.assertEqual(
            normalized_keyhole_targets("L", 3),
            [(0.52, 0.84), (0.28, 0.22), (0.80, 0.14)],
        )

    def test_curved_c_targets_substantial_upper_middle_and_lower_arcs(self):
        self.assertEqual(
            normalized_keyhole_targets("C", 3),
            [(0.72, 0.84), (0.16, 0.50), (0.70, 0.16)],
        )

    def test_script_b_prioritizes_two_stem_points_and_the_bowl(self):
        self.assertEqual(
            anatomical_keyhole_targets("b", 3),
            [(0.25, 0.78), (0.22, 0.22), (0.76, 0.48)],
        )

    def test_script_s_avoids_its_narrow_terminal_tips(self):
        self.assertEqual(
            anatomical_keyhole_targets("s", 3),
            [(0.78, 0.78), (0.50, 0.50), (0.20, 0.22)],
        )

    def test_script_n_prioritizes_its_stems(self):
        self.assertEqual(
            anatomical_keyhole_targets("n", 3),
            [(0.27, 0.78), (0.27, 0.22), (0.73, 0.78)],
        )

    def test_lowercase_i_targets_the_stem(self):
        self.assertEqual(
            anatomical_keyhole_targets("i", 2),
            [(0.50, 0.76), (0.50, 0.24)],
        )

    def test_script_g_uses_both_bowl_sides_leg_and_terminal(self):
        self.assertEqual(desired_keyhole_count("g", box(0, 0, 20, 28)), 4)
        self.assertEqual(
            anatomical_keyhole_targets("g", 4),
            [(0.25, 0.72), (0.72, 0.72), (0.30, 0.40), (0.72, 0.12)],
        )

    def test_script_o_uses_two_upper_points_and_thicker_side(self):
        self.assertEqual(
            anatomical_keyhole_targets("o", 3),
            [(0.26, 0.72), (0.72, 0.76), (0.70, 0.28)],
        )

    def test_script_a_uses_two_stem_points_and_the_bowl(self):
        self.assertEqual(
            anatomical_keyhole_targets("a", 3),
            [(0.72, 0.78), (0.64, 0.22), (0.25, 0.50)],
        )

    def test_led_guide_tracks_the_medial_ridge(self):
        points = approximate_led_centerline_points(box(0, 0, 10, 2))
        self.assertTrue(points)
        self.assertLess(max(abs(y - 1.0) for _x, y in points), 0.5)

    def test_i_mounts_leave_more_than_module_plus_hardware(self):
        targets = normalized_keyhole_targets("I", 2)
        self.assertGreater((targets[0][1] - targets[1][1]) * 9.463, 2.70)

    def test_boundary_reach_reports_uncovered_edge(self):
        reach, _ = maximum_boundary_reach(
            box(0, 0, 20, 2),
            [MountFeature("round", (2, 1))],
        )
        self.assertGreater(reach, 17.9)

    def test_layout_validation_enforces_center_spacing(self):
        with self.assertRaises(ValueError):
            validate_mount_layout(
                box(0, 0, 4, 2),
                "TEST",
                [MountFeature("round", (1, 1)), MountFeature("round", (3, 1))],
                2.70,
                10.0,
            )

    def test_narrow_keyhole_clearance_becomes_round_mount(self):
        footprint = box(-0.2815, -0.53125, 0.2815, 0.53125)
        features = choose_mount_features(box(0, 0, 2, 1.5), [(1, 0.75)], footprint, Point(0, 0))
        self.assertEqual(features[0].kind, "round")

    def test_well_clear_keyhole_is_retained(self):
        footprint = box(-0.2815, -0.53125, 0.2815, 0.53125)
        features = choose_mount_features(box(0, 0, 4, 4), [(2, 2)], footprint, Point(0, 0))
        self.assertEqual(features[0].kind, "keyhole")

    def test_round_fallback_cannot_violate_edge_clearance(self):
        footprint = box(-0.2815, -0.53125, 0.2815, 0.53125)
        with self.assertRaises(ValueError):
            choose_mount_features(
                box(0, 0, 1.5, 1.5),
                [(0.30, 0.75)],
                footprint,
                Point(0, 0),
                coil_clearance=0.625,
            )

    def test_detached_i_dot_prefers_two_clear_keyholes(self):
        footprint = box(-0.2815, -0.53125, 0.2815, 0.53125)
        dot = Point(0, 0).buffer(2.0, quad_segs=64)
        centers = targeted_keyhole_layout(
            dot,
            "i",
            footprint,
            Point(0, 0),
            minimum_spacing=1.0,
            preferred_edge_clearance=0.625,
        )
        features = choose_mount_features(
            dot,
            centers,
            footprint,
            Point(0, 0),
            coil_clearance=0.625,
        )
        self.assertEqual([feature.kind for feature in features], ["keyhole", "keyhole"])
        self.assertGreaterEqual(
            ((centers[0][0] - centers[1][0]) ** 2 + (centers[0][1] - centers[1][1]) ** 2) ** 0.5,
            1.0,
        )

    def test_missing_reference_openings_falls_back_to_production_default(self):
        diameter, source = choose_electrical_diameter([], None)
        self.assertEqual(diameter, 0.375)
        self.assertEqual(source, "production default")


class SissyKeyholeRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent
        source = ezdxf.readfile(root / "test_fixtures" / "sissys_hole_file_test.dxf")
        cls.backs, cls.labels = procedural_back_labels(
            robust_back_polygons(source.modelspace()),
            "Sissy's Log Cabin",
        )
        template = ezdxf.readfile(root / "Keyhole example.dxf")
        cls.footprint, cls.template_center = keyhole_template_geometry(template)

    def generated_mounts(self, one_based_index):
        back = self.backs[one_based_index - 1]
        label = self.labels[one_based_index - 1]
        spacing = effective_mount_spacing(back, 2.70, label)
        centers = targeted_keyhole_layout(
            back,
            label,
            self.footprint,
            self.template_center,
            minimum_spacing=spacing,
            preferred_edge_clearance=0.625,
            circular_mount_diameter=0.250,
            led_guide_points=approximate_led_centerline_points(back),
        )
        return back, choose_mount_features(
            back,
            centers,
            self.footprint,
            self.template_center,
            coil_clearance=0.625,
            circular_diameter=0.250,
        )

    def test_reported_letters_use_only_clear_keyholes(self):
        for index in (2, 3, 6, 7, 9, 11, 12, 17):
            with self.subTest(index=index, label=self.labels[index - 1]):
                _back, mounts = self.generated_mounts(index)
                self.assertTrue(mounts)
                self.assertTrue(all(mount.kind == "keyhole" for mount in mounts))

    def test_l_follows_its_slanted_stem_then_uses_the_foot(self):
        back, mounts = self.generated_mounts(9)
        normalized = [
            (
                (mount.center[0] - back.bounds[0]) / (back.bounds[2] - back.bounds[0]),
                (mount.center[1] - back.bounds[1]) / (back.bounds[3] - back.bounds[1]),
            )
            for mount in mounts
        ]
        self.assertGreater(normalized[0][0], normalized[1][0])
        self.assertGreater(normalized[0][1], 0.78)
        self.assertLess(normalized[1][1], 0.28)
        self.assertGreater(normalized[2][0], 0.75)
        self.assertLess(normalized[2][1], 0.15)

    def test_g_supports_both_bowl_sides_middle_leg_and_terminal(self):
        back, mounts = self.generated_mounts(11)
        normalized = [
            (
                (mount.center[0] - back.bounds[0]) / (back.bounds[2] - back.bounds[0]),
                (mount.center[1] - back.bounds[1]) / (back.bounds[3] - back.bounds[1]),
            )
            for mount in mounts
        ]
        self.assertEqual(len(normalized), 4)
        self.assertLess(normalized[0][0], 0.40)
        self.assertGreater(normalized[1][0], 0.60)
        self.assertGreater(normalized[0][1], 0.60)
        self.assertGreater(normalized[1][1], 0.60)
        self.assertLess(normalized[2][0], 0.40)
        self.assertGreater(normalized[2][1], 0.30)
        self.assertLess(normalized[2][1], 0.50)
        self.assertGreater(normalized[3][0], 0.60)
        self.assertLess(normalized[3][1], 0.20)

    def test_apostrophe_uses_two_clear_keyholes_at_dot_spacing(self):
        _back, mounts = self.generated_mounts(7)
        self.assertEqual([mount.kind for mount in mounts], ["keyhole", "keyhole"])
        self.assertGreaterEqual(
            ((mounts[0].center[0] - mounts[1].center[0]) ** 2
             + (mounts[0].center[1] - mounts[1].center[1]) ** 2) ** 0.5,
            1.0,
        )

    def test_n_has_two_mounts_in_main_stem_and_one_in_upper_opposite_stem(self):
        back, mounts = self.generated_mounts(17)
        normalized = [
            (
                (mount.center[0] - back.bounds[0]) / (back.bounds[2] - back.bounds[0]),
                (mount.center[1] - back.bounds[1]) / (back.bounds[3] - back.bounds[1]),
            )
            for mount in mounts
        ]
        self.assertLess(abs(normalized[0][0] - normalized[1][0]), 0.08)
        self.assertGreater(normalized[0][1], normalized[1][1])
        self.assertGreater(normalized[2][0], 0.65)
        self.assertGreater(normalized[2][1], 0.70)


if __name__ == "__main__":
    unittest.main()

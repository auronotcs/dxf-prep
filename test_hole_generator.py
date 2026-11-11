from __future__ import annotations

import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "holegen_libs"))

from shapely.geometry import box
from shapely.ops import unary_union

from hole_generator import (
    build_alignment_groups,
    labels_for_text_components,
    labels_with_logos,
    order_polygons_for_text,
)


def e_shape(x: float, y: float, height: float):
    scale = height / 18.0
    return unary_union(
        [
            box(x, y, x + 2 * scale, y + height),
            box(x, y + 16 * scale, x + 12 * scale, y + height),
            box(x, y + 8 * scale, x + 10 * scale, y + 10 * scale),
            box(x, y, x + 12 * scale, y + 2 * scale),
        ]
    )


class AlignmentClusteringTests(unittest.TestCase):
    def test_detached_lowercase_i_dot_is_one_text_character(self):
        stem = box(0, 0, 1, 5)
        dot = box(0.35, 6, 0.95, 6.6)
        following = box(2, 0, 5, 5)
        self.assertEqual(labels_for_text_components("is", [stem, dot, following]), ["i", "i", "s"])

    def test_unrelated_extra_piece_is_not_silently_assigned(self):
        with self.assertRaisesRegex(ValueError, "closed text piece"):
            labels_for_text_components("AB", [box(0, 0, 1, 5), box(2, 0, 3, 5), box(8, 0, 9, 1)])

    def test_selected_logo_is_interleaved_without_fake_text_character(self):
        self.assertEqual(labels_with_logos("AB C", 4, {1}), ["A", "SHAPE_1", "B", "C"])

    def test_shape_count_mismatch_is_a_reportable_value_error(self):
        with self.assertRaisesRegex(ValueError, "selected as logo"):
            labels_with_logos("ABC", 5, {0})

    def test_multiple_lines_sizes_and_punctuation_are_independent(self):
        # Deliberately shuffled source order: extraction order must not matter.
        source = [
            box(20, -30, 30, -18),
            e_shape(0, 0, 18),
            box(58, -1, 60, 3),  # comma near the 9-inch copy
            box(18, 0, 30, 18),
            box(35, 13, 37, 18),  # apostrophe near the 18-inch copy
            e_shape(48, 0, 9),
            box(5, -30, 15, -18),
        ]
        ordered, lines = order_polygons_for_text(source)
        labels = ["E", "F", "'", "E", ",", "G", "H"]
        groups, rows_by_index = build_alignment_groups(labels, ordered, lines)

        self.assertEqual(lines, [[0, 1, 2, 3, 4], [5, 6]])
        self.assertEqual(len(groups), 3)
        self.assertEqual(sorted(rows_by_index), list(range(7)))

        group_characters = ["".join(labels[index] for index in sorted(group.indices)) for group in groups]
        self.assertEqual(group_characters, ["EF'", "E,", "GH"])
        self.assertEqual(tuple(round(value, 3) for value in groups[0].rows), (1.0, 9.0, 17.0))
        self.assertEqual(tuple(round(value, 3) for value in groups[1].rows), (0.5, 4.5, 8.5))
        self.assertNotEqual(groups[0].rows, groups[2].rows)


if __name__ == "__main__":
    unittest.main()

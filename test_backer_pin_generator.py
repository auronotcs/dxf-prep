import math
import tempfile
import unittest
from pathlib import Path

import ezdxf
from shapely.affinity import translate
from shapely.geometry import Point, box

from backer_pin_generator import (
    BACKER_PIN_LAYER,
    LETTER_PIN_LAYER,
    discover_backer_panels,
    equivalent_component_geometry,
    generate_backer_pin_job,
    match_selected_panels,
    place_roll_pin_holes,
)


def add_rectangle(modelspace, min_x, min_y, max_x, max_y):
    modelspace.add_lwpolyline(
        [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)],
        close=True,
    )


def make_two_panel_document():
    document = ezdxf.new("R2000")
    modelspace = document.modelspace()
    for offset_x in (0.0, 30.0):
        add_rectangle(modelspace, offset_x, 0.0, offset_x + 20.0, 20.0)
        add_rectangle(modelspace, offset_x + 3.0, 8.0, offset_x + 6.0, 13.0)
        add_rectangle(modelspace, offset_x + 11.0, 8.0, offset_x + 14.0, 13.0)
    return document


class RollPinPlacementTests(unittest.TestCase):
    def test_pair_obeys_material_and_spacing_clearance(self):
        component = box(0.0, 0.0, 4.0, 2.0)
        centers, requested = place_roll_pin_holes(component, 0.125, 0.125, 0.5)

        self.assertEqual(requested, 2)
        self.assertEqual(len(centers), 2)
        required_center_clearance = 0.125 / 2 + 0.125
        for center in centers:
            self.assertGreaterEqual(
                component.boundary.distance(Point(center)) + 1e-6,
                required_center_clearance,
            )
        self.assertGreaterEqual(math.dist(*centers) + 1e-6, 0.5)

    def test_translated_identical_components_receive_identical_offsets(self):
        original = box(0.0, 0.0, 4.0, 2.0)
        shifted = translate(original, xoff=17.25, yoff=-6.5)
        original_centers, original_requested = place_roll_pin_holes(original, 0.125, 0.125, 0.5)
        shifted_centers, shifted_requested = place_roll_pin_holes(shifted, 0.125, 0.125, 0.5)

        shifted_local = [(round(x - 17.25, 4), round(y + 6.5, 4)) for x, y in shifted_centers]
        self.assertEqual(shifted_local, original_centers)
        self.assertEqual(shifted_requested, original_requested)

    def test_equivalent_copy_tolerates_small_dxf_tessellation_differences(self):
        original = box(0.0, 0.0, 3.0, 5.0)
        near_copy = box(12.0, 7.0, 15.001, 11.999)
        different_shape = box(12.0, 7.0, 16.0, 11.0)

        self.assertTrue(equivalent_component_geometry(original, near_copy))
        self.assertFalse(equivalent_component_geometry(original, different_shape))


class BackerJobTests(unittest.TestCase):
    def test_discovers_and_selects_multiple_backers(self):
        document = make_two_panel_document()
        panels = discover_backer_panels(document.modelspace())

        self.assertEqual(len(panels), 2)
        self.assertEqual([len(panel.letters) for panel in panels], [2, 2])
        selected = match_selected_panels(panels, [panels[1].polygon.bounds])
        self.assertEqual([panel.index for panel in selected], [2])

    def test_writes_three_registered_and_auditable_outputs(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "two-panels.dxf"
            master = root / "master.dxf"
            letters = root / "letters.dxf"
            backers = root / "backers.dxf"
            make_two_panel_document().saveas(source)

            result = generate_backer_pin_job(
                source,
                master,
                letters,
                backers,
                letter_hole_diameter=0.125,
                backer_hole_diameter=0.140,
                letter_edge_clearance=0.125,
                minimum_pin_spacing=0.5,
            )

            self.assertEqual(len(result["panels"]), 2)
            self.assertEqual(result["total_components"], 4)
            self.assertEqual(result["total_pins"], 8)
            self.assertFalse(result["single_pin_components"])

            documents = [ezdxf.readfile(path) for path in (master, letters, backers)]
            for document in documents:
                self.assertFalse(document.audit().has_errors)
                self.assertEqual(len(list(document.groups)), 2)

            master_document, letters_document, backers_document = documents

            def centers(document, layer):
                return {
                    (round(entity.dxf.center.x, 4), round(entity.dxf.center.y, 4))
                    for entity in document.modelspace().query(f'CIRCLE[layer=="{layer}"]')
                }

            letter_centers = centers(letters_document, LETTER_PIN_LAYER)
            backer_centers = centers(backers_document, BACKER_PIN_LAYER)
            self.assertEqual(letter_centers, backer_centers)
            self.assertEqual(len(letter_centers), 8)
            self.assertEqual(centers(master_document, LETTER_PIN_LAYER), letter_centers)
            self.assertEqual(centers(master_document, BACKER_PIN_LAYER), backer_centers)

            self.assertEqual(len(master_document.modelspace()), 22)
            self.assertEqual(len(letters_document.modelspace()), 12)
            self.assertEqual(len(backers_document.modelspace()), 10)

    def test_near_identical_copies_reuse_the_same_local_pin_layout(self):
        document = ezdxf.new("R2000")
        modelspace = document.modelspace()
        add_rectangle(modelspace, 0.0, 0.0, 20.0, 20.0)
        add_rectangle(modelspace, 3.0, 8.0, 6.0, 13.0)
        add_rectangle(modelspace, 11.0, 8.0, 14.001, 12.999)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "near-copies.dxf"
            document.saveas(source)
            result = generate_backer_pin_job(
                source,
                root / "master.dxf",
                root / "letters.dxf",
                root / "backers.dxf",
                letter_hole_diameter=0.125,
                backer_hole_diameter=0.140,
                letter_edge_clearance=0.125,
                minimum_pin_spacing=0.5,
            )

            local_layouts = []
            for component, centers in zip(result["panels"][0].letters, result["layouts"][0]):
                min_x, min_y, _max_x, _max_y = component.bounds
                local_layouts.append(
                    [(round(x - min_x, 4), round(y - min_y, 4)) for x, y in centers]
                )
            self.assertEqual(local_layouts[0], local_layouts[1])


if __name__ == "__main__":
    unittest.main()

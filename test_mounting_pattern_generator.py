import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "holegen_libs"))

import ezdxf

from mounting_pattern_generator import (
    GUIDE_LAYER,
    GUIDE_LINETYPE,
    LABEL_LAYER,
    add_pattern_guides,
    ensure_pattern_resources,
    generate_pattern,
)


class MountingPatternTests(unittest.TestCase):
    def make_document(self):
        doc = ezdxf.new("R2000")
        doc.modelspace().add_lwpolyline([(0, 0), (20, 0), (20, 10), (0, 10)], close=True)
        return doc

    def test_adds_frame_centers_and_vertical_arial_label(self):
        doc = self.make_document()
        ensure_pattern_resources(doc)
        bounds, entities = add_pattern_guides(doc, "TEST PATTERN")
        self.assertEqual(bounds, (0.0, 0.0, 20.0, 10.0))
        self.assertGreater(len(entities), 4)
        guides = [entity for entity in entities if entity.dxf.layer == GUIDE_LAYER]
        labels = [entity for entity in entities if entity.dxf.layer == LABEL_LAYER]
        self.assertGreater(len(guides), 100)
        self.assertTrue(all(entity.dxftype() == "LINE" for entity in guides))
        self.assertTrue(all(entity.dxf.linetype == "Continuous" for entity in guides))
        self.assertEqual(labels[0].dxf.style, "ARIAL")
        self.assertEqual(labels[0].dxf.rotation, 90.0)
        self.assertEqual(labels[0].dxf.text, "TEST PATTERN")

    def test_resources_keep_named_linetype_but_layer_uses_physical_segments(self):
        doc = self.make_document()
        ensure_pattern_resources(doc)
        self.assertIn(GUIDE_LINETYPE, doc.linetypes)
        self.assertEqual(doc.layers.get(GUIDE_LAYER).dxf.linetype, "Continuous")

    def test_compact_guides_use_only_six_continuous_lines(self):
        doc = self.make_document()
        ensure_pattern_resources(doc)
        _bounds, entities = add_pattern_guides(doc, "COMPACT", physical_dashes=False)
        guides = [entity for entity in entities if entity.dxf.layer == GUIDE_LAYER]
        self.assertEqual(len(guides), 6)
        self.assertTrue(all(entity.dxftype() == "LINE" for entity in guides))
        self.assertTrue(all(entity.dxf.linetype == "Continuous" for entity in guides))

    def test_generated_mounting_features_survive_pattern_output(self):
        feature_layers = {
            "STUD_HOLES",
            "KEYHOLES",
            "ELECTRICAL_HOLES",
            "MOUNTING_HOLES",
            "LETTER_PIN_HOLES",
            "BACKER_CLEARANCE_HOLES",
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "generated-master.dxf"
            output = root / "pattern.dxf"
            preview = root / "pattern.png"
            doc = self.make_document()
            for index, layer in enumerate(sorted(feature_layers)):
                doc.layers.add(layer)
                doc.modelspace().add_circle((2.0 + index * 2.0, 5.0), 0.25, dxfattribs={"layer": layer})
            doc.saveas(source)

            generate_pattern(
                source,
                output,
                preview,
                "FEATURE TEST",
                1.0,
                0.75,
                False,
                physical_dashes=False,
            )

            result = ezdxf.readfile(output)
            surviving_layers = {
                entity.dxf.layer
                for entity in result.modelspace()
                if entity.dxftype() == "CIRCLE"
            }
            self.assertEqual(surviving_layers, feature_layers)


if __name__ == "__main__":
    unittest.main()

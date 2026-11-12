import tempfile
import unittest
from pathlib import Path

from letter_mounting_automation_app import (
    BACKER_MODE,
    KEYHOLE_MODE,
    PATTERN_MODE,
    STUD_MODE,
    load_last_open_directory,
    pattern_source_for_review,
    save_last_open_directory,
)


class LastOpenDirectorySettingsTests(unittest.TestCase):
    def test_saved_directory_is_loaded(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            dxf_directory = root / "graphic files" / "customer"
            dxf_directory.mkdir(parents=True)
            settings_path = root / "preferences" / "settings.json"

            save_last_open_directory(dxf_directory, settings_path)

            self.assertEqual(load_last_open_directory(settings_path), dxf_directory.resolve())

    def test_missing_saved_directory_falls_back(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings_path = root / "settings.json"
            settings_path.write_text(
                '{"last_open_directory": "C:/folder/that/does/not/exist"}',
                encoding="utf-8",
            )

            self.assertIsNone(load_last_open_directory(settings_path))

    def test_invalid_settings_are_ignored(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings_path = Path(temporary_directory) / "settings.json"
            settings_path.write_text("not json", encoding="utf-8")

            self.assertIsNone(load_last_open_directory(settings_path))


class PatternSourceTests(unittest.TestCase):
    def test_generated_master_is_used_after_each_hole_workflow(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.dxf"
            generated = root / "generated-master.dxf"
            source.touch()
            generated.touch()

            for mode in (STUD_MODE, KEYHOLE_MODE, BACKER_MODE):
                with self.subTest(mode=mode):
                    review = {
                        "mode": mode,
                        "artifacts": [{"role": "master", "path": generated}],
                    }
                    self.assertEqual(pattern_source_for_review(source, review), generated)

    def test_original_source_is_the_fallback_without_a_generated_master(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.dxf"
            source.touch()

            self.assertEqual(pattern_source_for_review(source, None), source)
            self.assertEqual(
                pattern_source_for_review(
                    source,
                    {"mode": PATTERN_MODE, "artifacts": [{"role": "pattern", "path": root / "x.dxf"}]},
                ),
                source,
            )
            self.assertEqual(
                pattern_source_for_review(
                    source,
                    {"mode": STUD_MODE, "artifacts": [{"role": "master", "path": root / "missing.dxf"}]},
                ),
                source,
            )

if __name__ == "__main__":
    unittest.main()

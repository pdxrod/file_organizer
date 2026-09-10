"""Tests for phone_daemon.load_config config resolution and recovery."""

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import phone_daemon  # noqa: E402

# Keep test output quiet — load_config logs warnings on recovery paths.
logging.getLogger("phone_daemon").addHandler(logging.NullHandler())

TEMPLATE_YAML = (
    "target_directory: /storage/emulated/0/staging\n"
    "min_age_minutes: 5\n"
)


class LoadConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.config = self.dir / "phone_daemon_config.yaml"
        self.template = self.dir / "phone_daemon_config.template.yaml"

    def test_missing_config_is_created_from_template(self):
        self.template.write_text(TEMPLATE_YAML)
        cfg = phone_daemon.load_config(str(self.config))
        self.assertEqual(cfg["target_directory"], "/storage/emulated/0/staging")
        self.assertTrue(self.config.exists())
        self.assertNotEqual(self.config.stat().st_size, 0)

    def test_empty_config_recovers_from_template_instead_of_crashing(self):
        """Regression: an empty config used to die with
        'JSONDecodeError: Expecting value: line 1 column 1 (char 0)'."""
        self.template.write_text(TEMPLATE_YAML)
        self.config.write_text("")
        cfg = phone_daemon.load_config(str(self.config))
        self.assertEqual(cfg["target_directory"], "/storage/emulated/0/staging")
        # The empty file must have been replaced with real content.
        self.assertGreater(self.config.stat().st_size, 0)

    def test_whitespace_only_config_recovers_from_template(self):
        self.template.write_text(TEMPLATE_YAML)
        self.config.write_text("\n  \n")
        cfg = phone_daemon.load_config(str(self.config))
        self.assertEqual(cfg["min_age_minutes"], 5)

    def test_empty_config_without_template_falls_back_to_defaults(self):
        self.config.write_text("")
        original_find = phone_daemon._find_template
        phone_daemon._find_template = lambda p: None  # no template anywhere
        try:
            cfg = phone_daemon.load_config(str(self.config))
        finally:
            phone_daemon._find_template = original_find
        self.assertIn("target_directory", cfg)
        self.assertIn("source_directories", cfg)

    def test_valid_config_parses_unchanged(self):
        self.config.write_text(TEMPLATE_YAML)
        cfg = phone_daemon.load_config(str(self.config))
        self.assertEqual(cfg["target_directory"], "/storage/emulated/0/staging")
        self.assertEqual(cfg["min_age_minutes"], 5)


if __name__ == "__main__":
    unittest.main()

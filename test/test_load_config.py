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

    def test_yaml_config_without_pyyaml_parses_via_builtin_parser(self):
        """Regression: with PyYAML missing, a comment-first YAML config used to
        die as a JSON decode error at 'line 1 column 1'. Now the built-in
        parser reads the supported config subset."""
        self.config.write_text(
            "# comment first\n"
            "target_directory: /storage/emulated/0/staging  # trailing\n"
            "min_age_minutes: 5\n"
            "delete_after_copy: false\n"
            "include_extensions: []\n"
            "exclude_extensions: [\".tmp\", \".part\"]\n"
            "source_directories:\n"
            "  # inline comment between items\n"
            "  - \"/storage/emulated/0/DCIM\"\n"
            "  - \"/storage/emulated/0/Pictures\"\n"
        )
        orig_yaml = sys.modules.get("yaml")
        sys.modules["yaml"] = None  # makes `import yaml` raise ImportError
        try:
            cfg = phone_daemon.load_config(str(self.config))
        finally:
            if orig_yaml is None:
                sys.modules.pop("yaml", None)
            else:
                sys.modules["yaml"] = orig_yaml
        self.assertEqual(cfg["target_directory"], "/storage/emulated/0/staging")
        self.assertEqual(cfg["min_age_minutes"], 5)
        self.assertIs(cfg["delete_after_copy"], False)
        self.assertEqual(cfg["include_extensions"], [])
        self.assertEqual(cfg["exclude_extensions"], [".tmp", ".part"])
        self.assertEqual(
            cfg["source_directories"][:2],
            ["/storage/emulated/0/DCIM", "/storage/emulated/0/Pictures"],
        )

    def test_unparseable_config_without_pyyaml_exits_with_install_hint(self):
        """Content outside the subset and not JSON must exit with an
        actionable hint rather than a bare decode error."""
        self.config.write_text("key: [unterminated\n")
        orig_yaml = sys.modules.get("yaml")
        sys.modules["yaml"] = None
        try:
            with self.assertLogs("phone_daemon", level="ERROR") as cm:
                with self.assertRaises(SystemExit):
                    phone_daemon.load_config(str(self.config))
        finally:
            if orig_yaml is None:
                sys.modules.pop("yaml", None)
            else:
                sys.modules["yaml"] = orig_yaml
        joined = "\n".join(cm.output)
        self.assertIn("pip install pyyaml", joined)

    def test_builtin_parser_matches_pyyaml_on_real_template(self):
        """Parity guard: the built-in parser must produce exactly the same
        phone config dict as PyYAML for the shipped merged template. If this
        fails, the phone: section has outgrown the supported subset."""
        import yaml as pyyaml

        repo_root = Path(__file__).resolve().parent.parent
        template_text = (
            repo_root / "config_template.yaml"
        ).read_text(encoding="utf-8")
        expected = pyyaml.safe_load(template_text)["phone"]
        extracted = phone_daemon._extract_phone_section(template_text)
        actual = phone_daemon._parse_simple_yaml(extracted)
        self.assertEqual(actual, expected)

    def test_extract_phone_section_leaves_flat_configs_alone(self):
        flat = "target_directory: /tmp/staging\nmin_age_minutes: 5\n"
        self.assertEqual(phone_daemon._extract_phone_section(flat), flat)

    def test_extract_phone_section_takes_only_phone_part(self):
        merged = (
            "computer:\n"
            "  output_base: /tmp/organized\n"
            "  exclude_patterns:\n"
            "    - \"*.zzz\"\n"
            "phone:\n"
            "  target_directory: /tmp/staging\n"
            "  min_age_minutes: 5\n"
        )
        extracted = phone_daemon._extract_phone_section(merged)
        self.assertIn("target_directory: /tmp/staging", extracted)
        self.assertNotIn("computer:", extracted)
        self.assertNotIn("output_base", extracted)

    def test_missing_config_created_from_merged_template_phone_section(self):
        """With the merged config_template.yaml next to the daemon, a missing
        phone config must be created from the phone: section only."""
        merged = self.dir / "config_template.yaml"
        merged.write_text(
            "computer:\n"
            "  output_base: /tmp/organized\n"
            "phone:\n"
            "  target_directory: /storage/emulated/0/staging\n"
            "  min_age_minutes: 7\n"
        )
        cfg = phone_daemon.load_config(str(self.config))
        self.assertEqual(cfg["target_directory"], "/storage/emulated/0/staging")
        self.assertEqual(cfg["min_age_minutes"], 7)
        self.assertNotIn("computer", cfg)
        self.assertTrue(self.config.exists())
        # The written live config must be flat and parseable by the built-in
        # parser too (Termux has no PyYAML).
        written = self.config.read_text(encoding="utf-8")
        self.assertNotIn("computer:", written)
        self.assertIn("target_directory: /storage/emulated/0/staging", written)
        self.assertEqual(
            phone_daemon._parse_simple_yaml(written),
            {"target_directory": "/storage/emulated/0/staging",
             "min_age_minutes": 7},
        )

    def test_merged_template_works_without_pyyaml(self):
        """The textual phone-section extraction must work when PyYAML is
        unavailable (Termux)."""
        self.template.write_text(
            "computer:\n"
            "  output_base: /tmp/organized\n"
            "phone:\n"
            "  target_directory: /storage/emulated/0/staging\n"
            "  min_age_minutes: 5\n"
        )
        self.template.rename(self.dir / "config_template.yaml")
        self.config.write_text("")
        orig_yaml = sys.modules.get("yaml")
        sys.modules["yaml"] = None
        try:
            cfg = phone_daemon.load_config(str(self.config))
        finally:
            if orig_yaml is None:
                sys.modules.pop("yaml", None)
            else:
                sys.modules["yaml"] = orig_yaml
        self.assertEqual(cfg["target_directory"], "/storage/emulated/0/staging")
        self.assertEqual(cfg["min_age_minutes"], 5)

    def test_legacy_flat_phone_template_still_honoured(self):
        """Existing phone_daemon_config.template.yaml files keep working
        (copied verbatim when no config_template.yaml is present)."""
        self.template.write_text(TEMPLATE_YAML)
        cfg = phone_daemon.load_config(str(self.config))
        self.assertEqual(cfg["target_directory"], "/storage/emulated/0/staging")

    def test_json_config_still_parses_without_pyyaml(self):
        """The JSON fallback must keep working when PyYAML is unavailable."""
        self.config.write_text('{"target_directory": "/tmp/staging"}')
        orig_yaml = sys.modules.get("yaml")
        sys.modules["yaml"] = None
        try:
            cfg = phone_daemon.load_config(str(self.config))
        finally:
            if orig_yaml is None:
                sys.modules.pop("yaml", None)
            else:
                sys.modules["yaml"] = orig_yaml
        self.assertEqual(cfg["target_directory"], "/tmp/staging")

    def test_tab_indentation_exits_with_plain_message(self):
        """Item 9: tabs in YAML indentation must produce a clear 'use spaces'
        message instead of a cryptic parser error."""
        self.config.write_text(
            "target_directory: /tmp/staging\n"
            "source_directories:\n\t  - /tmp\n"
        )
        with self.assertRaises(SystemExit):
            phone_daemon.load_config(str(self.config))

    def test_first_run_report_runs_on_template_creation(self):
        """The will-it-work report must not raise when the config is freshly
        created from the merged template (it prints to stderr)."""
        import io
        from contextlib import redirect_stderr
        self.template.write_text(
            "phone:\n"
            "  target_directory: /tmp/staging\n"
            "  source_directories:\n"
            "    - /storage/emulated/0/DCIM\n"
        )
        self.template.rename(self.dir / "config_template.yaml")
        buf = io.StringIO()
        with redirect_stderr(buf):
            cfg = phone_daemon.load_config(str(self.config))
        self.assertEqual(cfg["target_directory"], "/tmp/staging")
        out = buf.getvalue()
        self.assertIn("Will it work?", out)
        self.assertIn("SPACES", out)


if __name__ == "__main__":
    unittest.main()

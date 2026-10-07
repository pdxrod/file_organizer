"""Tests for phone_sync.load_config config resolution and recovery."""

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from file_organizer import phone_sync  # noqa: E402

# Keep test output quiet — load_config logs warnings on recovery paths.
logging.getLogger("phone_sync").addHandler(logging.NullHandler())

TEMPLATE_YAML = (
    "target_directory: /storage/emulated/0/staging\n"
    "min_age_minutes: 5\n"
)


class LoadConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.config = self.dir / "phone_sync_config.yaml"
        self.template = self.dir / "phone_daemon_config.template.yaml"  # legacy name

    def test_missing_config_is_created_from_template(self):
        self.template.write_text(TEMPLATE_YAML)
        cfg = phone_sync.load_config(str(self.config))
        self.assertEqual(cfg["target_directory"], "/storage/emulated/0/staging")
        self.assertTrue(self.config.exists())
        self.assertNotEqual(self.config.stat().st_size, 0)

    def test_empty_config_recovers_from_template_instead_of_crashing(self):
        """Regression: an empty config used to die with
        'JSONDecodeError: Expecting value: line 1 column 1 (char 0)'."""
        self.template.write_text(TEMPLATE_YAML)
        self.config.write_text("")
        cfg = phone_sync.load_config(str(self.config))
        self.assertEqual(cfg["target_directory"], "/storage/emulated/0/staging")
        # The empty file must have been replaced with real content.
        self.assertGreater(self.config.stat().st_size, 0)

    def test_whitespace_only_config_recovers_from_template(self):
        self.template.write_text(TEMPLATE_YAML)
        self.config.write_text("\n  \n")
        cfg = phone_sync.load_config(str(self.config))
        self.assertEqual(cfg["min_age_minutes"], 5)

    def test_empty_config_without_template_falls_back_to_defaults(self):
        self.config.write_text("")
        original_find = phone_sync._find_template
        phone_sync._find_template = lambda p: None  # no template anywhere
        try:
            cfg = phone_sync.load_config(str(self.config))
        finally:
            phone_sync._find_template = original_find
        self.assertIn("target_directory", cfg)
        self.assertIn("source_directories", cfg)

    def test_valid_config_parses_unchanged(self):
        self.config.write_text(TEMPLATE_YAML)
        cfg = phone_sync.load_config(str(self.config))
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
            cfg = phone_sync.load_config(str(self.config))
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
            with self.assertLogs("phone_sync", level="ERROR") as cm:
                with self.assertRaises(SystemExit):
                    phone_sync.load_config(str(self.config))
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

        # Test file lives at src/test/ → project root is three levels up.
        repo_root = Path(__file__).resolve().parent.parent.parent
        template_text = (
            repo_root / "config_template.yaml"
        ).read_text(encoding="utf-8")
        expected = pyyaml.safe_load(template_text)["phone"]
        extracted = phone_sync._extract_phone_section(template_text)
        actual = phone_sync._parse_simple_yaml(extracted)
        self.assertEqual(actual, expected)

    def test_extract_phone_section_leaves_flat_configs_alone(self):
        flat = "target_directory: /tmp/staging\nmin_age_minutes: 5\n"
        self.assertEqual(phone_sync._extract_phone_section(flat), flat)

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
        extracted = phone_sync._extract_phone_section(merged)
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
        cfg = phone_sync.load_config(str(self.config))
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
            phone_sync._parse_simple_yaml(written),
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
            cfg = phone_sync.load_config(str(self.config))
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
        cfg = phone_sync.load_config(str(self.config))
        self.assertEqual(cfg["target_directory"], "/storage/emulated/0/staging")

    def test_json_config_still_parses_without_pyyaml(self):
        """The JSON fallback must keep working when PyYAML is unavailable."""
        self.config.write_text('{"target_directory": "/tmp/staging"}')
        orig_yaml = sys.modules.get("yaml")
        sys.modules["yaml"] = None
        try:
            cfg = phone_sync.load_config(str(self.config))
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
            phone_sync.load_config(str(self.config))

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
            cfg = phone_sync.load_config(str(self.config))
        self.assertEqual(cfg["target_directory"], "/tmp/staging")
        out = buf.getvalue()
        self.assertIn("Will it work?", out)
        self.assertIn("SPACES", out)


class OutputBaseItem15Tests(unittest.TestCase):
    """Item 15: the organized soft-link folder defaults to inside the
    file_organizer folder and is configurable via output_base."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()

    def _load(self, extra: str = ""):
        from file_organizer import config as cfgmod
        cfg_file = self.dir / "config.yaml"
        cfg_file.write_text(extra)
        return cfgmod.Config(str(cfg_file))

    def test_default_is_inside_file_organizer_folder(self):
        from file_organizer.config import find_project_root
        cfg = self._load()
        expected = str(find_project_root() / "organized")
        self.assertEqual(cfg.data.get("output_base"), expected)

    def test_literal_organized_means_the_default_folder(self):
        from file_organizer.config import find_project_root
        cfg = self._load("output_base: organized\n")
        self.assertEqual(
            cfg.data.get("output_base"), str(find_project_root() / "organized")
        )

    def test_old_home_default_migrates_to_project_folder(self):
        # Configs generated before item 15 say ~/organized; the new default
        # applies to them too.
        from file_organizer.config import find_project_root
        cfg = self._load("output_base: ~/organized\n")
        self.assertEqual(
            cfg.data.get("output_base"), str(find_project_root() / "organized")
        )

    def test_explicit_path_is_respected(self):
        cfg = self._load(f"output_base: {self.dir}/my-organized\n")
        self.assertEqual(
            cfg.data.get("output_base"), str(self.dir / "my-organized")
        )

    def test_template_documents_the_new_default(self):
        from file_organizer.config import find_project_root
        template = find_project_root() / "config_template.yaml"
        text = template.read_text(encoding="utf-8")
        self.assertIn('output_base: "organized"', text)

    def test_ensure_output_dir_creates_configured_folder(self):
        from file_organizer.config import ensure_output_dir
        cfg = self._load(f"output_base: {self.dir}/custom-organized\n")
        out = ensure_output_dir(cfg)
        self.assertEqual(out, self.dir / "custom-organized")
        self.assertTrue(out.is_dir())

    def test_ensure_output_dir_creates_default_folder(self):
        # The default resolves to <file_organizer folder>/organized; point
        # find_project_root at the temp dir so the test never touches the
        # real project folder.
        import file_organizer.config as cfgmod
        from file_organizer.config import ensure_output_dir
        from unittest import mock
        with mock.patch.object(
            cfgmod, "find_project_root", return_value=self.dir
        ):
            cfg = self._load()  # no output_base key → default resolution
            out = ensure_output_dir(cfg)
        self.assertEqual(out, self.dir / "organized")
        self.assertTrue(out.is_dir())


class StagingMapItem12bTests(unittest.TestCase):
    """Item 12b: phone staging subfolders map to computer folders.

    Rules: longest configured staging_map prefix → longest same-named
    folder under MAIN_DRIVE → misc fallback with the path preserved.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        self.main = self.dir / "main"
        (self.main / "Music").mkdir(parents=True)
        self.misc = str(self.main / "misc")
        self.drives = {"MAIN_DRIVE": str(self.main)}

    def _map(self, rel, staging_map=None):
        from file_organizer.config import map_staging_path
        return map_staging_path(rel, self.drives, staging_map, self.misc)

    def test_configured_override_wins(self):
        dest, rem = self._map("Music", {"Music": "MAIN_DRIVE/Entertainment"})
        self.assertEqual(dest, str(self.main / "Entertainment"))
        self.assertEqual(rem, "")

    def test_configured_override_applies_to_subfolders(self):
        dest, rem = self._map(
            "Music/sub", {"Music": "MAIN_DRIVE/Entertainment"}
        )
        self.assertEqual(dest, str(self.main / "Entertainment"))
        self.assertEqual(rem, "sub")

    def test_longest_prefix_wins(self):
        mapping = {
            "DCIM": "MAIN_DRIVE/Pictures",
            "DCIM/Camera": "MAIN_DRIVE/Pictures/Camera",
        }
        dest, rem = self._map("DCIM/Camera", mapping)
        self.assertEqual(dest, str(self.main / "Pictures" / "Camera"))
        self.assertEqual(rem, "")

    def test_same_name_folder_under_main_drive(self):
        dest, rem = self._map("Music")
        self.assertEqual(dest, str(self.main / "Music"))
        self.assertEqual(rem, "")

    def test_same_name_nested_subfolder(self):
        dest, rem = self._map("Music/sub")
        self.assertEqual(dest, str(self.main / "Music"))
        self.assertEqual(rem, "sub")

    def test_unknown_folder_goes_to_misc_with_path_preserved(self):
        dest, rem = self._map("DCIM/Camera")
        self.assertEqual(dest, self.misc)
        self.assertEqual(rem, "DCIM/Camera")

    def test_no_main_drive_falls_back_to_misc(self):
        from file_organizer.config import map_staging_path
        dest, rem = map_staging_path("Music", {}, {}, self.misc)
        self.assertEqual(dest, self.misc)
        self.assertEqual(rem, "Music")


if __name__ == "__main__":
    unittest.main()

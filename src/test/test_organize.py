"""Tests for the single `organize` entry point (item 12a).

organize lives at the project root and dispatches to the computer package
or the phone sync program depending on the platform. These tests cover the
pure parts: platform detection, subcommand mapping, and usage text.
"""

import importlib.util
from importlib.machinery import SourceFileLoader
import os
import sys
import unittest
from pathlib import Path

# The repo layout is: organize at the root, package under src/, tests under
# src/test/. Make both importable.
SRC = Path(__file__).resolve().parent.parent
ROOT = SRC.parent
sys.path.insert(0, str(SRC))

_organize_path = ROOT / "organize"
_loader = SourceFileLoader("organize", str(_organize_path))
_spec = importlib.util.spec_from_loader("organize", _loader)
organize = importlib.util.module_from_spec(_spec)
_loader.exec_module(organize)


class PlatformDetectionTests(unittest.TestCase):
    def _env_cleared(self):
        self._old = os.environ.get("ORGANIZE_PLATFORM")
        os.environ.pop("ORGANIZE_PLATFORM", None)
        self.addCleanup(
            lambda: os.environ.__setitem__("ORGANIZE_PLATFORM", self._old)
            if self._old is not None
            else os.environ.pop("ORGANIZE_PLATFORM", None)
        )

    def test_flag_overrides_win(self):
        self._env_cleared()
        self.assertEqual(organize.detect_platform(["--phone"]), "phone")
        self.assertEqual(organize.detect_platform(["--computer"]), "computer")
        self.assertEqual(
            organize.detect_platform(["--computer", "--phone"]), "computer"
        )

    def test_env_override(self):
        os.environ["ORGANIZE_PLATFORM"] = "phone"
        self.addCleanup(os.environ.pop, "ORGANIZE_PLATFORM", None)
        self.assertEqual(organize.detect_platform([]), "phone")

    def test_automatic_is_computer_on_this_host(self):
        self._env_cleared()
        # macOS test host has no /system/build.prop — must be computer.
        self.assertEqual(organize.detect_platform([]), "computer")

    def test_split_platform_args_removes_flags(self):
        platform, rest = organize.split_platform_args(
            ["--phone", "scan", "--dry-run"]
        )
        self.assertEqual(platform, "phone")
        self.assertEqual(rest, ["scan", "--dry-run"])
        self.assertIsNone(organize.split_platform_args(["test"])[0])


class SubcommandMappingTests(unittest.TestCase):
    def test_computer_test_is_dry_run(self):
        mapped, handled = organize.map_command("computer", ["test"])
        self.assertEqual(mapped, ["--scan-once"])
        self.assertIsNone(handled)

    def test_computer_test_real_is_production(self):
        mapped, _ = organize.map_command("computer", ["test-real"])
        self.assertEqual(mapped, ["--REAL", "--scan-once"])

    def test_computer_auto_is_production(self):
        mapped, handled = organize.map_command("computer", ["auto"])
        self.assertEqual(mapped, ["--REAL", "--scan-once"])
        self.assertIsNone(handled)

    def test_computer_lifecycle_commands_handled_here(self):
        for cmd in ("start", "stop", "restart", "status", "log", "clean",
                    "gui", "web", "pull", "push", "list", "setup"):
            mapped, handled = organize.map_command("computer", [cmd])
            self.assertEqual(handled, cmd, cmd)
            self.assertEqual(mapped, [cmd])

    def test_adb_subcommands_map_to_phone_pull_flags(self):
        # Regression: `list` and `push` used to be dropped, so every ADB
        # subcommand ran phone_pull.sh with no flags (plain pull mode).
        self.assertEqual(organize.adb_argv(["pull"]), [])
        self.assertEqual(organize.adb_argv(["push"]), ["--push"])
        self.assertEqual(organize.adb_argv(["list"]), ["--list"])

    def test_adb_subcommand_extra_flags_pass_through(self):
        self.assertEqual(
            organize.adb_argv(["push", "--dry-run"]),
            ["--push", "--dry-run"],
        )
        self.assertEqual(
            organize.adb_argv(["pull", "--device", "R5G123", "-v"]),
            ["--device", "R5G123", "-v"],
        )

    def test_phone_scan_is_production(self):
        mapped, handled = organize.map_command("phone", ["scan"])
        self.assertEqual(mapped, ["--scan-once"])
        self.assertIsNone(handled)

    def test_phone_auto_is_production(self):
        mapped, handled = organize.map_command("phone", ["auto"])
        self.assertEqual(mapped, ["--scan-once"])
        self.assertIsNone(handled)

    def test_phone_test_is_dry_run(self):
        mapped, _ = organize.map_command("phone", ["test"])
        self.assertEqual(mapped, ["--scan-once", "--dry-run"])

    def test_phone_lifecycle_commands_handled_here(self):
        # start/stop/status delegate to phone_sync's own flags; only
        # restart/log/setup are orchestrated by organize itself.
        for cmd in ("restart", "log", "setup"):
            mapped, handled = organize.map_command("phone", [cmd])
            self.assertEqual(handled, cmd, cmd)

    def test_phone_start_maps_to_background_flag(self):
        mapped, handled = organize.map_command("phone", ["start"])
        self.assertEqual(mapped, ["--background"])
        self.assertIsNone(handled)

    def test_find_cloud_drives_maps_and_no_find_proton(self):
        mapped, handled = organize.map_command("phone", ["find-cloud-drives"])
        self.assertEqual(mapped, ["--find-cloud-drives"])
        self.assertIsNone(handled)
        # The user's spec: no specific find-proton command.
        self.assertNotIn("find-proton", organize.PHONE_COMMANDS)
        self.assertNotIn("--find-proton", organize.PHONE_PASSTHROUGH)

    def test_phone_rename_maps_to_rename_held(self):
        mapped, handled = organize.map_command("phone", ["rename"])
        self.assertEqual(mapped, ["--rename-held"])
        self.assertIsNone(handled)
        self.assertIn("--rename-held", organize.PHONE_PASSTHROUGH)

    def test_unknown_flags_pass_through(self):
        mapped, handled = organize.map_command(
            "computer", ["--REAL", "--scan-once"]
        )
        self.assertEqual(mapped, ["--REAL", "--scan-once"])
        self.assertIsNone(handled)


class UsageTests(unittest.TestCase):
    def test_computer_usage_mentions_test_real(self):
        text = organize.usage("computer")
        self.assertIn("test-real", text)
        self.assertIn("./organize start", text)
        self.assertIn("computer", text)

    def test_phone_usage_mentions_scan(self):
        text = organize.usage("phone")
        self.assertIn("./organize scan", text)
        self.assertIn("phone", text)

    def test_usage_mentions_auto_on_both_platforms(self):
        for platform in ("computer", "phone"):
            self.assertIn("./organize auto", organize.usage(platform))

    def test_usage_leads_with_auto_before_test(self):
        """./organize auto is the one command most people will run — the
        usage banner must put it first, ahead of the dry-run test command."""
        for platform in ("computer", "phone"):
            text = organize.usage(platform)
            self.assertIn("START HERE", text)
            self.assertLess(
                text.index("./organize auto"),
                text.index("./organize test"),
            )

    def test_usage_mentions_config_files(self):
        for platform in ("computer", "phone"):
            self.assertIn("config_template.yaml", organize.usage(platform))

    def test_computer_usage_says_phone_config_is_created_on_the_phone(self):
        text = organize.usage("computer")
        self.assertIn("phone_sync_config.yaml", text)
        self.assertIn("section on the phone", text)
        # Regression: a computer run only ever creates config.yaml, so the
        # old "Both are created ... the first time you run" claim is gone.
        self.assertNotIn("Both are created", text)

    def test_phone_usage_says_computer_config_is_created_on_the_computer(self):
        text = organize.usage("phone")
        self.assertIn("phone_sync_config.yaml next to organize", text)
        self.assertIn("template on the computer", text)
        self.assertNotIn("Both are created", text)


class ProjectRootTests(unittest.TestCase):
    def test_find_project_root_locates_template(self):
        from file_organizer.config import find_project_root
        root = find_project_root()
        self.assertTrue((root / "config_template.yaml").exists())
        self.assertTrue((root / "organize").exists())

    def test_phone_template_search_walks_up_to_root(self):
        from file_organizer import phone_sync
        # A config path deep in a temp dir: the search must walk up from
        # phone_sync.py to the project root and find config_template.yaml.
        found = phone_sync._find_template(Path("/tmp/nonexistent/xyz"))
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "config_template.yaml")


if __name__ == "__main__":
    unittest.main()

"""Tests for the INSTALLED organize entry point (packaging/computer_entry.py).

The repo launcher (`organize`) cannot be bundled as-is — it computes paths
from its own location and respawns the daemon via `python -m file_organizer`.
The installers ship a faithful port instead; these tests pin the port: the
command tables, the bare-run/editor rule, the daemon spawn, the GUI/web
dispatch, and the frozen find_project_root behaviour.
"""

import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]  # src/test → src → repo root
sys.path.insert(0, str(ROOT / "packaging"))
sys.path.insert(0, str(ROOT / "src"))

import computer_entry  # noqa: E402


class CommandMapTests(unittest.TestCase):
    def test_auto_maps_to_real_scan_once(self):
        self.assertEqual(computer_entry.map_command(["auto"]),
                         (["--REAL", "--scan-once"], None))

    def test_tidy_real_maps(self):
        self.assertEqual(computer_entry.map_command(["tidy-real"]),
                         (["--REAL", "--tidy"], None))

    def test_case_and_dashes_are_ignored(self):
        self.assertEqual(computer_entry.map_command(["--AUTO"]),
                         (["--REAL", "--scan-once"], None))

    def test_lifecycle_commands_are_handled_here(self):
        for cmd in ("start", "stop", "restart", "status", "log", "clean",
                    "gui", "web", "pull", "push", "list", "setup", "config"):
            mapped, handled = computer_entry.map_command([cmd])
            self.assertEqual(handled, cmd)
            self.assertEqual(mapped[0], cmd)

    def test_test_is_handled_here(self):
        self.assertEqual(computer_entry.map_command(["test"]),
                         (["test"], "test"))

    def test_phone_only_commands_are_explained(self):
        mapped, handled = computer_entry.map_command(["rename"])
        self.assertEqual(handled, "phone-only")
        self.assertEqual(mapped[0], "rename")

    def test_unknown_tokens_pass_through(self):
        self.assertEqual(computer_entry.map_command(["--REAL"]),
                         (["--REAL"], None))


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="entry_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Point the entry at a scratch install folder.
        self.here_patch = mock.patch.object(computer_entry, "HERE", self.tmp)
        self.here_patch.start()
        self.addCleanup(self.here_patch.stop)

    def _write_config(self):
        cfg = self.tmp / "config.yaml"
        cfg.write_text("output_base: organized\n")
        return cfg

    def test_bare_run_without_config_runs_editor(self):
        with mock.patch.object(computer_entry, "config_edit",
                               return_value=7) as edit:
            err = io.StringIO()
            with redirect_stderr(err):
                rc = computer_entry.main([])
        self.assertEqual(rc, 7)
        edit.assert_called_once()
        self.assertIn("config editor", err.getvalue())

    def test_bare_run_with_valid_config_prints_usage(self):
        self._write_config()
        out = io.StringIO()
        with redirect_stdout(out):
            rc = computer_entry.main([])
        self.assertEqual(rc, 0)
        self.assertIn("organize", out.getvalue())

    def test_version_prints_version(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = computer_entry.main(["version"])
        self.assertEqual(rc, 0)
        from file_organizer import __version__
        self.assertEqual(out.getvalue().strip(), f"organize {__version__}")

    def test_help_prints_usage(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = computer_entry.main(["help"])
        self.assertEqual(rc, 0)
        self.assertIn("organize", out.getvalue())

    def test_start_spawns_this_binary_as_daemon(self):
        self._write_config()
        with mock.patch.object(
            computer_entry, "pid_log",
            return_value=(str(self.tmp / "p.pid"),
                          str(self.tmp / "p.log")),
        ), mock.patch.object(
            computer_entry, "time"
        ) as _t, mock.patch(
            "file_organizer.single_instance.read_holder",
            return_value=None,
        ), mock.patch.object(computer_entry.subprocess, "Popen") as popen:
            proc = mock.MagicMock()
            proc.poll.return_value = None
            popen.return_value = proc
            rc = computer_entry.main(["start"])
        self.assertEqual(rc, 0)
        argv = popen.call_args[0][0]
        self.assertEqual(argv[0], sys.executable)  # the binary itself
        self.assertEqual(argv[1], "--REAL")
        self.assertIn("--config", argv)
        self.assertEqual(popen.call_args[1].get("cwd"), str(self.tmp))

    def test_cli_passthrough_adds_config(self):
        # run_cli itself injects --config; verify the bridge directly.
        with mock.patch("file_organizer.cli.main") as cli_main:
            computer_entry.run_cli(["--REAL", "--scan-once"])
        args = cli_main.call_args[0][0]
        self.assertEqual(args[-2:],
                         ["--config", str(computer_entry.config_path())])
        self.assertEqual(args[:2], ["--REAL", "--scan-once"])

    def test_main_passes_through_to_run_cli(self):
        with mock.patch.object(computer_entry, "run_cli") as run:
            rc = computer_entry.main(["--REAL", "--scan-once"])
        self.assertEqual(rc, 0)
        self.assertEqual(run.call_args[0][0], ["--REAL", "--scan-once"])

    def test_gui_and_web_dispatch(self):
        with mock.patch.object(computer_entry, "computer_gui",
                               return_value=5):
            self.assertEqual(computer_entry.main(["gui"]), 5)
        with mock.patch.object(computer_entry, "computer_web",
                               return_value=6):
            self.assertEqual(computer_entry.main(["web"]), 6)

    def test_phone_only_command_explains(self):
        err = io.StringIO()
        with redirect_stderr(err):
            rc = computer_entry.main(["rename"])
        self.assertEqual(rc, 1)
        self.assertIn("phone", err.getvalue().lower())


class FrozenProjectRootTests(unittest.TestCase):
    """find_project_root's frozen branch: in an installed build everything
    (config.yaml, organized/, the log) lives next to the binary."""

    def test_frozen_root_is_the_executable_folder(self):
        from file_organizer.config import find_project_root
        exe_dir = Path(tempfile.mkdtemp(prefix="frozen_root_"))
        self.addCleanup(shutil.rmtree, exe_dir, ignore_errors=True)
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "executable", str(exe_dir / "organize")):
            self.assertEqual(find_project_root(), exe_dir.resolve())

    def test_dev_root_unchanged_when_not_frozen(self):
        from file_organizer.config import find_project_root
        self.assertEqual(find_project_root(), ROOT)


if __name__ == "__main__":
    unittest.main()

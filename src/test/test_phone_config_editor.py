"""Tests for `organize config` on the phone (phone_config_editor.py).

Same I/O-injection style as test_config_editor.py: scripted answers, no
terminal, everything in temp dirs. The editor must produce files that
the phone daemon's built-in flat-YAML parser reads back (PyYAML is
usually missing on Termux).
"""

import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

from file_organizer import phone_config_editor as pe  # noqa: E402
from file_organizer.phone_sync import _parse_simple_yaml  # noqa: E402


# A small template with the shape the editor expects (a phone: section).
TEMPLATE = """\
phone:
  source_directories:
    - "/storage/emulated/0/DCIM"
    - "/storage/emulated/0/Pictures"
  target_directory: "/storage/emulated/0/file_organizer_staging"
  sync_cloud_drives: true
  cloud_sync_subfolder: "file_organizer"
  cloud_drive_paths:
    - "/storage/emulated/0/ProtonDrive"
  min_free_space_mb: 200
  min_age_minutes: 10
  collapse_rename_families: false
  hold_app_generated_names: true
  normalise_leading_dates: true
  scan_interval_seconds: 300
  idle_backoff_factor: 2
  idle_backoff_max_multiplier: 8
  max_file_size_mb: 500
  include_extensions: []
  exclude_extensions:
    - ".tmp"
  exclude_patterns:
    - ".thumbnails"
  exclude_folder_names:
    - ".git"
  exclude_folder_patterns:
    - "whatsapp"
  delete_after_copy: false
  db_path: ""
  prune_max_age_days: 30
  log_path: ""
  pid_path: ""
"""


class ScriptedInput:
    """input_fn that answers from a list; extra questions fail loudly."""

    def __init__(self, answers):
        self._answers = list(answers)

    def __call__(self, prompt):
        assert self._answers, f"editor asked more questions than scripted: {prompt!r}"
        return self._answers.pop(0)


class PhoneConfigEditorTests(unittest.TestCase):
    def _tmp(self, template=TEMPLATE, config=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        tp = d / "config_template.yaml"
        tp.write_text(template, encoding="utf-8")
        cp = d / "phone_sync_config.yaml"
        if config is not None:
            cp.write_text(config, encoding="utf-8")
        return cp, tp

    def _run(self, answers, template=TEMPLATE, config=None):
        cp, tp = self._tmp(template, config)
        out = []
        code = pe.run_phone_editor(
            str(cp), template_path=str(tp),
            input_fn=ScriptedInput(answers), output_fn=out.append,
        )
        return code, cp, "\n".join(out)

    # ── full walk ──────────────────────────────────────────────────────

    def test_fresh_walk_creates_and_edits(self):
        code, cp, out = self._run([
            "y",                              # start fresh from template
            "",                               # source folders: done
            "/storage/emulated/0/my_staging", # staging folder
            "n",                              # cloud sync: turn OFF
            "",                               # cloud drive paths: done
            "",                               # free-space guard
            "",                               # grace period
            "",                               # collapse families: keep
            "",                               # hold app-generated: keep
            "",                               # normalise dates: keep
            "",                               # scan interval
            "",                               # idle factor
            "",                               # idle max
            "",                               # max file size
            "",                               # include extensions
            "",                               # exclude extensions
            "",                               # excluded names
            "",                               # excluded folder names
            "",                               # excluded folder patterns
            "",                               # delete after copy: keep OFF
            "",                               # prune days
            "y",                              # save
        ])
        self.assertEqual(code, 0, out)
        parsed = _parse_simple_yaml(cp.read_text(encoding="utf-8"))
        self.assertEqual(parsed["target_directory"],
                         "/storage/emulated/0/my_staging")
        self.assertIs(parsed["sync_cloud_drives"], False)
        self.assertEqual(parsed["min_age_minutes"], 10)
        self.assertEqual(parsed["source_directories"],
                         ["/storage/emulated/0/DCIM",
                          "/storage/emulated/0/Pictures"])
        self.assertIn("Saved", out)

    # ── x / s conventions ─────────────────────────────────────────────

    def test_x_aborts_without_writing(self):
        original = "target_directory: /old/staging\n"
        code, cp, out = self._run(["x"], config=original)
        self.assertEqual(code, 1)
        self.assertEqual(cp.read_text(encoding="utf-8"), original)
        self.assertIn("Cancelled", out)

    def test_s_saves_immediately(self):
        original = ("target_directory: /old/staging\n"
                    "scan_interval_seconds: 60\n")
        code, cp, out = self._run(["s"], config=original)
        self.assertEqual(code, 0, out)
        parsed = _parse_simple_yaml(cp.read_text(encoding="utf-8"))
        self.assertEqual(parsed["target_directory"], "/old/staging")
        self.assertEqual(parsed["scan_interval_seconds"], 60)
        self.assertIn("Saved", out)

    def test_invalid_config_declining_fresh_start(self):
        original = "target_directory\n\t/storage/x\n"  # TAB indentation
        code, cp, out = self._run(["n"], config=original)
        self.assertEqual(code, 1)
        self.assertEqual(cp.read_text(encoding="utf-8"), original)
        self.assertIn("Nothing changed", out)

    def test_missing_template_fails_cleanly(self):
        cp, tp = self._tmp()
        tp.unlink()
        out = []
        code = pe.run_phone_editor(
            str(cp), template_path=str(tp),
            input_fn=ScriptedInput([]), output_fn=out.append,
        )
        self.assertEqual(code, 1)
        self.assertIn("template not found", "\n".join(out))

    # ── flat dump round-trip ───────────────────────────────────────────

    def test_dump_flat_round_trips_tricky_values(self):
        cfg = {
            "a_path": "/storage/emulated/0/my folder",
            "a_number": 300,
            "a_string_that_looks_numeric": "2025",
            "a_bool": True,
            "a_false": False,
            "a_none": None,
            "a_true_word": "true",
            "a_list": [".jpg", "x # y", 'quoted"value'],
            "empty_list": [],
            "a_float": 2.5,
        }
        parsed = _parse_simple_yaml(pe._dump_flat(cfg))
        self.assertEqual(parsed, cfg)

    def test_write_never_installs_a_broken_file(self):
        cp, tp = self._tmp()
        original = "target_directory: /keep/me\n"
        cp.write_text(original, encoding="utf-8")
        err = pe._write_phone_config(str(cp), {"weird": {"a": 1}})
        self.assertIsNotNone(err)
        self.assertEqual(cp.read_text(encoding="utf-8"), original)

    def test_real_repo_template_round_trips(self):
        """The actual config_template.yaml's phone: section must survive
        create → load → dump → parse with the values intact."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        real = repo_root / "config_template.yaml"
        self.assertTrue(real.exists(), "config_template.yaml not found")
        cp, _tp = self._tmp()
        self.assertIsNone(pe._create_from_template(str(real), str(cp)))
        cfg = pe._load_phone_config(str(cp))
        self.assertEqual(cfg["target_directory"],
                         "/storage/emulated/0/file_organizer_staging")
        self.assertIs(cfg["sync_cloud_drives"], True)
        self.assertIsNone(pe._write_phone_config(str(cp), cfg))
        parsed = _parse_simple_yaml(cp.read_text(encoding="utf-8"))
        self.assertEqual(parsed, cfg)

    # ── health checks and warnings ─────────────────────────────────────

    def test_phone_config_problem(self):
        cp, _tp = self._tmp()
        self.assertIn("no phone_sync_config.yaml", pe.phone_config_problem(str(cp)))
        cp.write_text("target_directory: /x\n", encoding="utf-8")
        self.assertIsNone(pe.phone_config_problem(str(cp)))
        cp.write_text("target_directory\n\t/x\n", encoding="utf-8")
        self.assertIn("TAB", pe.phone_config_problem(str(cp)))
        cp.write_text("", encoding="utf-8")
        self.assertIn("empty", pe.phone_config_problem(str(cp)))

    def test_staging_inside_source_warns(self):
        warn = pe._staging_inside_source(
            ["/storage/emulated/0/DCIM"],
            "/storage/emulated/0/DCIM/staging")
        self.assertIn("inside a source folder", warn)
        self.assertIsNone(pe._staging_inside_source(
            ["/storage/emulated/0/DCIM"], "/storage/emulated/0/Pictures"))
        warn = pe._staging_inside_source(
            ["/storage/emulated/0/staging/sub"],
            "/storage/emulated/0/staging")
        self.assertIn("inside the staging folder", warn)


if __name__ == "__main__":
    unittest.main()

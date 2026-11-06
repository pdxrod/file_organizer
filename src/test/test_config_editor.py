"""Tests for the `organize config` interactive editor (config_editor.py).

The editor's I/O is injectable (input_fn/output_fn), so every test drives
it with a scripted list of answers — no terminal, no real stdin. All
config files and templates live in temp dirs; the real repo config.yaml
is never touched.
"""

import sys
import tempfile
import unittest
import yaml
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

from file_organizer import config_editor as ce  # noqa: E402
from file_organizer.config import ConfigError, starter_data  # noqa: E402


# A small template with the shape the editor expects (computer: section).
TEMPLATE_YAML = """\
computer:
  drives:
    MAIN_DRIVE: "/Users/janedoe"
    EXTERNAL_DRIVE: "/Volumes/YourExternalDrive"
  sync_pairs: []
  one_way_pairs: []
  source_folders:
    - "MAIN_DRIVE/Documents"
  output_base: "organized"
  enable_content_analysis: true
  enable_folder_sync: true
  enable_duplicate_detection: false
  enable_semantic_categories: true
  auto_git: false
  auto_git_folders: []
  collapse_rename_families: false
  dedupe_download_copies: true
  ml_content_analysis:
    enabled: true
  exclude_patterns:
    - ".DS_Store"
  router:
    enabled: false
    action: move
    route_roots: []
    watch_folders: []
    target_folders: {}
    deepseek:
      enabled: false
      api_key_path: ""
  phone_pull:
    auto_pull: true
    local_stage: "MAIN_DRIVE/misc"
    remote_stage: "PROTON_DRIVE/misc"
    adb_path: "adb"
phone:
  target_directory: /storage/emulated/0/staging
"""


class Scripted:
    """input_fn that returns the next scripted answer, or EOF when empty."""

    def __init__(self, answers):
        self.answers = list(answers)

    def __call__(self, prompt):
        if not self.answers:
            raise EOFError(f"script ran out of answers at prompt: {prompt!r}")
        return self.answers.pop(0)


class Capture:
    """output_fn that records every line."""

    def __init__(self):
        self.lines = []

    def __call__(self, message):
        self.lines.append(str(message))

    def text(self):
        return "\n".join(self.lines)


class TempConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.config = self.dir / "config.yaml"
        self.template = self.dir / "config_template.yaml"
        self.template.write_text(TEMPLATE_YAML)

    def write(self, text):
        self.config.write_text(text)
        return str(self.config)


# ── config_problem / needs_editor ──────────────────────────────────────────

class ConfigProblemTests(TempConfigTests):
    def test_missing_config_is_a_problem(self):
        problem = ce.config_problem(str(self.config))
        self.assertIn("no config.yaml yet", problem)
        self.assertTrue(ce.needs_editor(str(self.config)))

    def test_tab_indentation_is_a_problem(self):
        self.write("drives:\n\tMAIN_DRIVE: /tmp\n")
        problem = ce.config_problem(str(self.config))
        self.assertIn("TAB", problem)
        self.assertTrue(ce.needs_editor(str(self.config)))

    def test_yaml_syntax_error_is_a_problem(self):
        self.write("drives: [unclosed\n")
        problem = ce.config_problem(str(self.config))
        self.assertIn("not valid YAML", problem)
        self.assertTrue(ce.needs_editor(str(self.config)))

    def test_empty_config_is_a_problem(self):
        self.write("")
        problem = ce.config_problem(str(self.config))
        self.assertIn("empty", problem)

    def test_non_mapping_config_is_a_problem(self):
        self.write("- just\n- a list\n")
        problem = ce.config_problem(str(self.config))
        self.assertIn("top-level mapping", problem)

    def test_circular_drive_reference_is_a_problem(self):
        self.write("drives:\n  A: A/sub\n")
        problem = ce.config_problem(str(self.config))
        self.assertIn("Circular drive reference", problem)

    def test_valid_config_has_no_problem(self):
        self.write("output_base: organized\n")
        self.assertIsNone(ce.config_problem(str(self.config)))
        self.assertFalse(ce.needs_editor(str(self.config)))


# ── starter_data (shared with create_starter) ──────────────────────────────

class StarterDataTests(TempConfigTests):
    def test_dangerous_features_are_off(self):
        data = starter_data(str(self.template))
        self.assertFalse(data["enable_folder_sync"])
        self.assertFalse(data["enable_content_analysis"])
        self.assertFalse(data["enable_duplicate_detection"])
        self.assertFalse(data["enable_semantic_categories"])
        self.assertFalse(data["auto_git"])
        self.assertFalse(data["router"]["enabled"])
        self.assertFalse(data["ml_content_analysis"]["enabled"])

    def test_safe_values_survive(self):
        data = starter_data(str(self.template))
        self.assertEqual(data["drives"]["MAIN_DRIVE"], "/Users/janedoe")
        self.assertEqual(data["source_folders"], ["MAIN_DRIVE/Documents"])

    def test_missing_template_raises(self):
        with self.assertRaises(ConfigError):
            starter_data(str(self.dir / "nope.yaml"))


# ── prompt helpers ─────────────────────────────────────────────────────────

class PromptHelperTests(unittest.TestCase):
    def test_ask_blank_returns_default(self):
        self.assertEqual(ce.ask("q?", Scripted([""]), default="d"), "d")
        self.assertIsNone(ce.ask("q?", Scripted([""]), default=None))

    def test_ask_eof_aborts(self):
        with self.assertRaises(ce.EditorAbort):
            ce.ask("q?", Scripted([]))

    def test_ask_x_aborts_case_insensitively(self):
        for answer in ("x", "X", " x "):
            with self.assertRaises(ce.EditorAbort, msg=answer):
                ce.ask("q?", Scripted([answer]))

    def test_confirm_x_aborts(self):
        with self.assertRaises(ce.EditorAbort):
            ce.confirm("q?", Scripted(["x"]))
        with self.assertRaises(ce.EditorAbort):
            ce.confirm("q?", Scripted(["X"]))

    def test_ask_s_save_quits_case_insensitively(self):
        for answer in ("s", "S", " s "):
            with self.assertRaises(ce.EditorSaveQuit, msg=answer):
                ce.ask("q?", Scripted([answer]))

    def test_confirm_s_save_quits(self):
        with self.assertRaises(ce.EditorSaveQuit):
            ce.confirm("q?", Scripted(["s"]))
        with self.assertRaises(ce.EditorSaveQuit):
            ce.confirm("q?", Scripted(["S"]))

    def test_ask_validate_reprompts(self):
        answers = Scripted(["bad", "good"])
        value = ce.ask("q?", answers, validate=lambda v: "nope" if v == "bad" else None)
        self.assertEqual(value, "good")

    def test_confirm_variants(self):
        self.assertTrue(ce.confirm("q?", Scripted([""]), default=True))
        self.assertFalse(ce.confirm("q?", Scripted([""]), default=False))
        self.assertTrue(ce.confirm("q?", Scripted(["y"])))
        self.assertFalse(ce.confirm("q?", Scripted(["n"])))
        # Invalid answer reprompts, then the blank keeps the default.
        self.assertTrue(ce.confirm("q?", Scripted(["maybe", ""]), default=True))


# ── path_status ────────────────────────────────────────────────────────────

class PathStatusTests(TempConfigTests):
    def test_existing_path(self):
        self.assertIn("exists", ce.path_status(str(self.dir)))

    def test_placeholder_marker(self):
        self.assertIn("starter example", ce.path_status("/Users/janedoe/docs"))

    def test_missing_path(self):
        self.assertIn("not available", ce.path_status(str(self.dir / "nope")))


# ── section editors ────────────────────────────────────────────────────────

class EditDrivesTests(unittest.TestCase):
    def test_enter_keeps_everything(self):
        drives = {"MAIN_DRIVE": "/tmp"}
        result = ce.edit_drives(drives, Scripted([""]), Capture())
        self.assertEqual(result, drives)

    def test_edit_changes_value(self):
        drives = {"MAIN_DRIVE": "/tmp"}
        out = Capture()
        # e1 → name prompt (blank keeps) → folder prompt (new value)
        result = ce.edit_drives(drives, Scripted(["e1", "", "/tmp/other", ""]),
                                out)
        self.assertEqual(result["MAIN_DRIVE"], "/tmp/other")
        self.assertIn("exists", out.text())

    def test_edit_renames_drive(self):
        drives = {"MAIN_DRIVE": "/tmp"}
        result = ce.edit_drives(drives, Scripted(["e1", "gdrive", "", ""]),
                                Capture())
        self.assertNotIn("MAIN_DRIVE", result)
        self.assertEqual(result["GDRIVE"], "/tmp")

    def test_edit_renames_and_changes_value(self):
        drives = {"MAIN_DRIVE": "/tmp"}
        result = ce.edit_drives(drives, Scripted(["e1", "gdrive", "/tmp/x", ""]),
                                Capture())
        self.assertEqual(result["GDRIVE"], "/tmp/x")

    def test_edit_rename_to_existing_name_keeps_old_name(self):
        drives = {"MAIN_DRIVE": "/tmp", "BACKUP_DRIVE": "/tmp2"}
        result = ce.edit_drives(drives, Scripted(["e1", "backup_drive", "", ""]),
                                Capture())
        self.assertEqual(result["MAIN_DRIVE"], "/tmp")
        self.assertEqual(result["BACKUP_DRIVE"], "/tmp2")

    def test_bare_e_edits_only_drive(self):
        drives = {"MAIN_DRIVE": "/tmp"}
        result = ce.edit_drives(drives, Scripted(["e", "", "/tmp/y", ""]),
                                Capture())
        self.assertEqual(result["MAIN_DRIVE"], "/tmp/y")

    def test_bare_e_asks_which_number(self):
        drives = {"MAIN_DRIVE": "/tmp", "EXTERNAL_DRIVE": "/Volumes/x"}
        result = ce.edit_drives(drives, Scripted(["e", "2", "", "/tmp/z", ""]),
                                Capture())
        self.assertEqual(result["MAIN_DRIVE"], "/tmp")
        self.assertEqual(result["EXTERNAL_DRIVE"], "/tmp/z")

    def test_bare_r_removes_only_drive(self):
        drives = {"MAIN_DRIVE": "/tmp"}
        result = ce.edit_drives(drives, Scripted(["r", ""]), Capture())
        self.assertEqual(result, {})

    def test_remove_drops_drive(self):
        drives = {"MAIN_DRIVE": "/tmp", "EXTERNAL_DRIVE": "/Volumes/x"}
        result = ce.edit_drives(drives, Scripted(["r2", ""]), Capture())
        self.assertNotIn("EXTERNAL_DRIVE", result)
        self.assertEqual(result["MAIN_DRIVE"], "/tmp")

    def test_add_drive(self):
        drives = {}
        result = ce.edit_drives(drives, Scripted(["a", "backup_drive", "/tmp", ""]),
                                Capture())
        self.assertEqual(result["BACKUP_DRIVE"], "/tmp")

    def test_eof_aborts(self):
        with self.assertRaises(ce.EditorAbort):
            ce.edit_drives({"MAIN_DRIVE": "/tmp"}, Scripted([]), Capture())

    def test_x_aborts(self):
        with self.assertRaises(ce.EditorAbort):
            ce.edit_drives({"MAIN_DRIVE": "/tmp"}, Scripted(["X"]), Capture())


class EditPathListTests(unittest.TestCase):
    def test_add_then_done(self):
        items = ["/tmp/a"]
        result = ce.edit_path_list("t", items, "explain",
                                   Scripted(["a", "/tmp/b", ""]), Capture())
        self.assertEqual(result, ["/tmp/a", "/tmp/b"])

    def test_remove_then_done(self):
        items = ["/tmp/a", "/tmp/b"]
        result = ce.edit_path_list("t", items, "explain",
                                   Scripted(["r1", ""]), Capture())
        self.assertEqual(result, ["/tmp/b"])

    def test_edit_item(self):
        items = ["/tmp/a"]
        result = ce.edit_path_list("t", items, "explain",
                                   Scripted(["e1", "/tmp/z", ""]), Capture())
        self.assertEqual(result, ["/tmp/z"])

    def test_enter_done_keeps_list(self):
        items = ["/tmp/a"]
        result = ce.edit_path_list("t", items, "explain",
                                   Scripted([""]), Capture())
        self.assertEqual(result, items)

    def test_x_aborts(self):
        with self.assertRaises(ce.EditorAbort):
            ce.edit_path_list("t", ["/tmp/a"], "explain",
                              Scripted(["x"]), Capture())

    def test_bare_e_edits_only_entry(self):
        items = ["/tmp/a"]
        result = ce.edit_path_list("t", items, "explain",
                                   Scripted(["e", "/tmp/z", ""]), Capture())
        self.assertEqual(result, ["/tmp/z"])

    def test_bare_e_asks_which_number(self):
        items = ["/tmp/a", "/tmp/b"]
        result = ce.edit_path_list("t", items, "explain",
                                   Scripted(["e", "2", "/tmp/z", ""]), Capture())
        self.assertEqual(result, ["/tmp/a", "/tmp/z"])

    def test_bare_r_removes_only_entry(self):
        items = ["/tmp/a"]
        result = ce.edit_path_list("t", items, "explain",
                                   Scripted(["r", ""]), Capture())
        self.assertEqual(result, [])

    def test_s_save_quits(self):
        with self.assertRaises(ce.EditorSaveQuit):
            ce.edit_path_list("t", ["/tmp/a"], "explain",
                              Scripted(["s"]), Capture())


class EditPairsTests(unittest.TestCase):
    def test_enter_keeps_empty(self):
        self.assertEqual(ce.edit_pairs("t", [], False, Scripted([""]), Capture()), [])

    def test_add_pair(self):
        result = ce.edit_pairs("t", [], False,
                               Scripted(["a", "A", "B", ""]), Capture())
        self.assertEqual(result, [{"folders": ["A", "B"]}])

    def test_remove_pair(self):
        pairs = [{"folders": ["A", "B"]}, {"folders": ["C", "D"]}]
        result = ce.edit_pairs("t", pairs, True, Scripted(["r1", ""]), Capture())
        self.assertEqual(result, [{"folders": ["C", "D"]}])

    def test_edit_pair_blank_keeps(self):
        pairs = [{"folders": ["A", "B"]}]
        result = ce.edit_pairs("t", pairs, False, Scripted(["e1", "", "", ""]),
                               Capture())
        self.assertEqual(result, [{"folders": ["A", "B"]}])

    def test_edit_pair_changes(self):
        pairs = [{"folders": ["A", "B"]}]
        result = ce.edit_pairs("t", pairs, True, Scripted(["e1", "C", "D", ""]),
                               Capture())
        self.assertEqual(result, [{"folders": ["C", "D"]}])

    def test_x_aborts(self):
        with self.assertRaises(ce.EditorAbort):
            ce.edit_pairs("t", [], False, Scripted(["x"]), Capture())

    def test_bare_e_asks_which_number(self):
        pairs = [{"folders": ["A", "B"]}, {"folders": ["C", "D"]}]
        result = ce.edit_pairs("t", pairs, True,
                               Scripted(["e", "2", "E", "F", ""]), Capture())
        self.assertEqual(result[1], {"folders": ["E", "F"]})

    def test_bare_r_removes_only_pair(self):
        pairs = [{"folders": ["A", "B"]}]
        result = ce.edit_pairs("t", pairs, False, Scripted(["r", ""]), Capture())
        self.assertEqual(result, [])

    def test_s_save_quits(self):
        with self.assertRaises(ce.EditorSaveQuit):
            ce.edit_pairs("t", [], False, Scripted(["s"]), Capture())


class ToggleTests(unittest.TestCase):
    def test_keep_leaves_value(self):
        cfg = {"enable_folder_sync": False}
        self.assertFalse(ce._toggle(cfg, "enable_folder_sync", "t", "explain",
                                    Scripted(["y"]), Capture()))
        self.assertFalse(cfg["enable_folder_sync"])

    def test_flip_changes_value(self):
        cfg = {"enable_folder_sync": False}
        self.assertTrue(ce._toggle(cfg, "enable_folder_sync", "t", "explain",
                                   Scripted(["n"]), Capture()))
        self.assertTrue(cfg["enable_folder_sync"])

    def test_missing_key_defaults_off(self):
        cfg = {}
        ce._toggle(cfg, "auto_git", "t", "explain", Scripted(["y"]), Capture())
        self.assertNotIn("auto_git", cfg)


# ── write_config ───────────────────────────────────────────────────────────

class WriteConfigTests(TempConfigTests):
    def test_writes_valid_spaces_only_file(self):
        cfg = {"drives": {"MAIN_DRIVE": "/tmp"}, "output_base": "organized"}
        err = ce.write_config(str(self.config), cfg)
        self.assertIsNone(err)
        text = self.config.read_text()
        self.assertNotIn("\t", text)
        self.assertEqual(yaml.safe_load(text)["drives"], {"MAIN_DRIVE": "/tmp"})

    def test_rejects_invalid_settings_and_keeps_old_file(self):
        self.write("output_base: organized\n")
        original = self.config.read_text()
        err = ce.write_config(str(self.config), {"output_base": "/"})
        self.assertIn("don't validate", err)
        self.assertEqual(self.config.read_text(), original)

    def test_replaces_existing_file(self):
        self.write("output_base: organized\n")
        err = ce.write_config(str(self.config), {"output_base": "/tmp"})
        self.assertIsNone(err)
        self.assertEqual(yaml.safe_load(self.config.read_text())["output_base"],
                         "/tmp")


# ── run_editor end to end ──────────────────────────────────────────────────

# One full walk with everything kept: fresh start, skip every section,
# keep every toggle as-is (phone pull stays ON → 3 path prompts), save.
FULL_WALK_KEEP_ALL = [
    "y",                    # start fresh
    "",                     # drives: done
    "",                     # source folders: done
    "",                     # sync pairs: done
    "",                     # one-way pairs: done
    "",                     # output_base: keep
    "y", "y", "y", "y", "y", "y", "y",  # 7 feature toggles: keep
    "y",                    # router: keep OFF
    "y",                    # phone pull: keep ON
    "", "", "",             # local_stage / remote_stage / adb_path: keep
    "",                     # exclusions: done
    "y",                    # save
]


class RunEditorTests(TempConfigTests):
    def test_missing_config_walk_creates_config(self):
        out = Capture()
        answers = list(FULL_WALK_KEEP_ALL)
        answers[6] = "n"  # flip folder sync ON during the walk
        rc = ce.run_editor(str(self.config), template_path=str(self.template),
                           input_fn=Scripted(answers), output_fn=out)
        self.assertEqual(rc, 0)
        self.assertTrue(self.config.exists())
        text = self.config.read_text()
        self.assertNotIn("\t", text)
        saved = yaml.safe_load(text)
        self.assertEqual(saved["drives"]["MAIN_DRIVE"], "/Users/janedoe")
        self.assertTrue(saved["enable_folder_sync"])       # flipped ON in walk
        self.assertFalse(saved["router"]["enabled"])       # starter turns it off
        self.assertIn("Saved", out.text())

    def test_invalid_config_is_rebuilt(self):
        self.write("drives:\n\tMAIN_DRIVE: /tmp\n")  # tabs → invalid
        out = Capture()
        rc = ce.run_editor(str(self.config), template_path=str(self.template),
                           input_fn=Scripted(FULL_WALK_KEEP_ALL), output_fn=out)
        self.assertEqual(rc, 0)
        self.assertNotIn("\t", self.config.read_text())
        self.assertIn("TAB", out.text())          # the problem was explained
        self.assertIn("not allowed", out.text())

    def test_cancel_writes_nothing(self):
        rc = ce.run_editor(str(self.config), template_path=str(self.template),
                           input_fn=Scripted(["y"]), output_fn=Capture())
        self.assertEqual(rc, 1)
        self.assertFalse(self.config.exists())

    def test_x_at_drives_menu_quits_without_saving(self):
        out = Capture()
        rc = ce.run_editor(str(self.config), template_path=str(self.template),
                           input_fn=Scripted(["y", "x"]), output_fn=out)
        self.assertEqual(rc, 1)
        self.assertFalse(self.config.exists())
        self.assertIn("Cancelled", out.text())

    def test_s_at_drives_menu_saves_changes_so_far_and_exits(self):
        out = Capture()
        # fresh start, edit MAIN_DRIVE (e1: keep name, new folder), then s
        rc = ce.run_editor(str(self.config), template_path=str(self.template),
                           input_fn=Scripted(["y", "e1", "", "/tmp", "s"]),
                           output_fn=out)
        self.assertEqual(rc, 0)
        self.assertTrue(self.config.exists())
        saved = yaml.safe_load(self.config.read_text())
        self.assertEqual(saved["drives"]["MAIN_DRIVE"], "/tmp")
        self.assertFalse(saved["enable_folder_sync"])  # starter default kept
        self.assertIn("Saved", out.text())

    def test_s_before_anything_loaded_saves_safe_starter(self):
        out = Capture()
        rc = ce.run_editor(str(self.config), template_path=str(self.template),
                           input_fn=Scripted(["s"]), output_fn=out)
        self.assertEqual(rc, 0)
        saved = yaml.safe_load(self.config.read_text())
        self.assertFalse(saved["router"]["enabled"])
        self.assertIn("starter", out.text())

    def test_s_mid_section_saves_in_progress_edits(self):
        out = Capture()
        rc = ce.run_editor(str(self.config), template_path=str(self.template),
                           input_fn=Scripted(
                               ["y", "", "a", "/tmp/new-src", "s"]),
                           output_fn=out)
        self.assertEqual(rc, 0)
        saved = yaml.safe_load(self.config.read_text())
        self.assertIn("/tmp/new-src", saved["source_folders"])
        self.assertIn("Saved", out.text())

    def test_x_at_toggle_quits_without_saving(self):
        out = Capture()
        rc = ce.run_editor(str(self.config), template_path=str(self.template),
                           input_fn=Scripted(
                               ["y", "", "", "", "", "", "x"]
                           ), output_fn=out)
        self.assertEqual(rc, 1)
        self.assertFalse(self.config.exists())
        self.assertIn("Cancelled", out.text())

    def test_x_at_save_prompt_leaves_existing_file_alone(self):
        self.write("output_base: organized\n")
        original = self.config.read_text()
        # no phone_pull keys → walk is 15 prompts, then x declines at save
        answers = [
            "", "", "", "", "",
            "y", "y", "y", "y", "y", "y", "y",
            "y", "y", "",
            "x",
        ]
        out = Capture()
        rc = ce.run_editor(str(self.config), template_path=str(self.template),
                           input_fn=Scripted(answers), output_fn=out)
        self.assertEqual(rc, 1)
        self.assertEqual(self.config.read_text(), original)
        self.assertIn("Cancelled", out.text())

    def test_declining_save_leaves_existing_file_alone(self):
        self.write("output_base: organized\n")
        original = self.config.read_text()
        # This config has no phone_pull section, so the walk skips the
        # three phone-pull path prompts: 16 answers, last one declines.
        answers = [
            "",                        # drives: done
            "",                        # source folders: done
            "",                        # sync pairs: done
            "",                        # one-way pairs: done
            "",                        # output_base: keep
            "y", "y", "y", "y", "y", "y", "y",
            "y",                       # router: keep OFF
            "y",                       # phone pull: keep OFF (key absent)
            "",                        # exclusions: done
            "n",                       # decline saving
        ]
        rc = ce.run_editor(str(self.config), template_path=str(self.template),
                           input_fn=Scripted(answers), output_fn=Capture())
        self.assertEqual(rc, 1)
        self.assertEqual(self.config.read_text(), original)

    def test_valid_config_skip_keeps_values(self):
        self.write(
            "drives:\n  MAIN_DRIVE: /tmp\n"
            "source_folders:\n  - /tmp/src\n"
            "output_base: organized\n"
            "sync_pairs: []\n"
            "one_way_pairs: []\n"
        )
        out = Capture()
        # No phone_pull keys → walk is 15 prompts + save (blank → default y).
        answers = [
            "",                        # drives: done
            "",                        # source folders: done
            "",                        # sync pairs: done
            "",                        # one-way pairs: done
            "",                        # output_base: keep
            "y", "y", "y", "y", "y", "y", "y",
            "y",                       # router: keep OFF
            "y",                       # phone pull: keep OFF (key absent)
            "",                        # exclusions: done
            "",                        # save: default yes
        ]
        rc = ce.run_editor(str(self.config), template_path=str(self.template),
                           input_fn=Scripted(answers), output_fn=out)
        self.assertEqual(rc, 0)
        saved = yaml.safe_load(self.config.read_text())
        self.assertEqual(saved["drives"]["MAIN_DRIVE"], "/tmp")
        self.assertEqual(saved["source_folders"], ["/tmp/src"])
        self.assertIn("Loaded your current config.yaml", out.text())

    def test_editing_a_drive_lands_in_the_file(self):
        # Edit MAIN_DRIVE during the walk, then save.
        answers = [
            "y",                       # start fresh
            "e1", "", "/tmp", "",      # drives: edit 1 (keep name, new folder), then done
            "",                        # source folders: done
            "",                        # sync pairs: done
            "",                        # one-way pairs: done
            "",                        # output_base: keep
            "y", "y", "y", "y", "y", "y", "y",
            "y",                       # router OFF
            "y",                       # phone pull ON
            "", "", "",
            "",                        # exclusions
            "y",                       # save
        ]
        rc = ce.run_editor(str(self.config), template_path=str(self.template),
                           input_fn=Scripted(answers), output_fn=Capture())
        self.assertEqual(rc, 0)
        saved = yaml.safe_load(self.config.read_text())
        self.assertEqual(saved["drives"]["MAIN_DRIVE"], "/tmp")

    def test_phone_pull_turned_off_skips_path_questions(self):
        answers = FULL_WALK_KEEP_ALL[:]
        # Index 14 is the phone-pull toggle; flipping it OFF skips the
        # three phone-pull path prompts that follow.
        answers[14] = "n"
        out = Capture()
        rc = ce.run_editor(str(self.config), template_path=str(self.template),
                           input_fn=Scripted(answers), output_fn=out)
        self.assertEqual(rc, 0)
        saved = yaml.safe_load(self.config.read_text())
        self.assertFalse(saved["phone_pull"]["auto_pull"])


if __name__ == "__main__":
    unittest.main()

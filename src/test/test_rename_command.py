"""Tests for 'organize rename' — interactive renaming of held
(app-generated) files on the phone."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from file_organizer import phone_sync  # noqa: E402
from file_organizer.phone_sync import (  # noqa: E402
    PhoneSync,
    apply_rename,
    collect_held_files,
    rename_held_interactive,
)


class ApplyRenameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _file(self, name: str) -> Path:
        p = self.dir / name
        p.write_text("x")
        return p

    def test_renames_and_keeps_extension(self):
        p = self._file("shot.jpeg")
        new = apply_rename(p, "bank-statement")
        self.assertEqual(new.name, "bank-statement.jpeg")
        self.assertTrue(new.exists())
        self.assertFalse(p.exists())

    def test_explicit_extension_wins(self):
        p = self._file("shot.jpeg")
        new = apply_rename(p, "scan.pdf")
        self.assertEqual(new.name, "scan.pdf")
        self.assertTrue(new.exists())

    def test_empty_name_raises(self):
        p = self._file("shot.jpeg")
        with self.assertRaises(ValueError):
            apply_rename(p, "   ")
        self.assertTrue(p.exists())

    def test_path_separators_and_dots_refused(self):
        p = self._file("shot.jpeg")
        for bad in ("a/b", "..", ".", "a\\b"):
            with self.assertRaises(ValueError):
                apply_rename(p, bad)
        self.assertTrue(p.exists())

    def test_existing_target_refused(self):
        p = self._file("shot.jpeg")
        self._file("taken.jpeg")
        with self.assertRaises(ValueError):
            apply_rename(p, "taken")
        self.assertTrue(p.exists())


class CollectHeldFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.src = self.dir / "pictures"
        self.src.mkdir()

    def _sync(self, extra: str = "") -> PhoneSync:
        config = self.dir / "phone_sync_config.yaml"
        config.write_text(
            f"db_path: \"{self.dir}/db.sqlite\"\n"
            f"target_directory: \"{self.dir}/staging\"\n"
            "min_age_minutes: 10\n"
            "source_directories:\n"
            f"  - \"{self.src}\"\n"
            + extra
        )
        return phone_sync.PhoneSync(str(config))

    def test_collects_only_app_generated_names(self):
        (self.src / "Screenshot_2026-01-01.png").write_text("x")
        (self.src / "holiday-photo.png").write_text("x")
        sync = self._sync()
        names = [p.name for p in collect_held_files(sync)]
        self.assertIn("Screenshot_2026-01-01.png", names)
        self.assertNotIn("holiday-photo.png", names)

    def test_fresh_files_included_min_age_ignored(self):
        # A brand-new screenshot is still offered for renaming.
        (self.src / "20261010_120000.jpg").write_text("x")
        sync = self._sync()
        self.assertEqual(len(collect_held_files(sync)), 1)

    def test_hold_disabled_returns_nothing(self):
        (self.src / "Screenshot_2026-01-01.png").write_text("x")
        sync = self._sync("hold_app_generated_names: false\n")
        self.assertEqual(collect_held_files(sync), [])


class RenameInteractiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.src = self.dir / "pictures"
        self.src.mkdir()

    def _sync(self, extra: str = "") -> PhoneSync:
        config = self.dir / "phone_sync_config.yaml"
        config.write_text(
            f"db_path: \"{self.dir}/db.sqlite\"\n"
            f"target_directory: \"{self.dir}/staging\"\n"
            "min_age_minutes: 10\n"
            "source_directories:\n"
            f"  - \"{self.src}\"\n"
            + extra
        )
        return phone_sync.PhoneSync(str(config))

    def test_renames_keeps_and_reprompts_on_bad_name(self):
        # Names without any YYYYMMDD / YYYY-MM-DD date, so no prefill.
        (self.src / "BBl-Screenshot-1700019577590.jpeg").write_text("x")
        (self.src / "Snapchat-12345.png").write_text("x")
        (self.src / "dsc_0001.jpg").write_text("x")
        sync = self._sync()

        prompts = []
        answers = iter(["wedding-photo", "", "a/b", "receipt"])
        stats = rename_held_interactive(
            sync,
            input_fn=lambda prompt: (
                prompts.append(prompt) or next(answers)
            ),
            print_fn=lambda msg: None,
        )
        self.assertEqual(stats, {"renamed": 2, "kept": 1, "deleted": 0})
        # File 1 renamed, extension kept.
        self.assertTrue((self.src / "wedding-photo.jpeg").exists())
        self.assertFalse(
            (self.src / "BBl-Screenshot-1700019577590.jpeg").exists()
        )
        # File 2 kept on empty input.
        self.assertTrue((self.src / "Snapchat-12345.png").exists())
        # File 3: bad name re-prompts, then renames.
        self.assertTrue((self.src / "receipt.jpg").exists())
        self.assertFalse((self.src / "dsc_0001.jpg").exists())
        # The prompt is exactly the format the user asked for.
        self.assertEqual(
            prompts[0],
            "name: BBl-Screenshot-1700019577590.jpeg "
            "new name (d for delete): ",
        )
        self.assertEqual(len(prompts), 4)

    def test_d_deletes_the_file(self):
        (self.src / "Snapchat-12345.png").write_text("x")
        sync = self._sync()
        messages = []
        stats = rename_held_interactive(
            sync,
            input_fn=lambda prompt: "d",
            print_fn=messages.append,
        )
        self.assertEqual(stats, {"renamed": 0, "kept": 0, "deleted": 1})
        self.assertFalse((self.src / "Snapchat-12345.png").exists())
        self.assertTrue(any("deleted Snapchat-12345.png" in m for m in messages))

    def test_uppercase_D_deletes_the_file(self):
        (self.src / "Snapchat-12345.png").write_text("x")
        sync = self._sync()
        stats = rename_held_interactive(
            sync,
            input_fn=lambda prompt: "D",
            print_fn=lambda msg: None,
        )
        self.assertEqual(stats, {"renamed": 0, "kept": 0, "deleted": 1})
        self.assertFalse((self.src / "Snapchat-12345.png").exists())

    def test_date_prefill_prompt_and_rename(self):
        # A YYYY-MM-DD date in the name prefills the normalised date.
        (self.src / "Screenshot-2026-09-05-999.txt").write_text("x")
        sync = self._sync()
        prompts = []
        stats = rename_held_interactive(
            sync,
            input_fn=lambda prompt: (
                prompts.append(prompt) or "party"
            ),
            print_fn=lambda msg: None,
        )
        self.assertEqual(
            prompts[0],
            "name: Screenshot-2026-09-05-999.txt "
            "new name (d for delete): 20260905-",
        )
        self.assertEqual(stats, {"renamed": 1, "kept": 0, "deleted": 0})
        self.assertTrue((self.src / "20260905-party.txt").exists())
        self.assertFalse(
            (self.src / "Screenshot-2026-09-05-999.txt").exists()
        )

    def test_eight_digit_date_prefill(self):
        (self.src / "Screenshot-20260905-999.jpg").write_text("x")
        sync = self._sync()
        prompts = []
        stats = rename_held_interactive(
            sync,
            input_fn=lambda prompt: (
                prompts.append(prompt) or "party"
            ),
            print_fn=lambda msg: None,
        )
        self.assertTrue(prompts[0].endswith("new name (d for delete): 20260905-"))
        self.assertTrue((self.src / "20260905-party.jpg").exists())

    def test_date_prefill_enter_alone_keeps(self):
        (self.src / "Screenshot-2026-09-05-999.txt").write_text("x")
        sync = self._sync()
        stats = rename_held_interactive(
            sync,
            input_fn=lambda prompt: "",
            print_fn=lambda msg: None,
        )
        self.assertEqual(stats, {"renamed": 0, "kept": 1, "deleted": 0})
        self.assertTrue(
            (self.src / "Screenshot-2026-09-05-999.txt").exists()
        )

    def test_date_prefill_d_deletes(self):
        (self.src / "Screenshot-2026-09-05-999.txt").write_text("x")
        sync = self._sync()
        stats = rename_held_interactive(
            sync,
            input_fn=lambda prompt: "d",
            print_fn=lambda msg: None,
        )
        self.assertEqual(stats, {"renamed": 0, "kept": 0, "deleted": 1})
        self.assertFalse(
            (self.src / "Screenshot-2026-09-05-999.txt").exists()
        )

    def test_nothing_to_rename(self):
        sync = self._sync()
        messages = []
        stats = rename_held_interactive(
            sync,
            input_fn=lambda prompt: "",
            print_fn=messages.append,
        )
        self.assertEqual(stats, {"renamed": 0, "kept": 0, "deleted": 0})
        self.assertIn("Nothing to rename", messages[0])

    def test_warns_when_new_name_still_looks_app_generated(self):
        (self.src / "Snapchat-123.png").write_text("x")
        sync = self._sync()
        messages = []
        stats = rename_held_interactive(
            sync,
            input_fn=lambda prompt: "Snapchat-456",
            print_fn=messages.append,
        )
        self.assertEqual(stats, {"renamed": 1, "kept": 0, "deleted": 0})
        self.assertTrue((self.src / "Snapchat-456.png").exists())
        self.assertTrue(any("still looks app-generated" in m for m in messages))

    def test_eof_stops_gracefully(self):
        (self.src / "Screenshot_2026-01-01.png").write_text("x")
        sync = self._sync()

        def _eof(prompt):
            raise EOFError()

        messages = []
        stats = rename_held_interactive(
            sync, input_fn=_eof, print_fn=messages.append,
        )
        self.assertEqual(stats, {"renamed": 0, "kept": 0, "deleted": 0})
        self.assertTrue(any("Stopped" in m for m in messages))
        self.assertTrue((self.src / "Screenshot_2026-01-01.png").exists())


class DatePrefixInTests(unittest.TestCase):
    """The prompt prefill: normalised YYYYMMDD- of a date in the name."""

    def test_dashed_date_anywhere(self):
        self.assertEqual(
            phone_sync.date_prefix_in("Screenshot-2026-09-05-999.txt"),
            "20260905-",
        )

    def test_eight_digit_date(self):
        self.assertEqual(
            phone_sync.date_prefix_in("Screenshot-20260905-999.jpg"),
            "20260905-",
        )

    def test_underscore_and_dot_separators(self):
        self.assertEqual(phone_sync.date_prefix_in("a_2026_09_05_b"), "20260905-")
        self.assertEqual(phone_sync.date_prefix_in("a.2026.09.05.b"), "20260905-")

    def test_invalid_month_or_day_rejected(self):
        self.assertEqual(phone_sync.date_prefix_in("x-2026-24-05-y"), "")
        self.assertEqual(phone_sync.date_prefix_in("x-2026-09-45-y"), "")
        self.assertEqual(
            phone_sync.date_prefix_in("IMG-20241319-WA0000.jpg"), ""
        )  # month 13 is not a date

    def test_no_date_returns_empty(self):
        self.assertEqual(phone_sync.date_prefix_in("holiday-photo.png"), "")
        self.assertEqual(
            phone_sync.date_prefix_in("BBl-Screenshot-1788321667280.jpeg"), ""
        )  # phone timestamp, not a 19xx/20xx date

    def test_date_glued_to_digits_ignored(self):
        self.assertEqual(phone_sync.date_prefix_in("12026090599.jpg"), "")


if __name__ == "__main__":
    unittest.main()

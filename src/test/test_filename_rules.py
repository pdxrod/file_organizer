"""Tests for item 13: hold app-generated filenames until renamed and
normalise leading dates to YYYYMMDD.

The patterns are grounded in real examples from the user's folders:
- Screenshot_20250831_190852_Gmail_1.jpg, BBl-Screenshot-1700019577590.jpeg,
- 20260409_050201.jpg, 20260703_100056 (1)_1.jpg, IMG-20240219-WA0000.jpg,
- 20260218_150629_LINE_B204.jpg, 20260427_133222.mp4,
- 2025-12-06-postal-annex-receipt_1.png, 2026_08_09_clairlemon_caitoz.jpg.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from file_organizer import phone_sync  # noqa: E402
from file_organizer.filename_rules import (  # noqa: E402
    is_app_generated, normalise_leading_date,
)

import logging  # noqa: E402
logging.getLogger("phone_sync").addHandler(logging.NullHandler())


class AppGeneratedTests(unittest.TestCase):
    def test_app_names_are_held(self):
        held = [
            "Screenshot_20250831_190852_Gmail_1.jpg",
            "BBl-Screenshot-1700019577590.jpeg",
            "20260409_050201.jpg",
            "20260703_100056 (1)_1.jpg",
            "20260427_133222.mp4",
            "20260427_133222_1.mp4",
            "IMG-20240219-WA0000.jpg",
            "VID-20240219-WA0001.mp4",
            "IMG_20260101_123456.jpg",
            "PXL_20260101_123456789.jpg",
            "MVIMG_20260101_123456.jpg",
            "20260218_150629_LINE_B204.jpg",
            "WhatsApp Image 2026-01-01 at 12.34.56.jpeg",
            "Screen Shot 2026-01-01 at 12.34.56.png",
            "Screen Recording 2026-01-01 at 12.34.56.mov",
            "photo_2026-01-01_12-34-56.jpg",
            "Snapchat-123456789.jpg",
            "DSC_0001.jpg",
            "DSC01234.jpg",
            "Audio_20260101_123456.m4a",
            "2026-01-01 12.34.56.jpg",
            "2026-01-01 at 12.34.56.jpg",
        ]
        for name in held:
            self.assertTrue(is_app_generated(name), name)

    def test_user_names_are_not_held(self):
        copied = [
            "yugoslavia.txt",
            "20231210_covid_excess_deaths_sweden_v_world.jpg",
            # Timestamp + real words = user renamed it.
            "20231203_014527_lehmann_shulevitz_hard_moral_choices.jpg",
            "2025-12-06-postal-annex-receipt_1.png",
            "2026-09-23_macklemore_mocha_miracles.png",
            "report (2026).txt",
            "IMG-2026-01-01-birthday.jpg",  # IMG + date + real words
        ]
        for name in copied:
            self.assertFalse(is_app_generated(name), name)


class NormaliseDateTests(unittest.TestCase):
    def test_leading_dates_normalised(self):
        cases = {
            "2025-12-06-postal-annex-receipt_1.png":
                "20251206-postal-annex-receipt_1.png",
            "2026_08_09_clairlemon_caitoz_partisangirl_blocks_me.jpg":
                "20260809_clairlemon_caitoz_partisangirl_blocks_me.jpg",
            "2026.09.23-macklemore.png": "20260923-macklemore.png",
            "IMG-2026-01-01-birthday.jpg": "IMG-20260101-birthday.jpg",
            "2025-12-07-different-ai-systems-haaretz-20231007-rape-claims.txt":
                "20251207-different-ai-systems-haaretz-20231007-rape-claims.txt",
            "2026-09-06-gloria-steinem.jpg": "20260906-gloria-steinem.jpg",
        }
        for name, expected in cases.items():
            self.assertEqual(normalise_leading_date(name), expected, name)

    def test_untouched_names(self):
        unchanged = [
            "IMG_20260101_123456.jpg",      # already YYYYMMDD (app-generated)
            "20260101_123456.jpg",
            "yugoslavia.txt",
            "report.txt",
            "2026-99-99-invalid.jpg",       # not a real date
            "2026-13-40-invalid.jpg",
        ]
        for name in unchanged:
            self.assertEqual(normalise_leading_date(name), name, name)


class PhoneHoldAndNormaliseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        self.src = self.dir / "DCIM"
        self.src.mkdir()
        self.target = self.dir / "staging"
        self.old = time.time() - 7200

    def _write(self, name, content="x"):
        p = self.src / name
        p.write_text(content)
        os.utime(p, (self.old, self.old))
        return p

    def _sync(self, extra: str = ""):
        config = self.dir / "phone_sync_config.yaml"
        config.write_text(
            f"db_path: \"{self.dir}/db.sqlite\"\n"
            f"target_directory: \"{self.target}\"\n"
            "min_age_minutes: 0\n"
            # Never touch REAL cloud drives from tests: with the new
            # default sync_cloud_drives: true, the finder would otherwise
            # discover the developer's Proton Drive and write into it.
            "cloud_drive_paths:\n"
            f"  - \"{self.dir}/no-cloud-drive-here\"\n"
            "source_directories:\n"
            f"  - \"{self.src}\"\n"
            + extra
        )
        return phone_sync.PhoneSync(str(config))

    def test_app_generated_file_is_held(self):
        p = self._write("Screenshot_20260101-123456.png")
        sync = self._sync()
        try:
            stats = sync.scan_and_sync(dry_run=False)
            self.assertEqual(stats["held"], 1)
            self.assertEqual(stats["copied"], 0)
            self.assertFalse((self.target / p.name).exists())
        finally:
            sync.db.close()

    def test_hold_can_be_disabled(self):
        p = self._write("Screenshot_20260101-123456.png")
        sync = self._sync("hold_app_generated_names: false\n")
        try:
            stats = sync.scan_and_sync(dry_run=False)
            self.assertEqual(stats["held"], 0)
            self.assertTrue((self.target / p.name).exists())
        finally:
            sync.db.close()

    def test_leading_date_normalised_on_staged_copy(self):
        self._write("2026-09-23-party.jpg", "pic")
        sync = self._sync()
        try:
            stats = sync.scan_and_sync(dry_run=False)
            self.assertEqual(stats["copied"], 1)
            self.assertFalse((self.target / "2026-09-23-party.jpg").exists())
            self.assertTrue((self.target / "20260923-party.jpg").exists())
            # The source keeps its original name.
            self.assertTrue((self.src / "2026-09-23-party.jpg").exists())
        finally:
            sync.db.close()

    def test_normalisation_can_be_disabled(self):
        self._write("2026-09-23-party.jpg", "pic")
        sync = self._sync("normalise_leading_dates: false\n")
        try:
            sync.scan_and_sync(dry_run=False)
            self.assertTrue((self.target / "2026-09-23-party.jpg").exists())
            self.assertFalse((self.target / "20260923-party.jpg").exists())
        finally:
            sync.db.close()


if __name__ == "__main__":
    unittest.main()

"""Tests for item 10: no _1/(N) renames.

Covers:
- rename_family.parse_family on all the family shapes in the spec.
- plan_collapse / apply_collapse (newest wins, siblings deleted, min-age).
- phone copy_file never creates _N suffixes: overwrite when newer+different
  size, keep when not newer.
- phone-side family collapse behind collapse_rename_families, with
  dry-run planning and min-age protection.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from file_organizer import phone_sync  # noqa: E402
from file_organizer.rename_family import (  # noqa: E402
    parse_family, plan_collapse, apply_collapse,
    plan_underscore_duplicates,
)

import logging  # noqa: E402
logging.getLogger("phone_sync").addHandler(logging.NullHandler())


class ParseFamilyTests(unittest.TestCase):
    def test_plain_names_are_not_families(self):
        for name in ("foo.png", "foo_1.png", "IMG_20260101_123456.jpg",
                     "yugoslavia.txt", ""):
            self.assertIsNone(parse_family(name), name)

    def test_year_suffixes_are_not_families(self):
        # 4-digit groups like (2026) are years, not rename counters.
        self.assertIsNone(parse_family("report (2026).txt"))

    def test_family_shapes_map_to_base(self):
        expected = {
            "foo (1).png": "foo.png",
            "foo (2).png": "foo.png",
            "foo (1) (1).png": "foo.png",
            "foo (1) (2).png": "foo.png",
            "foo (1) (1)_1.png": "foo.png",
            "photo (12).jpg": "photo.jpg",
            "doc (3) (4).pdf": "doc.pdf",
        }
        for name, base in expected.items():
            fm = parse_family(name)
            self.assertIsNotNone(fm, name)
            self.assertEqual(fm.base_name, base)


class PlanCollapseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        self.old = time.time() - 7200

    def _write(self, name: str, content: str = "x", age: float = None):
        p = self.dir / name
        p.write_text(content)
        mtime = age if age is not None else self.old
        os.utime(p, (mtime, mtime))
        return p

    def test_newest_member_becomes_base_others_deleted(self):
        base = self._write("foo.png", "old", age=self.old - 100)
        m1 = self._write("foo (1).png", "one", age=self.old - 50)
        m2 = self._write("foo (2).png", "newest", age=self.old)
        actions = plan_collapse(self.dir)
        kinds = {(a.kind, a.path.name): a for a in actions}
        self.assertEqual(kinds[("rename", "foo (2).png")].target, base)
        self.assertIn(("delete", "foo (1).png"), kinds)
        # The old base is replaced by the rename, not deleted separately.
        self.assertNotIn(("delete", "foo.png"), kinds)

    def test_family_without_base_renames_latest(self):
        self._write("foo (1).png", "one", age=self.old - 50)
        m2 = self._write("foo (2).png", "two", age=self.old)
        actions = plan_collapse(self.dir)
        self.assertEqual(len(actions), 2)
        self.assertIn(("rename", "foo (2).png"), {(a.kind, a.path.name) for a in actions})
        self.assertEqual(
            next(a for a in actions if a.kind == "rename").target.name, "foo.png"
        )
        self.assertIn(("delete", "foo (1).png"), {(a.kind, a.path.name) for a in actions})

    def test_single_member_without_base_is_left_alone(self):
        self._write("foo (1).png")
        self.assertEqual(plan_collapse(self.dir), [])

    def test_min_age_skips_fresh_families(self):
        self._write("foo (1).png", age=time.time() - 60)
        self._write("foo (2).png", age=time.time() - 30)
        actions = plan_collapse(self.dir, min_age_seconds=600)
        self.assertEqual(actions, [])

    def test_apply_dry_run_changes_nothing(self):
        m1 = self._write("foo (1).png", "one", age=self.old - 50)
        m2 = self._write("foo (2).png", "newest", age=self.old)
        actions = plan_collapse(self.dir)
        stats = apply_collapse(actions, dry_run=True)
        self.assertEqual(stats, {"renamed": 0, "deleted": 0})
        self.assertTrue(m1.exists() and m2.exists())
        self.assertFalse((self.dir / "foo.png").exists())

    def test_apply_for_real(self):
        self._write("foo (1).png", "one", age=self.old - 50)
        self._write("foo (2).png", "newest", age=self.old)
        actions = plan_collapse(self.dir)
        stats = apply_collapse(actions, dry_run=False)
        self.assertEqual(stats, {"renamed": 1, "deleted": 1})
        self.assertEqual((self.dir / "foo.png").read_text(), "newest")
        self.assertFalse((self.dir / "foo (1).png").exists())
        self.assertFalse((self.dir / "foo (2).png").exists())

    def test_keep_differing_skips_divergent_family(self):
        # A web-archive family where the newest member differs from the
        # base: renaming over the base would destroy its content — safe
        # mode keeps the whole family untouched.
        base = self._write("page.png", "base-content", age=self.old - 100)
        mid = self._write("page (1).png", "base-content", age=self.old - 50)
        new = self._write("page (2).png", "DIFFERENT", age=self.old)
        actions = plan_collapse(self.dir, keep_differing=True)
        self.assertEqual(actions, [])
        self.assertTrue(base.exists() and mid.exists() and new.exists())

    def test_keep_differing_identical_family_collapses(self):
        # All members identical — safe mode still collapses the family.
        self._write("page.png", "same", age=self.old - 100)
        self._write("page (1).png", "same", age=self.old - 50)
        self._write("page (2).png", "same", age=self.old)
        actions = plan_collapse(self.dir, keep_differing=True)
        kinds = {(a.kind, a.path.name) for a in actions}
        self.assertEqual(kinds, {
            ("rename", "page (2).png"),
            ("delete", "page (1).png"),
        })


class UnderscoreDuplicateTests(unittest.TestCase):
    """Plain '_N' duplicates (foo_1.png) — the thousands of leftovers from
    the Mac's old behaviour. Content-checked: only IDENTICAL copies are
    deleted; numbered assets that are really different files are kept."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        self.old = time.time() - 7200

    def _write(self, name, content):
        p = self.dir / name
        p.write_text(content)
        os.utime(p, (self.old, self.old))
        return p

    def test_identical_underscore_copy_is_deleted(self):
        base = self._write("donatelli-david.jpg", "photo")
        dup = self._write("donatelli-david_1.jpg", "photo")
        actions = plan_underscore_duplicates(self.dir)
        self.assertEqual(
            [(a.kind, a.path.name) for a in actions],
            [("delete", "donatelli-david_1.jpg")],
        )
        apply_collapse(actions, dry_run=False)
        self.assertTrue(base.exists())
        self.assertFalse(dup.exists())

    def test_different_content_numbered_file_is_kept(self):
        # Web archives: combo_019.js is a different asset than combo.js.
        self._write("combo.js", "var a = 1")
        keep = self._write("combo_019.js", "var b = 2")
        actions = plan_underscore_duplicates(self.dir)
        self.assertEqual(actions, [])
        self.assertTrue(keep.exists())

    def test_no_base_file_leaves_numbered_name_alone(self):
        # Legit numbered names like IMG_20260101_123456.jpg stay.
        keep = self._write("IMG_20260101_123456.jpg", "x")
        actions = plan_underscore_duplicates(self.dir)
        self.assertEqual(actions, [])
        self.assertTrue(keep.exists())

    def test_multiple_identical_numbered_copies_all_deleted(self):
        base = self._write("report.txt", "r")
        self._write("report_1.txt", "r")
        self._write("report_2.txt", "r")
        self._write("report_3.txt", "r")
        actions = plan_underscore_duplicates(self.dir)
        self.assertEqual(len(actions), 3)
        apply_collapse(actions, dry_run=False)
        self.assertTrue(base.exists())
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         ["report.txt"])


class PhoneCopyOverwriteTests(unittest.TestCase):
    """copy_file must never invent _1/_N names (item 10)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        self.a = self.dir / "a"
        self.b = self.dir / "b"
        self.c = self.dir / "c"
        for d in (self.a, self.b, self.c):
            (d / "docs").mkdir(parents=True)
        self.target = self.dir / "staging"

    def _sync(self, extra: str = ""):
        config = self.dir / "phone_sync_config.yaml"
        config.write_text(
            f"db_path: \"{self.dir}/db.sqlite\"\n"
            f"target_directory: \"{self.target}\"\n"
            "min_age_minutes: 0\n"
            "source_directories:\n"
            f"  - \"{self.a}\"\n"
            f"  - \"{self.b}\"\n"
            f"  - \"{self.c}\"\n"
            + extra
        )
        return phone_sync.PhoneSync(str(config))

    def _write(self, base: Path, name: str, content: str, age: float):
        p = base / "docs" / name
        p.write_text(content)
        os.utime(p, (age, age))
        return p

    def test_no_underscore_suffix_and_newer_wins(self):
        old = time.time() - 7200
        self._write(self.a, "foo.png", "AAA", old)
        self._write(self.b, "foo.png", "BBBB", old + 600)
        self._write(self.c, "foo.png", "CC", old - 600)  # older than everything
        sync = self._sync()
        try:
            sync.run_once(dry_run=False)
            staged = self.target / "docs" / "foo.png"
            self.assertTrue(staged.exists())
            # No foo_1.png ever.
            self.assertFalse((self.target / "docs" / "foo_1.png").exists())
            self.assertEqual(staged.read_text(), "BBBB")
        finally:
            sync.db.close()

    def test_older_incoming_keeps_staged_copy(self):
        old = time.time() - 7200
        self._write(self.a, "foo.png", "AAA", old)
        self._write(self.c, "foo.png", "CCC", old - 3600)  # older, not known
        sync = self._sync()
        try:
            sync.run_once(dry_run=False)
            staged = self.target / "docs" / "foo.png"
            self.assertEqual(staged.read_text(), "AAA")
            # The older variant must not be backed up under a new name.
            self.assertEqual(
                sorted(p.name for p in (self.target / "docs").iterdir()),
                ["foo.png"],
            )
        finally:
            sync.db.close()


class PhoneCollapseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        self.src = self.dir / "DCIM"
        self.src.mkdir()
        self.target = self.dir / "staging"
        self.old = time.time() - 7200

    def _sync(self, collapse: str, min_age: int = 0):
        config = self.dir / "phone_sync_config.yaml"
        config.write_text(
            f"db_path: \"{self.dir}/db.sqlite\"\n"
            f"target_directory: \"{self.target}\"\n"
            f"min_age_minutes: {min_age}\n"
            f"collapse_rename_families: {collapse}\n"
            "source_directories:\n"
            f"  - \"{self.src}\"\n"
        )
        return phone_sync.PhoneSync(str(config))

    def _family(self, fresh: bool = False):
        p1 = self.src / "foo (1).png"
        p2 = self.src / "foo (2).png"
        p1.write_text("one")
        p2.write_text("newest")
        age = time.time() - 60 if fresh else self.old
        for p in (p1, p2):
            os.utime(p, (age, age))
        return p1, p2

    def test_disabled_by_default(self):
        p1, p2 = self._family()
        sync = self._sync("false")
        try:
            stats = sync.scan_and_sync(dry_run=False)
            self.assertEqual(stats["collapsed"], 0)
            self.assertTrue(p1.exists() and p2.exists())
        finally:
            sync.db.close()

    def test_dry_run_plans_but_does_not_touch(self):
        p1, p2 = self._family()
        sync = self._sync("true")
        try:
            sync.scan_and_sync(dry_run=True)
            self.assertTrue(p1.exists() and p2.exists())
            self.assertFalse((self.src / "foo.png").exists())
        finally:
            sync.db.close()

    def test_production_collapses_then_syncs(self):
        p1, p2 = self._family()
        sync = self._sync("true")
        try:
            stats = sync.scan_and_sync(dry_run=False)
            self.assertEqual(stats["collapsed"], 2)  # 1 rename + 1 delete
            self.assertFalse(p1.exists() and p2.exists())
            self.assertEqual((self.src / "foo.png").read_text(), "newest")
            # The collapsed file was then copied to staging.
            self.assertEqual(
                (self.target / "foo.png").read_text(), "newest"
            )
        finally:
            sync.db.close()

    def test_min_age_protects_fresh_families(self):
        p1, p2 = self._family(fresh=True)
        sync = self._sync("true", min_age=10)
        try:
            stats = sync.scan_and_sync(dry_run=False)
            self.assertEqual(stats["collapsed"], 0)
            self.assertTrue(p1.exists() and p2.exists())
        finally:
            sync.db.close()


if __name__ == "__main__":
    unittest.main()

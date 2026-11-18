"""Tests for the sweep/restore safety net.

Guards the failure mode that corrupted the user's data on 19 Nov: a sweep
crashed midway, the next restore skipped same-named items (leaving them in
the backup without a manifest), and the next sweep piled new items onto
those leftovers. backup-major-folders.py must refuse to run while any
backup data exists, and restore-major-folders.py must keep the manifest
when it couldn't restore everything.
"""

import importlib.util
from importlib.machinery import SourceFileLoader
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import testplan_common as tc

_here = Path(__file__).resolve().parent


def _load(name):
    loader = SourceFileLoader(f"{name}_under_test", str(_here / f"{name}.py"))
    spec = importlib.util.spec_from_loader(f"{name}_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


backup = _load("backup-major-folders")
restore = _load("restore-major-folders")


class SweepSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.broot = self.root / "backup"
        self.broot.mkdir()
        # Point the shared module at the temp backup, not the real one.
        self._old_root = tc.BACKUP_ROOT
        self._old_manifest = tc.MANIFEST_PATH
        tc.BACKUP_ROOT = self.broot
        tc.MANIFEST_PATH = self.broot / "manifest.json"
        self.addCleanup(self._restore_paths)
        self.addCleanup(self._stop_stdout)

    def _restore_paths(self):
        tc.BACKUP_ROOT = self._old_root
        tc.MANIFEST_PATH = self._old_manifest

    def _stop_stdout(self):
        pass

    def _config(self, pairs, drives=None):
        cfg = self.root / "config.yaml"
        drives = drives or {
            "MAIN_DRIVE": str(self.root / "main"),
            "PROTON_DRIVE": str(self.root / "proton"),
        }
        text = "drives:\n"
        for key, val in drives.items():
            text += f"  {key}: {val}\n"
        text += "sync_pairs:\n"
        for a, b in pairs:
            text += f"- folders:\n  - {a}\n  - {b}\n"
        cfg.write_text(text)
        return cfg

    def _run(self, mod, cfg):
        with mock.patch.object(sys, "argv",
                               [mod.__file__, "--config", str(cfg)]):
            return mod.main()

    def test_backup_refuses_when_manifest_exists(self):
        tc.MANIFEST_PATH.write_text("{}")
        (self.root / "main" / "pol").mkdir(parents=True)
        (self.root / "proton" / "pol").mkdir(parents=True)
        cfg = self._config([("MAIN_DRIVE/pol", "PROTON_DRIVE/pol")])
        with self.assertRaises(SystemExit) as ctx:
            self._run(backup, cfg)
        self.assertEqual(ctx.exception.code, 1)
        # Nothing was swept.
        self.assertEqual(list((self.root / "main" / "pol").iterdir()), [])

    def test_backup_refuses_on_leftovers_without_manifest(self):
        (self.broot / "folders" / "0").mkdir(parents=True)
        (self.broot / "folders" / "0" / "stray.txt").write_text("x")
        (self.root / "main" / "pol").mkdir(parents=True)
        (self.root / "proton" / "pol").mkdir(parents=True)
        cfg = self._config([("MAIN_DRIVE/pol", "PROTON_DRIVE/pol")])
        with self.assertRaises(SystemExit) as ctx:
            self._run(backup, cfg)
        self.assertEqual(ctx.exception.code, 1)
        # The source folders were NOT swept.
        self.assertEqual(list((self.root / "main" / "pol").iterdir()), [])

    def test_backup_allows_empty_leftover_dirs(self):
        # A finished restore leaves empty folders/0..N behind; that must
        # not block the next sweep.
        (self.broot / "folders" / "0").mkdir(parents=True)
        (self.broot / "folders" / "1").mkdir(parents=True)
        a = self.root / "main" / "pol"
        b = self.root / "proton" / "pol"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        (a / "keep.txt").write_text("a")
        cfg = self._config([("MAIN_DRIVE/pol", "PROTON_DRIVE/pol")])
        self._run(backup, cfg)  # must not refuse
        self.assertEqual(
            sorted(p.name for p in (self.broot / "folders" / "0").iterdir()),
            ["keep.txt"],
        )

    def test_backup_sweeps_and_writes_manifest(self):
        a = self.root / "main" / "pol"
        b = self.root / "proton" / "pol"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        (a / "keep.txt").write_text("a")
        (b / "keep2.txt").write_text("b")
        cfg = self._config([("MAIN_DRIVE/pol", "PROTON_DRIVE/pol")])
        self._run(backup, cfg)
        # Swept empty of the user's files, then the script's own test
        # files are dropped in.
        self.assertEqual(
            sorted(p.name for p in a.iterdir()),
            [tc.SIDE_A_FILE, tc.PROJECT],
        )
        self.assertEqual(sorted(p.name for p in b.iterdir()),
                         [tc.SIDE_B_FILE])
        manifest = json.loads(tc.MANIFEST_PATH.read_text())
        self.assertEqual(len(manifest["folders"]), 2)
        self.assertEqual(
            sorted(p.name for p in (self.broot / "folders" / "0").iterdir()),
            ["keep.txt"],
        )

    def test_restore_puts_everything_back_and_removes_manifest(self):
        original = self.root / "docs"
        original.mkdir()
        (original / "keep.txt").write_text("x")
        backup_dir = self.broot / "folders" / "0"
        backup_dir.mkdir(parents=True)
        (backup_dir / "gone.txt").write_text("y")
        tc.MANIFEST_PATH.write_text(json.dumps({
            "folders": [{"original": str(original),
                         "backup": str(backup_dir)}]
        }))
        restore.main()
        self.assertTrue((original / "gone.txt").exists())
        self.assertFalse(tc.MANIFEST_PATH.exists())

    def test_restore_skips_conflicts_and_keeps_manifest(self):
        original = self.root / "docs"
        original.mkdir()
        (original / "same.txt").write_text("newer")
        backup_dir = self.broot / "folders" / "0"
        backup_dir.mkdir(parents=True)
        (backup_dir / "same.txt").write_text("older")
        (backup_dir / "gone.txt").write_text("y")
        tc.MANIFEST_PATH.write_text(json.dumps({
            "folders": [{"original": str(original),
                         "backup": str(backup_dir)}]
        }))
        restore.main()
        # The conflicting item stays in the backup; the manifest stays so a
        # new sweep cannot pile onto the leftovers.
        self.assertTrue((backup_dir / "same.txt").exists())
        self.assertTrue((original / "gone.txt").exists())
        self.assertTrue(tc.MANIFEST_PATH.exists())

        # After the conflict is resolved by hand, the next restore finishes
        # and removes the manifest.
        (original / "same.txt").unlink()
        restore.main()
        self.assertTrue((original / "same.txt").exists())
        self.assertFalse(tc.MANIFEST_PATH.exists())

    def test_backup_refuses_after_restore_skipped_items(self):
        # The full failure chain: restore skips a same-named item, then a
        # new sweep must refuse instead of moving new data onto the
        # leftovers.
        original = self.root / "docs"
        original.mkdir()
        (original / "same.txt").write_text("newer")
        backup_dir = self.broot / "folders" / "0"
        backup_dir.mkdir(parents=True)
        (backup_dir / "same.txt").write_text("older")
        tc.MANIFEST_PATH.write_text(json.dumps({
            "folders": [{"original": str(original),
                         "backup": str(backup_dir)}]
        }))
        restore.main()
        (self.root / "main" / "pol").mkdir(parents=True)
        (self.root / "proton" / "pol").mkdir(parents=True)
        cfg = self._config([("MAIN_DRIVE/pol", "PROTON_DRIVE/pol")])
        with self.assertRaises(SystemExit) as ctx:
            self._run(backup, cfg)
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(list((self.root / "main" / "pol").iterdir()), [])


if __name__ == "__main__":
    unittest.main()

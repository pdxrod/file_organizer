"""Tests for the run-level cross-device lock (run_lock.py).

Two computers running organize at once would both read and write the
same cloud drive. The run lock (.file_organizer.run.lock) lives at the
root of the shared cloud drive; the second machine to start sees the
fresh heartbeat and backs out of its whole cycle, retrying on a later
run. A crashed machine's heartbeat stops and its lock is taken over
after run_lock_stale_minutes.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from file_organizer import run_lock  # noqa: E402
from file_organizer.config import DEFAULTS  # noqa: E402


class StubConfig:
    def __init__(self, raw_config=None, drives=None):
        self.raw_config = raw_config or {}
        self.drives = drives or {}


class LockDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="run_lock_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _mk(self, *parts):
        p = self.tmp.joinpath(*parts)
        p.mkdir(parents=True)
        return p

    def test_auto_picks_existing_cloud_drive(self):
        proton = self._mk("Library", "CloudStorage", "ProtonDrive-x-folder")
        home = self._mk("home")
        cfg = StubConfig(
            {"run_lock_enabled": True, "run_lock_dir": ""},
            {"MAIN_DRIVE": str(home), "PROTON_DRIVE": str(proton)},
        )
        self.assertEqual(run_lock.RunLock(cfg)._dir, proton)

    def test_cloud_drive_wins_over_volumes(self):
        proton = self._mk("Library", "CloudStorage", "ProtonDrive-x-folder")
        vol = self._mk("Volumes", "Backup")
        cfg = StubConfig(
            {"run_lock_enabled": True, "run_lock_dir": ""},
            {"EXTERNAL_DRIVE": str(vol), "PROTON_DRIVE": str(proton)},
        )
        self.assertEqual(run_lock.RunLock(cfg)._dir, proton)

    def test_volume_is_used_when_no_cloud_drive(self):
        vol = self._mk("Volumes", "Backup")
        home = self._mk("home")
        cfg = StubConfig(
            {"run_lock_enabled": True, "run_lock_dir": ""},
            {"MAIN_DRIVE": str(home), "EXTERNAL_DRIVE": str(vol)},
        )
        self.assertEqual(run_lock.RunLock(cfg)._dir, vol)

    def test_local_only_drives_mean_no_lock(self):
        home = self._mk("home")
        docs = self._mk("home", "Documents")
        cfg = StubConfig(
            {"run_lock_enabled": True, "run_lock_dir": ""},
            {"MAIN_DRIVE": str(home), "DOCS_DRIVE": str(docs)},
        )
        lock = run_lock.RunLock(cfg)
        self.assertIsNone(lock._dir)
        self.assertFalse(lock.active)
        # A local-only setup proceeds without a marker and creates nothing.
        self.assertTrue(lock.acquire())
        self.assertFalse((home / run_lock.RUN_LOCK_NAME).exists())
        lock.release()  # no-op

    def test_missing_drive_paths_are_skipped(self):
        home = self._mk("home")
        cfg = StubConfig(
            {"run_lock_enabled": True, "run_lock_dir": ""},
            {"MAIN_DRIVE": str(home),
             "PROTON_DRIVE": str(self.tmp / "unmounted" / "Proton")},
        )
        lock = run_lock.RunLock(cfg)
        self.assertIsNone(lock._dir)
        self.assertFalse(
            (self.tmp / "unmounted" / "Proton").exists()
        )

    def test_explicit_dir_wins(self):
        shared = self._mk("nas")
        cfg = StubConfig({"run_lock_enabled": True,
                          "run_lock_dir": str(shared)})
        self.assertEqual(run_lock.RunLock(cfg)._dir, shared)

    def test_explicit_missing_dir_disables_lock(self):
        cfg = StubConfig(
            {"run_lock_enabled": True,
             "run_lock_dir": str(self.tmp / "does-not-exist")},
        )
        lock = run_lock.RunLock(cfg)
        self.assertFalse(lock.active)
        self.assertTrue(lock.acquire())

    def test_disabled_by_config(self):
        shared = self._mk("nas")
        cfg = StubConfig({"run_lock_enabled": False,
                          "run_lock_dir": str(shared)})
        lock = run_lock.RunLock(cfg)
        self.assertFalse(lock.active)
        self.assertTrue(lock.acquire())


class AcquireTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="run_lock_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.shared = self.tmp / "shared"
        self.shared.mkdir()

    def _lock(self, raw=None):
        cfg = {"run_lock_enabled": True, "run_lock_dir": str(self.shared)}
        cfg.update(raw or {})
        return run_lock.RunLock(StubConfig(cfg))

    def test_acquire_creates_marker_second_machine_backs_out(self):
        a = self._lock()
        b = self._lock()
        self.assertTrue(a.acquire())
        self.assertFalse(b.acquire())
        marker = self.shared / run_lock.RUN_LOCK_NAME
        self.assertIn("host", marker.read_text())
        a.release()

    def test_backed_out_release_does_not_delete_foreign_lock(self):
        a = self._lock()
        b = self._lock()
        self.assertTrue(a.acquire())
        self.assertFalse(b.acquire())
        b.release()  # must be a no-op — b never held it
        self.assertTrue(
            (self.shared / run_lock.RUN_LOCK_NAME).exists()
        )
        a.release()

    def test_release_allows_reacquire(self):
        lock = self._lock()
        self.assertTrue(lock.acquire())
        lock.release()
        self.assertFalse(
            (self.shared / run_lock.RUN_LOCK_NAME).exists()
        )
        self.assertTrue(lock.acquire())
        lock.release()

    def test_stale_lock_is_taken_over(self):
        lock = self._lock()  # default staleness: 15 minutes
        marker = self.shared / run_lock.RUN_LOCK_NAME
        marker.write_text('{"host": "crashed-host"}')
        old = time.time() - 16 * 60
        os.utime(marker, (old, old))
        self.assertTrue(lock.acquire())
        lock.release()

    def test_fresh_foreign_lock_is_respected(self):
        marker = self.shared / run_lock.RUN_LOCK_NAME
        marker.write_text('{"host": "other-host"}')
        self.assertFalse(self._lock().acquire())

    def test_staleness_off_never_takes_over(self):
        marker = self.shared / run_lock.RUN_LOCK_NAME
        marker.write_text('{"host": "crashed-host"}')
        old = time.time() - 100 * 60
        os.utime(marker, (old, old))
        self.assertFalse(
            self._lock({"run_lock_stale_minutes": 0}).acquire()
        )

    def test_heartbeat_keeps_mtime_fresh(self):
        with mock.patch.object(run_lock, "HEARTBEAT_SECONDS", 0.05):
            lock = self._lock()
            self.assertTrue(lock.acquire())
            marker = self.shared / run_lock.RUN_LOCK_NAME
            first = marker.stat().st_mtime
            time.sleep(0.25)
            self.assertGreater(marker.stat().st_mtime, first)
            lock.release()

    def test_heartbeat_stops_on_release(self):
        with mock.patch.object(run_lock, "HEARTBEAT_SECONDS", 0.05):
            lock = self._lock()
            self.assertTrue(lock.acquire())
            thread = lock._heartbeat_thread
            lock.release()
            self.assertFalse(thread.is_alive())

    def test_run_lock_name_is_in_exclude_defaults(self):
        self.assertIn(".file_organizer.run.lock",
                      DEFAULTS["exclude_patterns"])


if __name__ == "__main__":
    unittest.main()

"""Tests for the cross-device sync lock in sync_engine.py.

Two machines syncing the same cloud/external pair at once is the classic
ProtonDrive/GoogleDrive/external-disk clash. The lock marker
(.file_organizer.sync.lock) lives on the SHARED side of the pair; the
second machine sees it, skips the pair this cycle, and picks it up later
— whoever started first finishes first. Stale locks (crashed runs) are
taken over after sync_lock_stale_minutes.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from file_organizer import sync_engine  # noqa: E402
from file_organizer.config import DEFAULTS  # noqa: E402


class StubConfig:
    def __init__(self, raw_config=None):
        self.raw_config = raw_config or {}


class SharedLockDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sync_lock_test_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def _engine(self):
        return sync_engine.SyncEngine(StubConfig())

    def _home_patch(self, home: Path):
        return mock.patch.object(
            Path, "home", classmethod(lambda cls: home)
        )

    def test_cloud_folder_is_the_shared_side(self):
        local = self.tmp / "local" / "pol"
        cloud = self.tmp / "Library" / "CloudStorage" / "Proton" / "pol"
        local.mkdir(parents=True)
        cloud.mkdir(parents=True)
        home = self.tmp / "home"
        with self._home_patch(home):
            self.assertEqual(
                self._engine()._shared_lock_dir(local, cloud), cloud
            )

    def test_external_volume_is_the_shared_side(self):
        local = self.tmp / "home" / "pol"
        external = self.tmp / "Volumes" / "Backup" / "pol"
        local.mkdir(parents=True)
        external.mkdir(parents=True)
        with self._home_patch(self.tmp / "home"):
            self.assertEqual(
                self._engine()._shared_lock_dir(local, external), external
            )

    def test_local_only_pair_gets_no_lock(self):
        home = self.tmp / "home"
        a = home / "pol"
        b = home / "pictures"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        with self._home_patch(home):
            self.assertIsNone(self._engine()._shared_lock_dir(a, b))

    def test_missing_folders_never_become_lock_dirs(self):
        home = self.tmp / "home"
        a = home / "pol"
        a.mkdir(parents=True)
        unmounted = self.tmp / "Volumes" / "NotThere" / "pol"
        with self._home_patch(home):
            # The unmounted /Volumes path must NOT be picked (and the lock
            # logic must never create it).
            self.assertIsNone(
                self._engine()._shared_lock_dir(a, unmounted)
            )
            self.assertFalse(unmounted.exists())


class LockAcquireTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sync_lock_test_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.shared = self.tmp / "shared"
        self.shared.mkdir()

    def _engine(self, raw=None):
        return sync_engine.SyncEngine(StubConfig(raw))

    def test_acquire_creates_lock_and_second_acquire_fails(self):
        engine = self._engine()
        self.assertTrue(engine._acquire_lock(self.shared))
        lock = self.shared / sync_engine.SyncEngine.SYNC_LOCK_NAME
        self.assertTrue(lock.exists())
        self.assertIn("host", lock.read_text())
        # Another machine's engine now loses the race.
        self.assertFalse(engine._acquire_lock(self.shared))

    def test_release_allows_reacquire(self):
        engine = self._engine()
        self.assertTrue(engine._acquire_lock(self.shared))
        engine._release_lock(self.shared)
        self.assertFalse(
            (self.shared / sync_engine.SyncEngine.SYNC_LOCK_NAME).exists()
        )
        self.assertTrue(engine._acquire_lock(self.shared))

    def test_stale_lock_is_taken_over(self):
        engine = self._engine()  # default staleness: 120 minutes
        lock = self.shared / sync_engine.SyncEngine.SYNC_LOCK_NAME
        lock.write_text('{"host": "crashed-host"}')
        old = __import__("time").time() - 121 * 60
        os.utime(lock, (old, old))
        self.assertTrue(engine._acquire_lock(self.shared))

    def test_fresh_lock_is_respected(self):
        engine = self._engine()
        lock = self.shared / sync_engine.SyncEngine.SYNC_LOCK_NAME
        lock.write_text('{"host": "other-host"}')
        self.assertFalse(engine._acquire_lock(self.shared))

    def test_lock_enabled_by_default_disablable_by_config(self):
        self.assertTrue(self._engine()._lock_enabled)
        self.assertFalse(
            self._engine({"sync_lock_enabled": False})._lock_enabled
        )

    def test_lock_name_is_in_rsync_exclude_defaults(self):
        self.assertIn(".file_organizer.sync.lock", DEFAULTS["exclude_patterns"])


if __name__ == "__main__":
    unittest.main()

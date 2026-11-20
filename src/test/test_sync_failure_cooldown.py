"""Tests for the failed-cloud-sync memory in sync_engine.py.

When an rsync run involving a cloud folder fails (e.g. a Proton Drive
placeholder that stalls on mmap), the engine remembers the exact
source→target unit and skips it on later runs for
sync_failure_cooldown_minutes instead of retrying the same stuck files
every cycle. Local pairs are never remembered, a success clears the
memory, and the cooldown expires so the file is retried later.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from file_organizer import sync_engine  # noqa: E402


class StubConfig:
    def __init__(self, raw_config=None):
        self.raw_config = raw_config or {}


class CloudMarkerTests(unittest.TestCase):
    def test_cloud_paths_detected(self):
        engine = sync_engine.SyncEngine(StubConfig())
        self.assertTrue(engine._is_cloud_path(
            Path("/Users/x/Library/CloudStorage/ProtonDrive-x/pol")))
        self.assertTrue(engine._is_cloud_path(Path("/Users/x/GoogleDrive/x")))
        self.assertFalse(engine._is_cloud_path(Path("/tmp/pol")))
        self.assertFalse(engine._is_cloud_path(Path("/Volumes/Backup/pol")))


class FailureMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sync_fail_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state = self.tmp / "failures.json"

    def _engine(self, raw=None):
        cfg = {
            "sync_lock_enabled": False,
            "sync_quick_check": False,
            "sync_failure_state_path": str(self.state),
        }
        cfg.update(raw or {})
        return sync_engine.SyncEngine(StubConfig(cfg))

    @staticmethod
    def _fail(stderr=b"mmap: Operation timed out"):
        return SimpleNamespace(returncode=20, stdout=b"", stderr=stderr)

    @staticmethod
    def _ok():
        return SimpleNamespace(returncode=0, stdout=b"")

    def _cloud_pair(self):
        cloud = self.tmp / "Library" / "CloudStorage" / "Proton" / "pol"
        local = self.tmp / "pol"
        cloud.mkdir(parents=True)
        local.mkdir(parents=True)
        return cloud, local

    def test_cloud_failure_is_remembered_and_skipped(self):
        cloud, local = self._cloud_pair()
        engine = self._engine()
        with mock.patch.object(sync_engine.subprocess, "run",
                               return_value=self._fail()):
            self.assertFalse(engine._run_rsync(cloud, local))
        # Second attempt within the cooldown: skipped, rsync never runs.
        with mock.patch.object(sync_engine.subprocess, "run") as run:
            self.assertTrue(engine._run_rsync(cloud, local))
        run.assert_not_called()
        data = json.loads(self.state.read_text())
        self.assertIn(f"{cloud}|{local}", data)

    def test_local_failure_is_not_remembered(self):
        a = self.tmp / "a"
        b = self.tmp / "b"
        a.mkdir()
        b.mkdir()
        engine = self._engine()
        with mock.patch.object(sync_engine.subprocess, "run",
                               return_value=self._fail(b"boom")):
            self.assertFalse(engine._run_rsync(a, b))
        with mock.patch.object(sync_engine.subprocess, "run",
                               return_value=self._ok()) as run:
            self.assertTrue(engine._run_rsync(a, b))
        self.assertEqual(run.call_count, 1)

    def test_success_clears_failure(self):
        cloud, local = self._cloud_pair()
        engine = self._engine()
        with mock.patch.object(sync_engine.subprocess, "run",
                               return_value=self._fail()):
            self.assertFalse(engine._run_rsync(cloud, local))
        self.assertIn(f"{cloud}|{local}",
                      json.loads(self.state.read_text()))
        # Rewind the recorded failure so the next attempt really runs.
        data = json.loads(self.state.read_text())
        key = f"{cloud}|{local}"
        data[key]["failed_at"] = data[key]["failed_at"] - 360 * 60 - 1
        self.state.write_text(json.dumps(data))
        engine._failures = engine._load_failures()
        with mock.patch.object(sync_engine.subprocess, "run",
                               return_value=self._ok()) as run:
            self.assertTrue(engine._run_rsync(cloud, local))
        self.assertEqual(run.call_count, 1)
        self.assertNotIn(f"{cloud}|{local}",
                         json.loads(self.state.read_text()))
        # And the next attempt really runs again.
        with mock.patch.object(sync_engine.subprocess, "run",
                               return_value=self._ok()) as run:
            self.assertTrue(engine._run_rsync(cloud, local))
        self.assertEqual(run.call_count, 1)

    def test_cooldown_expires_and_retries(self):
        cloud, local = self._cloud_pair()
        engine = self._engine()
        with mock.patch.object(sync_engine.subprocess, "run",
                               return_value=self._fail()):
            self.assertFalse(engine._run_rsync(cloud, local))
        # Rewind the recorded failure to before the cooldown.
        data = json.loads(self.state.read_text())
        key = f"{cloud}|{local}"
        data[key]["failed_at"] = data[key]["failed_at"] - 360 * 60 - 1
        self.state.write_text(json.dumps(data))
        engine._failures = engine._load_failures()
        with mock.patch.object(sync_engine.subprocess, "run",
                               return_value=self._ok()) as run:
            self.assertTrue(engine._run_rsync(cloud, local))
        run.assert_called_once()

    def test_zero_cooldown_disables_remembering(self):
        cloud, local = self._cloud_pair()
        engine = self._engine({"sync_failure_cooldown_minutes": 0})
        with mock.patch.object(sync_engine.subprocess, "run",
                               return_value=self._fail()):
            self.assertFalse(engine._run_rsync(cloud, local))
        self.assertFalse(self.state.exists())
        with mock.patch.object(sync_engine.subprocess, "run",
                               return_value=self._ok()) as run:
            self.assertTrue(engine._run_rsync(cloud, local))
        run.assert_called_once()

    def test_corrupt_state_file_is_ignored(self):
        cloud, local = self._cloud_pair()
        self.state.write_text("{not valid json")
        engine = self._engine()
        self.assertEqual(engine._failures, {})
        with mock.patch.object(sync_engine.subprocess, "run",
                               return_value=self._ok()) as run:
            self.assertTrue(engine._run_rsync(cloud, local))
        run.assert_called_once()

    def test_chunked_pair_skips_only_the_failed_chunk_next_cycle(self):
        cloud = self.tmp / "Library" / "CloudStorage" / "Proton" / "pol"
        local = self.tmp / "pol"
        for name in ("artist_a", "bad_artist", "artist_z"):
            (cloud / name).mkdir(parents=True)
            (local / name).mkdir(parents=True)
        engine = self._engine({
            "sync_chunk_subfolders": 2,
            "sync_pairs": [{"folders": [str(cloud), str(local)]}],
        })

        def fake_run(*args, **kwargs):
            cmd = args[0]
            if "bad_artist" in cmd[-2]:
                return self._fail()
            return self._ok()

        with mock.patch.object(sync_engine.subprocess, "run",
                               side_effect=fake_run) as run:
            summary = engine.run_all_sync_pairs()
        # Bidirectional: 3 chunks + loose pass in EACH direction = 8 runs.
        self.assertEqual(run.call_count, 8)
        self.assertFalse(summary["bidirectional"][0]["result"]["a_to_b"])
        state = json.loads(self.state.read_text())
        # The bad chunk failed in both directions → remembered twice.
        bad_keys = [k for k in state if "bad_artist" in k]
        self.assertEqual(len(bad_keys), 2)

        # Next cycle: the failed chunks are skipped, everything else runs.
        with mock.patch.object(sync_engine.subprocess, "run",
                               side_effect=fake_run) as run:
            summary = engine.run_all_sync_pairs()
        self.assertEqual(run.call_count, 6)  # (2 good chunks + loose) x 2
        self.assertTrue(summary["bidirectional"][0]["result"]["a_to_b"])


if __name__ == "__main__":
    unittest.main()

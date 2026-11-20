"""Tests for the sync quick check in sync_engine.py.

Before each rsync direction the engine compares the two trees with a
fast Python walk (size + mode + mtime_sec for files, target + mtime for
symlinks, presence for dirs) and SKIPS the direction when the target
already has everything from the source. On cloud mounts this avoids the
rsync scan entirely for in-sync pairs — rsync (especially openrsync)
walks/mmap-reads files, which is what makes unchanged Proton pairs take
minutes-to-hours. The quick check never changes what rsync does; it only
skips work, and it declines to decide (falls back to rsync) whenever the
exclude patterns or the tree can't be matched exactly.
"""

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


def _write(path, content=b"data", mtime=1700000000, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.chmod(str(path), mode)
    os.utime(str(path), (mtime, mtime))


class ExcludeMatcherTests(unittest.TestCase):
    def test_basename_globs_match_any_depth(self):
        m = sync_engine._ExcludeMatcher([".git", "*.pyc"])
        self.assertTrue(m.valid)
        self.assertTrue(m.matches(".git", is_dir=True))
        self.assertTrue(m.matches("a/b/.git", is_dir=True))
        self.assertTrue(m.matches("a/x.pyc", is_dir=False))
        self.assertFalse(m.matches("a/keep.txt", is_dir=False))

    def test_matching_is_case_sensitive_like_rsync(self):
        m = sync_engine._ExcludeMatcher(["Build"])
        self.assertFalse(m.matches("build", is_dir=True))

    def test_anchored_root_dir_pattern(self):
        m = sync_engine._ExcludeMatcher(["/vendor/"])
        self.assertTrue(m.matches("vendor", is_dir=True))
        self.assertFalse(m.matches("a/vendor", is_dir=True))
        # "/name/" excludes directories only, never files
        self.assertFalse(m.matches("vendor", is_dir=False))

    def test_slash_pattern_matches_path_suffix(self):
        m = sync_engine._ExcludeMatcher(["priv/static"])
        self.assertTrue(m.matches("priv/static", is_dir=True))
        self.assertTrue(m.matches("x/priv/static", is_dir=True))
        self.assertFalse(m.matches("priv", is_dir=True))
        self.assertFalse(m.matches("priv/other", is_dir=True))

    def test_double_star_prefix_is_basename(self):
        m = sync_engine._ExcludeMatcher(["**/.git"])
        self.assertTrue(m.matches(".git", is_dir=True))
        self.assertTrue(m.matches("a/b/.git", is_dir=True))

    def test_unsupported_shapes_invalidate(self):
        self.assertFalse(sync_engine._ExcludeMatcher(["foo/"]).valid)
        self.assertFalse(sync_engine._ExcludeMatcher(["/foo.txt"]).valid)
        self.assertFalse(sync_engine._ExcludeMatcher(["a/**/b"]).valid)
        self.assertTrue(sync_engine._ExcludeMatcher([""]).valid)


class WalkIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="quick_check_walk_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _engine(self, raw=None):
        cfg = {"exclude_patterns": [".git", "*.pyc"]}
        cfg.update(raw or {})
        return sync_engine.SyncEngine(StubConfig(cfg))

    def test_index_files_and_dirs(self):
        root = self.tmp / "a"
        _write(root / "f.txt", mtime=100)
        _write(root / "sub" / "g.txt", mtime=101)
        (root / "empty").mkdir(parents=True)
        idx = self._engine()._walk_index(root)
        self.assertEqual(idx["f.txt"], ("f", 0o644, 4, 100))
        self.assertEqual(idx["sub/g.txt"], ("f", 0o644, 4, 101))
        self.assertEqual(idx["sub"], ("d",))
        self.assertEqual(idx["empty"], ("d",))

    def test_index_applies_excludes(self):
        root = self.tmp / "a"
        _write(root / ".git" / "x.txt")
        _write(root / "n.pyc")
        _write(root / "keep.txt")
        _write(root / "sub" / "y.pyc")
        idx = self._engine()._walk_index(root)
        self.assertNotIn(".git", idx)
        self.assertNotIn(".git/x.txt", idx)
        self.assertNotIn("n.pyc", idx)
        self.assertNotIn("sub/y.pyc", idx)
        self.assertEqual(idx["keep.txt"][0], "f")
        self.assertEqual(idx["sub"], ("d",))  # sub itself is not excluded

    def test_sync_exclude_folder_names_are_root_only(self):
        root = self.tmp / "a"
        _write(root / "vendor" / "x.txt")
        _write(root / "sub" / "vendor" / "y.txt")
        idx = self._engine(
            {"sync_exclude_folder_names": ["vendor"]}
        )._walk_index(root)
        self.assertNotIn("vendor", idx)
        self.assertEqual(idx["sub/vendor"], ("d",))  # nested vendor kept

    def test_index_symlinks(self):
        root = self.tmp / "a"
        root.mkdir(parents=True)
        _write(self.tmp / "target.txt", mtime=100)
        os.symlink(str(self.tmp / "target.txt"), str(root / "link"))
        os.utime(str(root / "link"), (100, 100), follow_symlinks=False)
        (root / "realdir").mkdir(parents=True)
        _write(root / "realdir" / "inside.txt")
        os.symlink(str(root / "realdir"), str(root / "dirlink"))
        idx = self._engine()._walk_index(root)
        self.assertEqual(idx["link"], ("L", str(self.tmp / "target.txt"), 100))
        self.assertEqual(idx["dirlink"][0], "L")
        self.assertNotIn("dirlink/inside.txt", idx)  # not descended into
        self.assertEqual(idx["realdir/inside.txt"][0], "f")  # real dir kept

    def test_index_special_files_ignored(self):
        root = self.tmp / "a"
        root.mkdir(parents=True)
        os.mkfifo(str(root / "fifo"))
        idx = self._engine()._walk_index(root)
        self.assertNotIn("fifo", idx)

    def test_index_missing_root_returns_none(self):
        self.assertIsNone(self._engine()._walk_index(self.tmp / "nope"))

    def test_index_invalid_patterns_return_none(self):
        root = self.tmp / "a"
        _write(root / "f.txt")
        idx = self._engine({"exclude_patterns": ["foo/"]})._walk_index(root)
        self.assertIsNone(idx)


class NeedsSyncTests(unittest.TestCase):
    def test_needs_sync(self):
        ns = sync_engine.SyncEngine._needs_sync
        base = {"a": ("f", 0o644, 4, 100)}
        self.assertFalse(ns(base, dict(base)))
        self.assertFalse(ns({}, {}))
        self.assertTrue(ns(base, {}))
        self.assertTrue(ns(base, {"a": ("f", 0o644, 4, 101)}))
        self.assertTrue(ns(base, {"a": ("f", 0o600, 4, 100)}))
        self.assertTrue(ns({"d": ("d",)}, {}))
        # A target superset is fine — one-way pairs never delete.
        self.assertFalse(ns(base, {**base, "extra": ("f", 0o644, 4, 100)}))


class RunAllPairsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="quick_check_run_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _engine(self, raw):
        cfg = dict(raw)
        cfg.setdefault("sync_lock_enabled", False)
        return sync_engine.SyncEngine(StubConfig(cfg))

    def _fake_rsync(self):
        return mock.patch.object(
            sync_engine.subprocess, "run",
            return_value=SimpleNamespace(returncode=0, stdout=b""),
        )

    def test_identical_bidirectional_pair_is_skipped(self):
        a = self.tmp / "a"
        b = self.tmp / "b"
        _write(a / "f.txt", mtime=100)
        _write(b / "f.txt", mtime=100)
        engine = self._engine({"sync_pairs": [{"folders": [str(a), str(b)]}]})
        with self._fake_rsync() as run:
            summary = engine.run_all_sync_pairs()
        run.assert_not_called()
        self.assertTrue(summary["bidirectional"][0]["skipped"])

    def test_new_file_in_a_runs_only_a_to_b(self):
        a = self.tmp / "a"
        b = self.tmp / "b"
        _write(a / "f.txt", mtime=100)
        _write(b / "f.txt", mtime=100)
        _write(a / "new.txt", mtime=101)
        engine = self._engine({"sync_pairs": [{"folders": [str(a), str(b)]}]})
        with self._fake_rsync() as run:
            summary = engine.run_all_sync_pairs()
        self.assertEqual(run.call_count, 1)
        args = run.call_args[0][0]
        self.assertEqual(args[-2], str(a) + "/")
        self.assertEqual(args[-1], str(b))
        self.assertNotIn("skipped", summary["bidirectional"][0])

    def test_new_file_in_b_runs_only_b_to_a(self):
        a = self.tmp / "a"
        b = self.tmp / "b"
        _write(a / "f.txt", mtime=100)
        _write(b / "f.txt", mtime=100)
        _write(b / "new.txt", mtime=101)
        engine = self._engine({"sync_pairs": [{"folders": [str(a), str(b)]}]})
        with self._fake_rsync() as run:
            engine.run_all_sync_pairs()
        self.assertEqual(run.call_count, 1)
        args = run.call_args[0][0]
        self.assertEqual(args[-2], str(b) + "/")
        self.assertEqual(args[-1], str(a))

    def test_changes_on_both_sides_run_both_directions(self):
        a = self.tmp / "a"
        b = self.tmp / "b"
        _write(a / "only_a.txt")
        _write(b / "only_b.txt")
        engine = self._engine({"sync_pairs": [{"folders": [str(a), str(b)]}]})
        with self._fake_rsync() as run:
            engine.run_all_sync_pairs()
        self.assertEqual(run.call_count, 2)

    def test_quick_check_can_be_disabled(self):
        a = self.tmp / "a"
        b = self.tmp / "b"
        _write(a / "f.txt", mtime=100)
        _write(b / "f.txt", mtime=100)
        engine = self._engine({
            "sync_quick_check": False,
            "sync_pairs": [{"folders": [str(a), str(b)]}],
        })
        with self._fake_rsync() as run:
            engine.run_all_sync_pairs()
        self.assertEqual(run.call_count, 2)

    def test_excluded_diff_does_not_trigger_sync(self):
        a = self.tmp / "a"
        b = self.tmp / "b"
        _write(a / "f.txt", mtime=100)
        _write(b / "f.txt", mtime=100)
        _write(a / ".git" / "config")  # only on a, but excluded
        engine = self._engine({
            "exclude_patterns": [".git"],
            "sync_pairs": [{"folders": [str(a), str(b)]}],
        })
        with self._fake_rsync() as run:
            summary = engine.run_all_sync_pairs()
        run.assert_not_called()
        self.assertTrue(summary["bidirectional"][0]["skipped"])

    def test_one_way_skips_when_target_has_everything(self):
        src = self.tmp / "src"
        tgt = self.tmp / "tgt"
        _write(src / "f.txt", mtime=100)
        _write(tgt / "f.txt", mtime=100)
        _write(tgt / "extra.txt")  # target superset is fine (no delete)
        engine = self._engine({
            "one_way_pairs": [{"folders": [str(src), str(tgt)]}],
        })
        with self._fake_rsync() as run:
            summary = engine.run_all_sync_pairs()
        run.assert_not_called()
        self.assertTrue(summary["one_way"][0]["skipped"])

    def test_one_way_runs_when_source_is_newer(self):
        src = self.tmp / "src"
        tgt = self.tmp / "tgt"
        _write(src / "f.txt", mtime=101)
        _write(tgt / "f.txt", mtime=100)
        engine = self._engine({
            "one_way_pairs": [{"folders": [str(src), str(tgt)]}],
        })
        with self._fake_rsync() as run:
            summary = engine.run_all_sync_pairs()
        self.assertEqual(run.call_count, 1)
        args = run.call_args[0][0]
        self.assertEqual(args[-2], str(src) + "/")
        self.assertEqual(args[-1], str(tgt))
        self.assertNotIn("skipped", summary["one_way"][0])

    def test_one_way_runs_when_target_is_empty(self):
        src = self.tmp / "src"
        tgt = self.tmp / "tgt"
        _write(src / "f.txt")
        tgt.mkdir(parents=True)
        engine = self._engine({
            "one_way_pairs": [{"folders": [str(src), str(tgt)]}],
        })
        with self._fake_rsync() as run:
            engine.run_all_sync_pairs()
        self.assertEqual(run.call_count, 1)


class RsyncBinaryTests(unittest.TestCase):
    def _engine(self, raw=None):
        return sync_engine.SyncEngine(StubConfig(raw or {}))

    def test_args_use_W_and_selected_binary(self):
        engine = self._engine()
        args = engine._build_rsync_args(Path("/a"), Path("/b"))
        self.assertEqual(args[0], "rsync")
        self.assertIn("-W", args)
        self.assertNotIn("--whole-file", args)

    def test_rsync_binary_override_is_used(self):
        engine = self._engine({"rsync_binary": "/custom/rsync"})
        self.assertEqual(engine._rsync_bin, "/custom/rsync")
        args = engine._build_rsync_args(Path("/a"), Path("/b"))
        self.assertEqual(args[0], "/custom/rsync")


if __name__ == "__main__":
    unittest.main()

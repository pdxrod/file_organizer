"""Tests for the consolidated exclusion lists (item 3) on the phone side.

Covers:
- Exact folder-name exclusion in scan_sources (build vs mybuildings).
- Nested path patterns like priv/static and tmp/cache.
- Extension exclusions (.crypt14 etc.).
- Built-in safety defaults merged with (not replaced by) user config.
- proton_drive_paths config key driving find_proton_drive.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import phone_sync  # noqa: E402

import logging  # noqa: E402
logging.getLogger("phone_sync").addHandler(logging.NullHandler())


class ScanExclusionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        # Old files so min_age filtering does not interfere.
        self.old = time.time() - 7200

    def _write(self, rel: str) -> Path:
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
        os.utime(p, (self.old, self.old))
        return p

    def _scan(self, exclude_folder_names=None, exclude_extensions=None):
        return [
            str(p.relative_to(self.dir))
            for p, _ in phone_sync.scan_sources(
                [str(self.dir)],
                include_extensions=[],
                exclude_extensions=(
                    exclude_extensions
                    if exclude_extensions is not None
                    else list(phone_sync.DEFAULT_EXCLUDE_EXTENSIONS)
                ),
                exclude_patterns=list(phone_sync.DEFAULT_EXCLUDE_PATTERNS),
                exclude_folder_patterns=[],
                exclude_folder_names=(
                    exclude_folder_names
                    if exclude_folder_names is not None
                    else list(phone_sync.DEFAULT_EXCLUDE_FOLDER_NAMES)
                ),
                max_file_size_mb=500,
                min_age_seconds=0,
            )
        ]

    def test_exact_folder_names_excluded_but_lookalikes_kept(self):
        self._write("build/out.bin")
        self._write("mybuildings/photo.jpg")
        self._write(".git/config")
        found = self._scan()
        self.assertNotIn("build/out.bin", found)
        self.assertIn("mybuildings/photo.jpg", found)
        self.assertNotIn(".git/config", found)

    def test_nested_path_patterns_priv_static_and_tmp_cache(self):
        self._write("priv/static/app.js")
        self._write("priv/notes/note.txt")
        self._write("tmp/cache/blob.bin")
        self._write("tmp/pics/pic.jpg")
        found = self._scan()
        self.assertNotIn("priv/static/app.js", found)
        self.assertIn("priv/notes/note.txt", found)
        self.assertNotIn("tmp/cache/blob.bin", found)
        self.assertIn("tmp/pics/pic.jpg", found)

    def test_excluded_extensions(self):
        self._write("chat.crypt14")
        self._write("debug.log")
        self._write("keep.png")
        found = self._scan()
        self.assertNotIn("chat.crypt14", found)
        self.assertNotIn("debug.log", found)
        self.assertIn("keep.png", found)

    def test_build_cache_folders_excluded(self):
        for rel in [
            "node_modules/pkg/index.js",
            ".venv/bin/python",
            "__pycache__/mod.pyc",
            "dist/app.js",
            "Music/song.mp3",
        ]:
            self._write(rel)
        found = self._scan()
        for excluded in [
            "node_modules/pkg/index.js",
            ".venv/bin/python",
            "__pycache__/mod.pyc",
            "dist/app.js",
        ]:
            self.assertNotIn(excluded, found)
        self.assertIn("Music/song.mp3", found)

    def test_user_folder_names_are_added_to_defaults(self):
        self._write("family-pics/photo.jpg")
        found = self._scan(exclude_folder_names=["family-pics"])
        self.assertNotIn("family-pics/photo.jpg", found)

    def test_user_extensions_are_added_to_defaults(self):
        # The merge happens in PhoneSync.scan_and_sync via _merged_list:
        # even with a custom list, the built-in .crypt14 must still apply.
        merged = phone_sync._merged_list(
            phone_sync.DEFAULT_EXCLUDE_EXTENSIONS, [".custom"]
        )
        self.assertIn(".crypt14", merged)
        self.assertIn(".custom", merged)
        self._write("chat.crypt14")
        self._write("thing.custom")
        found = self._scan(exclude_extensions=merged)
        self.assertNotIn("chat.crypt14", found)
        self.assertNotIn("thing.custom", found)


class ProtonCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()

    def test_find_proton_drive_uses_given_candidates(self):
        (self.dir / "MyDrive").mkdir()
        found = phone_sync.find_proton_drive([str(self.dir / "MyDrive")])
        self.assertEqual(found, [self.dir / "MyDrive"])

    def test_config_proton_drive_paths_drives_finder(self):
        config = self.dir / "phone_sync_config.yaml"
        config.write_text(
            "proton_drive_paths:\n"
            f"  - \"{self.dir}/MyDrive\"\n"
        )
        cfg = phone_sync.load_config(str(config))
        candidates = cfg.get("proton_drive_paths") or \
            phone_sync.DEFAULT_PROTON_CANDIDATES
        self.assertEqual(candidates, [f"{self.dir}/MyDrive"])


class PhoneSyncMergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()

    def test_daemon_merges_safety_defaults_with_config(self):
        config = self.dir / "phone_sync_config.yaml"
        config.write_text(
            "target_directory: /tmp/staging\n"
            "source_directories: []\n"
            f"db_path: \"{self.dir}/db.sqlite\"\n"
            "exclude_folder_names:\n"
            "  - \"extra-folder\"\n"
            "exclude_extensions:\n"
            "  - \".custom\"\n"
        )
        daemon = phone_sync.PhoneSync(str(config))
        try:
            names = daemon.exclude_folder_names
            self.assertIn(".git", names)
            self.assertIn("node_modules", names)
            self.assertIn("extra-folder", names)
        finally:
            daemon.db.close()

    def test_merged_list_orders_and_dedupes(self):
        out = phone_sync._merged_list(["a", "b"], ["b", "c"], None)
        self.assertEqual(out, ["a", "b", "c"])


class PhonePlaceholderTests(unittest.TestCase):
    """Unedited template placeholders must stop the daemon with instructions
    (item 7)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()

    def _daemon(self, extra: str):
        config = self.dir / "phone_sync_config.yaml"
        config.write_text(
            f"db_path: \"{self.dir}/db.sqlite\"\n" + extra
        )
        return phone_sync.PhoneSync(str(config))

    def test_janedoe_target_directory_exits_with_message(self):
        with self.assertRaises(SystemExit):
            self._daemon(
                "target_directory: \"/Users/janedoe/misc\"\n"
                "source_directories: []\n"
            )

    def test_janedoe_source_directory_exits_with_message(self):
        with self.assertRaises(SystemExit):
            self._daemon(
                "target_directory: /tmp/staging\n"
                "source_directories:\n"
                "  - \"/Users/janedoe/Desktop\"\n"
            )

    def test_ordinary_missing_directories_are_allowed(self):
        # Real phones/macs have folders that come and go; only placeholder
        # paths are fatal.
        daemon = self._daemon(
            "target_directory: /tmp/staging\n"
            "source_directories:\n"
            "  - /storage/emulated/0/DCIM\n"
        )
        try:
            self.assertIn("/storage/emulated/0/DCIM", daemon.source_dirs)
        finally:
            daemon.db.close()


class IdleBackoffTests(unittest.TestCase):
    """Item 14: idle cycles must sleep progressively longer, capped, and any
    action must reset the streak."""

    def test_streak_zero_uses_base_interval(self):
        self.assertEqual(phone_sync.compute_idle_sleep(100, 2, 8, 0), 100)

    def test_doubles_per_idle_cycle(self):
        self.assertEqual(phone_sync.compute_idle_sleep(100, 2, 8, 1), 200)
        self.assertEqual(phone_sync.compute_idle_sleep(100, 2, 8, 2), 400)
        self.assertEqual(phone_sync.compute_idle_sleep(100, 2, 8, 3), 800)

    def test_capped_at_max_multiplier(self):
        self.assertEqual(phone_sync.compute_idle_sleep(100, 2, 8, 10), 800)
        self.assertEqual(phone_sync.compute_idle_sleep(300, 2, 8, 100), 2400)

    def test_factor_one_disables_backoff(self):
        self.assertEqual(phone_sync.compute_idle_sleep(100, 1, 8, 50), 100)

    def test_default_config_has_backoff_keys(self):
        cfg = phone_sync._default_config()
        self.assertIn("idle_backoff_factor", cfg)
        self.assertIn("idle_backoff_max_multiplier", cfg)


if __name__ == "__main__":
    unittest.main()

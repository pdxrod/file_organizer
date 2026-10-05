"""Tests for the consolidated exclusion lists (item 3) on the computer side.

Covers:
- The built-in safety defaults always being in force (union merge).
- SoftlinkHandler classification lists coming from config.
- SyncEngine rsync excludes coming from config (no hard-coded VCS list).
- FileScanner excluding folder names and file patterns from scans.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as cfgmod            # noqa: E402
import sync_engine                  # noqa: E402
import softlink_handler             # noqa: E402
from scanner import FileScanner     # noqa: E402


class ExclusionUnionTests(unittest.TestCase):
    """The safety defaults must never be removable by a user config."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _write_config(self, text: str) -> cfgmod.Config:
        path = self.dir / "config.yaml"
        path.write_text(text)
        return cfgmod.Config(str(path))

    def test_defaults_contain_the_full_exclusion_list(self):
        defaults = cfgmod.DEFAULTS["exclude_patterns"]
        for pat in [
            "*.crypt14", ".git", ".venv", "node_modules", "__pycache__",
            ".gradle", ".m2", "tmp/cache", "_build", "deps", "ebin",
            "dist", "build", "target", ".DS_Store", "*.pyc", "*.log",
            ".Spotlight-V100", ".TemporaryItems", ".fseventsd",
            ".DocumentRevisions-V100", ".bundle", "vendor", "bundle",
            "priv/static", ".env", "venv", "env",
        ]:
            self.assertIn(pat, defaults, f"missing default exclusion: {pat}")

    def test_user_patterns_add_to_defaults_instead_of_replacing(self):
        """An old config with its own exclude_patterns must not lose the
        built-in safety exclusions like .git."""
        cfg = self._write_config("exclude_patterns:\n  - \"*.zzz\"\n")
        patterns = cfg.raw_config["exclude_patterns"]
        self.assertIn("*.zzz", patterns)
        for pat in (".git", ".venv", "node_modules", "*.crypt14"):
            self.assertIn(pat, patterns)

    def test_union_does_not_duplicate(self):
        cfg = self._write_config(
            "exclude_patterns:\n  - \".git\"\n  - \"*.zzz\"\n"
        )
        patterns = cfg.raw_config["exclude_patterns"]
        self.assertEqual(patterns.count(".git"), 1)
        self.assertEqual(patterns.count("*.zzz"), 1)


class SoftlinkHandlerConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _config_with(self, text: str) -> cfgmod.Config:
        path = self.dir / "config.yaml"
        path.write_text(text)
        return cfgmod.Config(str(path))

    def test_falls_back_to_builtin_defaults(self):
        handler = softlink_handler.SoftlinkHandler(
            self._config_with("output_base: /tmp/x\n")
        )
        self.assertTrue(handler.should_never_softlink(".git"))
        self.assertTrue(handler.is_reproducible(".venv"))
        self.assertTrue(handler.is_build_cache("__pycache__"))

    def test_reads_classification_from_config(self):
        cfg = self._config_with(
            "output_base: /tmp/x\n"
            "softlink_never_patterns: [\".git\"]\n"
            "softlink_reproducible_patterns: [\".venv\"]\n"
            "softlink_build_cache_patterns: [\"__pycache__\"]\n"
        )
        handler = softlink_handler.SoftlinkHandler(cfg)
        self.assertTrue(handler.should_never_softlink(".git"))
        # Not in the config lists -> defaults do not leak in.
        self.assertFalse(handler.should_never_softlink(".svn"))
        self.assertFalse(handler.is_reproducible("node_modules"))
        self.assertFalse(handler.is_build_cache(".tox"))


class SyncEngineExclusionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_rsync_args_use_config_excludes_including_vcs(self):
        path = self.dir / "config.yaml"
        path.write_text("exclude_patterns:\n  - \"*.zzz\"\n")
        cfg = cfgmod.Config(str(path))
        engine = sync_engine.SyncEngine(cfg)
        args = engine._build_rsync_args(self.dir, self.dir / "target")
        # VCS folders arrive via the merged config defaults, not a hard-coded list.
        self.assertIn(".git", args)
        self.assertIn(".venv", args)
        self.assertIn("node_modules", args)
        self.assertIn("*.crypt14", args)
        self.assertIn("*.zzz", args)


class ScannerExclusionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _config(self) -> cfgmod.Config:
        path = self.dir / "config.yaml"
        path.write_text("output_base: /tmp/never-created\n")
        return cfgmod.Config(str(path))

    def test_excluded_folder_names_are_skipped_but_lookalikes_are_not(self):
        root = self.dir / "src"
        for d in ["build", "mybuildings", ".git", "priv/static", "Music"]:
            (root / d).mkdir(parents=True)
        (root / "build" / "out.bin").write_text("x")
        (root / "mybuildings" / "photo.jpg").write_text("x")
        (root / ".git" / "config").write_text("x")
        (root / "priv" / "static" / "app.js").write_text("x")
        (root / "Music" / "song.mp3").write_text("x")
        (root / "keep.png").write_text("x")
        (root / "note.crypt14").write_text("x")

        scanner = FileScanner(self._config())
        found = [
            str(p.relative_to(root))
            for p, _ in scanner.scan_directory(root, root)
        ]
        self.assertNotIn("build/out.bin", found)
        self.assertIn("mybuildings/photo.jpg", found)
        self.assertNotIn(".git/config", found)
        self.assertNotIn("priv/static/app.js", found)
        self.assertIn("Music/song.mp3", found)
        self.assertIn("keep.png", found)
        self.assertNotIn("note.crypt14", found)


class TemplatePlaceholderTests(unittest.TestCase):
    """A config copied from the template and never edited must fail fast
    with instructions instead of silently doing nothing (item 7)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()

    def _load(self, text: str):
        path = self.dir / "config.yaml"
        path.write_text(text)
        return cfgmod.Config(str(path))

    def test_missing_janedoe_drive_raises_with_instructions(self):
        with self.assertRaises(cfgmod.ConfigError) as cm:
            self._load(
                "output_base: /tmp/never-created\n"
                "drives:\n"
                "  MAIN_DRIVE: \"/Users/janedoe\"\n"
            )
        msg = str(cm.exception)
        self.assertIn("/Users/janedoe", msg)
        self.assertIn("Edit config.yaml", msg)

    def test_missing_placeholder_sync_pair_raises(self):
        with self.assertRaises(cfgmod.ConfigError):
            self._load(
                "output_base: /tmp/never-created\n"
                "sync_pairs:\n"
                "  - folders:\n"
                "      - \"/Users/janedoe/misc\"\n"
                "      - \"/Users/janedoe/other\"\n"
            )

    def test_real_missing_drive_only_warns(self):
        # A genuinely missing drive (unmounted volume) must NOT raise —
        # only template placeholders are fatal.
        cfg = self._load(
            "output_base: /tmp/never-created\n"
            "drives:\n"
            "  EXTERNAL_DRIVE: \"/Volumes/definitely-not-mounted-xyz\"\n"
        )
        self.assertEqual(
            cfg.drives["EXTERNAL_DRIVE"], "/Volumes/definitely-not-mounted-xyz"
        )

    def test_existing_placeholder_path_is_fine(self):
        # If a path contains 'janedoe' but DOES exist, it is a real path.
        real = self.dir / "janedoe"
        real.mkdir()
        cfg = self._load(
            "output_base: /tmp/never-created\n"
            "drives:\n"
            f"  MAIN_DRIVE: \"{real}\"\n"
        )
        self.assertEqual(cfg.drives["MAIN_DRIVE"], str(real))


if __name__ == "__main__":
    unittest.main()

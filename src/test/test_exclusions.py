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
from file_organizer import config as cfgmod     # noqa: E402
from file_organizer import sync_engine          # noqa: E402
from file_organizer import softlink_handler     # noqa: E402
from file_organizer.scanner import FileScanner  # noqa: E402


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

    def test_missing_janedoe_drive_reports_and_exits(self):
        # A missing drive is reported clearly and the program exits —
        # the config points somewhere that is not there.
        with self.assertRaises(cfgmod.ConfigError) as cm:
            self._load(
                "output_base: /tmp/never-created\n"
                "drives:\n"
                "  MAIN_DRIVE: \"/Users/janedoe\"\n"
            )
        msg = str(cm.exception)
        self.assertIn("MAIN_DRIVE", msg)
        self.assertIn("/Users/janedoe", msg)
        self.assertIn("do not exist", msg)

    def test_missing_placeholder_sync_pair_warns_not_raises(self):
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            cfg = self._load(
                "output_base: /tmp/never-created\n"
                "sync_pairs:\n"
                "  - folders:\n"
                "      - \"/Users/janedoe/misc\"\n"
                "      - \"/Users/janedoe/other\"\n"
            )
        self.assertIn("skipped", buf.getvalue())

    def test_real_missing_drive_reports_and_exits(self):
        # A genuinely missing drive (unmounted volume) also reports and
        # exits — the user must plug it in or fix the config.
        with self.assertRaises(cfgmod.ConfigError) as cm:
            self._load(
                "output_base: /tmp/never-created\n"
                "drives:\n"
                "  EXTERNAL_DRIVE: \"/Volumes/definitely-not-mounted-xyz\"\n"
            )
        self.assertIn("EXTERNAL_DRIVE", str(cm.exception))

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


class SectionedConfigTests(unittest.TestCase):
    """Since the single-template merge (item 5), config.yaml is generated
    from the computer: section of config_template.yaml — but legacy flat
    configs must keep working."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()

    def test_load_accepts_computer_section(self):
        path = self.dir / "config.yaml"
        path.write_text(
            "computer:\n"
            "  output_base: /tmp/never-created\n"
            "  exclude_patterns:\n"
            "    - \"*.zzz\"\n"
            "phone:\n"
            "  target_directory: /storage/emulated/0/staging\n"
        )
        cfg = cfgmod.Config(str(path))
        self.assertEqual(cfg.raw_config["output_base"], "/tmp/never-created")
        self.assertNotIn("phone", cfg.raw_config)
        self.assertNotIn("target_directory", cfg.raw_config)
        # Exclusion union still applies to the sectioned format.
        patterns = cfg.raw_config["exclude_patterns"]
        self.assertIn("*.zzz", patterns)
        self.assertIn(".git", patterns)

    def test_load_accepts_legacy_flat_config(self):
        path = self.dir / "config.yaml"
        path.write_text("output_base: /tmp/never-created\n")
        cfg = cfgmod.Config(str(path))
        self.assertEqual(cfg.raw_config["output_base"], "/tmp/never-created")

    def test_create_starter_writes_computer_section_only(self):
        import yaml as pyyaml
        path = self.dir / "config.yaml"
        cfgmod.Config.create_starter(str(path))
        starter = pyyaml.safe_load(path.read_text())
        self.assertIn("output_base", starter)
        self.assertNotIn("phone", starter)
        # create_starter turns the dangerous features off.
        self.assertFalse(starter["enable_folder_sync"])
        self.assertFalse(starter["auto_git"])
        self.assertFalse(starter["enable_duplicate_detection"])
        self.assertFalse(starter["ml_content_analysis"]["enabled"])


class IdleBackoffTests(unittest.TestCase):
    """Item 14: the computer daemon's backoff helper must match the phone
    daemon's exactly (they are intentionally duplicated)."""

    def test_cli_backoff_matches_phone_sync(self):
        from file_organizer import phone_sync
        from file_organizer.cli import compute_idle_sleep
        for streak in range(0, 10):
            self.assertEqual(
                compute_idle_sleep(300, 2, 8, streak),
                phone_sync.compute_idle_sleep(300, 2, 8, streak),
            )

    def test_defaults_include_backoff_keys(self):
        self.assertIn("idle_backoff_factor", cfgmod.DEFAULTS)
        self.assertIn("idle_backoff_max_multiplier", cfgmod.DEFAULTS)


class SyncChangeCountTests(unittest.TestCase):
    """rsync --itemize-changes output must be counted so idle cycles can be
    detected (item 14)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()

    def _engine(self):
        class _StubConfig:
            raw_config = {
                "exclude_patterns": [],
                "use_rsync": True,
                "rsync_disable_mmap": True,
            }
        return sync_engine.SyncEngine(_StubConfig())

    def test_count_itemized_lines(self):
        engine = self._engine()
        out = (
            b">f+++++++++ a.txt\n"
            b".d..t...... subdir/\n"
            b"sent 120 bytes  received 35 bytes\n"
        )
        self.assertEqual(engine._count_itemized_changes(out), 2)
        self.assertEqual(engine._count_itemized_changes(b"sent 120 bytes\n"), 0)
        self.assertEqual(engine._count_itemized_changes(None), 0)

    def test_real_rsync_first_run_counts_second_run_counts_nothing(self):
        import shutil
        if shutil.which("rsync") is None:
            self.skipTest("rsync not installed")
        src = self.dir / "src"
        dst = self.dir / "dst"
        src.mkdir()
        (src / "a.txt").write_text("hello")
        engine = self._engine()
        self.assertTrue(engine._run_rsync(src, dst))
        first = engine.last_change_count
        self.assertGreaterEqual(first, 1)
        before = engine.last_change_count
        self.assertTrue(engine._run_rsync(src, dst))
        self.assertEqual(engine.last_change_count - before, 0)

    # ── Item 11: cloud-placeholder sync fixes ──

    def _engine_with(self, raw):
        class _StubConfig:
            raw_config = raw
        return sync_engine.SyncEngine(_StubConfig())

    def test_whole_file_is_default(self):
        engine = self._engine_with({"exclude_patterns": []})
        args = engine._build_rsync_args(Path("/a"), Path("/b"))
        self.assertIn("--whole-file", args)
        self.assertNotIn("--no-whole-file", args)
        self.assertIn("--timeout=300", args)

    def test_legacy_rsync_disable_mmap_means_whole_file(self):
        # The old key used to add --no-whole-file (delta), which hung on
        # Proton placeholders. It must now mean whole-file.
        engine = self._engine_with({
            "exclude_patterns": [],
            "rsync_disable_mmap": True,
        })
        args = engine._build_rsync_args(Path("/a"), Path("/b"))
        self.assertIn("--whole-file", args)
        self.assertNotIn("--no-whole-file", args)

    def test_whole_file_can_be_disabled(self):
        engine = self._engine_with({
            "exclude_patterns": [],
            "rsync_whole_file": False,
        })
        args = engine._build_rsync_args(Path("/a"), Path("/b"))
        self.assertIn("--no-whole-file", args)

    def test_io_timeout_configurable(self):
        engine = self._engine_with({
            "exclude_patterns": [],
            "rsync_io_timeout_seconds": 0,
        })
        args = engine._build_rsync_args(Path("/a"), Path("/b"))
        self.assertFalse(any(a.startswith("--timeout=") for a in args))

    def test_many_subfolders_are_chunked_per_subfolder(self):
        from unittest import mock
        src = self.dir / "src"
        for name in ("one", "two", "three"):
            (src / name).mkdir(parents=True)
        (src / "loose.txt").write_text("x")
        engine = self._engine_with({
            "exclude_patterns": [],
            "sync_chunk_subfolders": 1,
        })
        calls = []
        with mock.patch.object(
            engine, "_run_rsync", side_effect=lambda s, t, delete=False, exclude_subdirs=None: calls.append((s, t, exclude_subdirs)) or True
        ):
            ok = engine._run_rsync_many(src, self.dir / "dst")
        self.assertTrue(ok)
        # one call per subfolder + one for the loose files
        self.assertEqual(len(calls), 4)
        subdir_calls = [c for c in calls if c[2] is None]
        self.assertEqual(len(subdir_calls), 3)
        loose = [c for c in calls if c[2] is not None][0]
        self.assertEqual(loose[0], src)
        self.assertEqual(sorted(loose[2]), ["one", "three", "two"])

    def test_few_subfolders_run_one_rsync(self):
        from unittest import mock
        src = self.dir / "src"
        (src / "one").mkdir(parents=True)
        engine = self._engine_with({
            "exclude_patterns": [],
            "sync_chunk_subfolders": 30,
        })
        calls = []
        with mock.patch.object(
            engine, "_run_rsync", side_effect=lambda s, t, delete=False, exclude_subdirs=None: calls.append(s) or True
        ):
            engine._run_rsync_many(src, self.dir / "dst")
        self.assertEqual(calls, [src])

    def test_defaults_exclude_proton_trash_markers(self):
        self.assertIn(".trashed*", cfgmod.DEFAULTS["exclude_patterns"])
        self.assertIn(".pending*", cfgmod.DEFAULTS["exclude_patterns"])


class FirstRunUxTests(unittest.TestCase):
    """Item 9: tabs must be caught with a plain message, and the first-run
    report must explain which paths will work."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()

    def test_tab_indentation_raises_clear_error(self):
        path = self.dir / "config.yaml"
        path.write_text("output_base: /tmp/never-created\n"
                        "drives:\n\t  MAIN_DRIVE: /tmp\n")
        with self.assertRaises(cfgmod.ConfigError) as cm:
            cfgmod.Config(str(path))
        msg = str(cm.exception)
        self.assertIn("TAB", msg)
        self.assertIn("SPACES", msg)

    def test_tabs_inside_values_are_allowed(self):
        # Only indentation tabs are fatal; tabs inside quoted strings are fine.
        path = self.dir / "config.yaml"
        path.write_text("output_base: /tmp/never-created\n"
                        "note: \"a\tb\"\n")
        cfg = cfgmod.Config(str(path))
        self.assertEqual(cfg.raw_config["note"], "a\tb")

    def test_first_run_report_flags_placeholders(self):
        import io
        from contextlib import redirect_stderr
        path = self.dir / "config.yaml"
        path.write_text(
            "computer:\n"
            "  drives:\n"
            "    MAIN_DRIVE: \"/Users/janedoe\"\n"
            f"    EXTERNAL_DRIVE: \"{self.dir}\"\n"
        )
        buf = io.StringIO()
        with redirect_stderr(buf):
            cfgmod.report_first_run(str(path))
        out = buf.getvalue()
        self.assertIn("Will it work?", out)
        self.assertIn("janedoe", out)
        self.assertIn("exists", out)   # the temp drive exists
        self.assertIn("TextEdit", out)
        self.assertIn("TAB", out)


if __name__ == "__main__":
    unittest.main()

"""Tests for the auto phone pull wiring in file_organizer.cli.

The computer's production auto cycle calls run_auto_phone_pull FIRST —
before the scan and organize work — so freshly pulled phone files are
organized and synced in the same cycle instead of the next one. These
tests cover the config gate and the ordering; the phone_pull.sh behavior
itself is covered by test_phone_pull.py.
"""

import logging
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from file_organizer import cli  # noqa: E402

logging.getLogger("file_organizer").addHandler(logging.NullHandler())


class FlagNormalizationTests(unittest.TestCase):
    """Flags are case-insensitive and dash-optional in cli.main."""

    OPTIONS = ("-R", "--REAL", "--scan-once", "--config", "-v", "--verbose")

    def test_canonical_forms(self):
        for token, expected in (
            ("--REAL", "--REAL"),
            ("real", "--REAL"),
            ("-Real", "--REAL"),
            ("-REAL", "--REAL"),
            ("REAL", "--REAL"),
            ("Scan-Once", "--scan-once"),
            ("-scan-once", "--scan-once"),
            ("V", "-v"),
            ("verbose", "--verbose"),
            ("config", "--config"),
        ):
            self.assertEqual(
                cli.normalize_flag_token(token, self.OPTIONS), expected,
                token,
            )

    def test_values_pass_through(self):
        self.assertEqual(
            cli.normalize_flag_token("my_config.yaml", self.OPTIONS),
            "my_config.yaml",
        )

    def test_argv_normalization(self):
        self.assertEqual(
            cli.normalize_argv(["-real", "Scan-Once", "config", "x.yaml"],
                               self.OPTIONS),
            ["--REAL", "--scan-once", "--config", "x.yaml"],
        )


class StubConfig:
    """Minimal Config stand-in exposing only raw_config."""

    def __init__(self, raw_config):
        self.raw_config = raw_config


class AutoPullGateTests(unittest.TestCase):
    def test_enabled_by_default(self):
        self.assertTrue(cli.auto_pull_enabled(StubConfig({})))
        self.assertTrue(
            cli.auto_pull_enabled(StubConfig({"phone_pull": {}}))
        )

    def test_disabled_when_auto_pull_false(self):
        cfg = StubConfig({"phone_pull": {"auto_pull": False}})
        self.assertFalse(cli.auto_pull_enabled(cfg))

    def test_enabled_when_auto_pull_true(self):
        cfg = StubConfig({"phone_pull": {"auto_pull": True}})
        self.assertTrue(cli.auto_pull_enabled(cfg))

    def test_disabled_gate_skips_script(self):
        cfg = StubConfig({"phone_pull": {"auto_pull": False}})
        self.assertFalse(cli.run_auto_phone_pull(cfg))


class PullOrderTests(unittest.TestCase):
    """run_full_cycle pulls FIRST — before the scan builds the organized
    tree — so a phone plugged in at cycle time gets its files organized
    in that same cycle."""

    def test_pull_runs_before_scan_and_organize(self):
        events = []

        def fake_pull(config):
            events.append("pull")
            return True

        class FakeScanner:
            def __init__(self, config):
                pass

            def scan_all_sources(self, allowed_extensions=None):
                events.append("scan")
                return iter(())

        with mock.patch.object(cli, "run_auto_phone_pull",
                               side_effect=fake_pull), \
             mock.patch.object(cli, "FileScanner", FakeScanner), \
             mock.patch.object(cli, "ContentAnalyzer"), \
             mock.patch.object(cli, "Organizer") as org_mock, \
             mock.patch.object(cli, "SyncEngine"), \
             mock.patch.object(cli, "SoftlinkHandler") as soft_mock, \
             mock.patch.object(cli, "AutoGit") as git_mock, \
             mock.patch.object(cli, "DedupEngine"), \
             mock.patch.object(cli, "_run_download_copy_dedupe",
                               return_value={}):
            org_mock.return_value.organize.return_value = {"links_created": 0}
            org_mock.return_value.clean_orphaned_links.return_value = 0
            soft_mock.return_value.get_rsync_exclude_patterns.return_value = []
            git_mock.return_value.enabled = False
            actions, _thread = cli.run_full_cycle(StubConfig({}),
                                                  dry_run=False)
        self.assertEqual(events, ["pull", "scan"])
        self.assertEqual(actions["links_created"], 0)

    def test_dry_run_does_not_pull(self):
        with mock.patch.object(cli, "run_auto_phone_pull") as pull_mock, \
             mock.patch.object(cli, "FileScanner") as scan_mock, \
             mock.patch.object(cli, "ContentAnalyzer"), \
             mock.patch.object(cli, "Organizer") as org_mock, \
             mock.patch.object(cli, "SyncEngine"), \
             mock.patch.object(cli, "SoftlinkHandler") as soft_mock, \
             mock.patch.object(cli, "AutoGit") as git_mock, \
             mock.patch.object(cli, "DedupEngine"), \
             mock.patch.object(cli, "_run_download_copy_dedupe",
                               return_value={}):
            scan_mock.return_value.scan_all_sources.return_value = iter(())
            org_mock.return_value.organize.return_value = {"links_created": 0}
            soft_mock.return_value.get_rsync_exclude_patterns.return_value = []
            git_mock.return_value.enabled = False
            cli.run_full_cycle(StubConfig({}), dry_run=True)
        pull_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

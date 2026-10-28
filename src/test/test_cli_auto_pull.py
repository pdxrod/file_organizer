"""Tests for the auto phone pull wiring in file_organizer.cli.

The computer's production auto cycle calls run_auto_phone_pull before the
folder sync so pulled phone files ride along to the cloud drive. These
tests cover the config gate; the phone_pull.sh behavior itself is covered
by test_phone_pull.py.
"""

import logging
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from file_organizer import cli  # noqa: E402

logging.getLogger("file_organizer").addHandler(logging.NullHandler())


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


if __name__ == "__main__":
    unittest.main()

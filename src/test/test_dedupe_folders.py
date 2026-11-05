"""Tests for cli._dedupe_folders sync-pair peer expansion.

Regression: `organize test-real` deleted download copies (foo_1, foo (1))
from ~/misc, but the two-way sync pair ~/misc ↔ Proton/misc copied them
straight back on the next rsync, so the duplicates reappeared every cycle.
The dedupe must cover both sides of the pair — these tests pin the folder
expansion that makes that happen.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from file_organizer.cli import _dedupe_folders  # noqa: E402


class _FakeConfig:
    def __init__(self, raw):
        self.raw_config = raw


def _cfg(organize=None, source=None, sync_pairs=None, one_way_pairs=None):
    return _FakeConfig({
        "organize_folders": organize or [],
        "source_folders": source or [],
        "sync_pairs": sync_pairs or [],
        "one_way_pairs": one_way_pairs or [],
    })


class DedupeFoldersTests(unittest.TestCase):
    def test_base_folders_are_union_of_organize_and_source(self):
        folders = _dedupe_folders(_cfg(
            organize=["/a/Documents", "/a/Music"],
            source=["/a/misc", "/a/Documents"],
        ))
        self.assertEqual(folders, ["/a/Documents", "/a/Music", "/a/misc"])

    def test_no_sync_pairs_adds_nothing(self):
        self.assertEqual(_dedupe_folders(_cfg(organize=["/a/misc"])),
                         ["/a/misc"])

    def test_two_way_pair_adds_the_peer(self):
        cfg = _cfg(
            organize=["/a/misc"],
            sync_pairs=[{"folders": ["/a/misc", "/cloud/misc"]}],
        )
        self.assertEqual(_dedupe_folders(cfg), ["/a/misc", "/cloud/misc"])

    def test_two_way_pair_adds_peer_in_either_direction(self):
        # The dedupe folder can be either side of the pair.
        cfg = _cfg(
            organize=["/cloud/misc"],
            sync_pairs=[{"folders": ["/a/misc", "/cloud/misc"]}],
        )
        self.assertEqual(_dedupe_folders(cfg), ["/cloud/misc", "/a/misc"])

    def test_unrelated_two_way_pair_is_ignored(self):
        cfg = _cfg(
            organize=["/a/misc"],
            sync_pairs=[{"folders": ["/a/pol", "/cloud/pol"]}],
        )
        self.assertEqual(_dedupe_folders(cfg), ["/a/misc"])

    def test_one_way_target_dedupe_adds_the_source(self):
        # One-way Proton/Music → ~/Music: copies deleted from the target
        # would be restored from the source, so the source is cleaned too.
        cfg = _cfg(
            organize=["/a/Music"],
            one_way_pairs=[{"folders": ["/cloud/Music", "/a/Music"]}],
        )
        self.assertEqual(_dedupe_folders(cfg), ["/a/Music", "/cloud/Music"])

    def test_one_way_source_dedupe_does_not_add_the_target(self):
        # One-way ~/pol → /Volumes/Ext/pol: nothing flows back from the
        # target, so deleting copies in ~/pol cannot be undone by it.
        cfg = _cfg(
            source=["/a/pol"],
            one_way_pairs=[{"folders": ["/a/pol", "/Volumes/Ext/pol"]}],
        )
        self.assertEqual(_dedupe_folders(cfg), ["/a/pol"])

    def test_pairs_chain_through_a_fixpoint(self):
        # /a/misc ↔ /cloud/misc, and /cloud/misc → /backup/misc one-way:
        # the backup source also restores copies, so it must be included.
        cfg = _cfg(
            source=["/a/misc"],
            sync_pairs=[{"folders": ["/a/misc", "/cloud/misc"]}],
            one_way_pairs=[{"folders": ["/backup/misc", "/cloud/misc"]}],
        )
        self.assertEqual(_dedupe_folders(cfg),
                         ["/a/misc", "/cloud/misc", "/backup/misc"])


if __name__ == "__main__":
    unittest.main()

"""Tests for testplan_common's flat YAML reader.

The guided config editor writes config.yaml with PyYAML's safe_dump, which
puts list dashes at the same column as their key (the 'indentationless'
style). The flat parser must read that as well as the hand-edited style
with deeper-indented list items — otherwise 'organize test' reports that no
sync pair is eligible even when both folders exist.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import testplan_common as tc


class ParseConfigStyleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.yaml"

    def _write(self, text):
        self.path.write_text(text)

    def test_indentationless_pyyaml_style(self):
        # Exactly what yaml.safe_dump produces (and what './organize
        # config' writes): list dashes at the same column as their key.
        self._write(
            "drives:\n"
            "  MAIN_DRIVE: /tmp/main\n"
            "  PROTON_DRIVE: /tmp/proton\n"
            "sync_pairs:\n"
            "- folders:\n"
            "  - MAIN_DRIVE/pol\n"
            "  - PROTON_DRIVE/pol\n"
            "- folders:\n"
            "  - MAIN_DRIVE/misc\n"
            "  - PROTON_DRIVE/misc\n"
            "one_way_pairs:\n"
            "- folders:\n"
            "  - MAIN_DRIVE/Music\n"
            "  - EXTERNAL_DRIVE/Music\n"
            "router:\n"
            "  enabled: true\n"
            "  route_roots:\n"
            "  - MAIN_DRIVE/misc\n"
            "  watch_folders:\n"
            "  - MAIN_DRIVE/Downloads\n"
            "  - MAIN_DRIVE/Desktop\n"
            "  target_folders:\n"
            "    political: ~/pol\n"
        )
        cfg = tc.parse_config(self.path)
        self.assertEqual(
            cfg["sync_pairs"],
            [["MAIN_DRIVE/pol", "PROTON_DRIVE/pol"],
             ["MAIN_DRIVE/misc", "PROTON_DRIVE/misc"]],
        )
        self.assertEqual(
            cfg["one_way_pairs"],
            [["MAIN_DRIVE/Music", "EXTERNAL_DRIVE/Music"]],
        )
        self.assertEqual(cfg["drives"]["MAIN_DRIVE"], "/tmp/main")
        self.assertEqual(
            cfg["router"]["watch_folders"],
            ["MAIN_DRIVE/Downloads", "MAIN_DRIVE/Desktop"],
        )
        self.assertEqual(cfg["router"]["route_roots"], ["MAIN_DRIVE/misc"])
        self.assertEqual(cfg["router"]["target_folders"]["political"],
                         "~/pol")

    def test_hand_edited_indented_style_still_works(self):
        self._write(
            "drives:\n"
            "  MAIN_DRIVE: /tmp/main\n"
            "sync_pairs:\n"
            "  - folders:\n"
            "    - MAIN_DRIVE/pol\n"
            "    - /tmp/proton/pol\n"
            "router:\n"
            "  watch_folders:\n"
            "    - MAIN_DRIVE/Downloads\n"
        )
        cfg = tc.parse_config(self.path)
        self.assertEqual(
            cfg["sync_pairs"], [["MAIN_DRIVE/pol", "/tmp/proton/pol"]]
        )
        self.assertEqual(cfg["router"]["watch_folders"],
                         ["MAIN_DRIVE/Downloads"])


class PairCandidateEligibilityTests(unittest.TestCase):
    def test_existing_pair_folders_are_eligible(self):
        # The bug this guards against: both folders exist on disk, but the
        # indentationless config parses as zero pairs, so the sweep step
        # bails with "no eligible sync_pair found".
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a = root / "main" / "pol"
            b = root / "proton" / "pol"
            a.mkdir(parents=True)
            b.mkdir(parents=True)
            cfg_path = root / "config.yaml"
            cfg_path.write_text(
                "drives:\n"
                f"  MAIN_DRIVE: {root / 'main'}\n"
                f"  PROTON_DRIVE: {root / 'proton'}\n"
                "sync_pairs:\n"
                "- folders:\n"
                "  - MAIN_DRIVE/pol\n"
                "  - PROTON_DRIVE/pol\n"
            )
            cfg = tc.parse_config(cfg_path)
            sync_cands, _ow_cands = tc.pair_candidates(cfg)
            self.assertEqual(len(sync_cands), 1)
            chosen, _ow, messages = tc.select_pairs(cfg)
            self.assertIsNotNone(chosen)
            self.assertFalse(any("no eligible sync_pair" in m
                                 for m in messages))


if __name__ == "__main__":
    unittest.main()

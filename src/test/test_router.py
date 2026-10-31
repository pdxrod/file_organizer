"""Tests for the deep router: name + content classification into the seven
buckets (pol / dev / dev-ml / Pictures / Music / Documents / misc).

Covers the decision phases, state persistence, apply + undo, collision
safety, exclusions, the watch pass, and the DeepSeek fallback paths. No
network and no macOS Vision here — extraction is stubbed.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

import file_organizer.router as router_mod  # noqa: E402
from file_organizer.router import Router  # noqa: E402

import logging  # noqa: E402
logging.getLogger("file_organizer.router").addHandler(logging.NullHandler())


class FakeConfig:
    """Minimal stand-in for Config: raw_config + _resolve_path passthrough."""

    def __init__(self, router_raw):
        self.raw_config = {"exclude_patterns": [], "router": router_raw}

    def _resolve_path(self, p):
        return p if isinstance(p, str) else str(p)


class StubExtractor:
    """Extraction stub: canned results keyed by file name."""

    def __init__(self, results=None):
        self.results = results or {}

    def extract(self, path):
        return self.results.get(
            Path(path).name, {"text": "", "meta": {}}
        )


class FakeDeepSeek:
    """Records the items it saw and returns a canned result list."""

    api_key = "test-key"

    def __init__(self, results=None):
        self.results = results or []
        self.seen = []

    def classify_batch(self, items):
        self.seen.append(items)
        if isinstance(self.results, list) and self.results:
            return list(self.results)
        return [{} for _ in items]


def make_router(tmp, **overrides):
    extractor = overrides.pop("extractor", None)
    raw = {
        "enabled": True,
        "action": "move",
        "route_roots": [str(tmp / "in")],
        "watch_folders": [str(tmp / "watch")],
        "target_folders": {
            "political": str(tmp / "out" / "pol"),
            "ml_development": str(tmp / "out" / "dev" / "ml"),
            "development": str(tmp / "out" / "dev"),
            "pictures": str(tmp / "out" / "Pictures"),
            "music": str(tmp / "out" / "Music"),
            "documents": str(tmp / "out" / "Documents"),
            "misc": str(tmp / "out" / "misc"),
        },
        "deepseek": {"enabled": False},
        "ocr": {"enabled": True, "budget_per_run": 10},
        "state_file": str(tmp / "router_state.json"),
    }
    raw.update(overrides)
    return Router(FakeConfig(raw), deepseek=None, extractor=extractor)


def write(tmp, rel, content="x"):
    p = Path(tmp) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def category_of(router, name):
    return router.state["entries"][str(router.route_roots[0] / name)].get(
        "category"
    )


class ExtensionFastPathTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_music_by_extension(self):
        write(self.tmp, "in/song.mp3")
        r = make_router(self.tmp)
        r.run()
        self.assertEqual(category_of(r, "song.mp3"), "music")

    def test_code_by_extension(self):
        write(self.tmp, "in/setup.rb", "puts :hi")
        r = make_router(self.tmp)
        r.run()
        self.assertEqual(category_of(r, "setup.rb"), "development")


class FilenameKeywordTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_political_name(self):
        write(self.tmp, "in/ukraine_war_news.txt", "hello")
        r = make_router(self.tmp)
        r.run()
        self.assertEqual(category_of(r, "ukraine_war_news.txt"), "political")

    def test_ml_name_on_code(self):
        write(self.tmp, "in/pytorch_training.py", "x = 1")
        r = make_router(self.tmp)
        r.run()
        self.assertEqual(category_of(r, "pytorch_training.py"),
                         "ml_development")

    def test_ml_name_on_text(self):
        write(self.tmp, "in/transformers_notes.md", "# notes")
        r = make_router(self.tmp)
        r.run()
        self.assertEqual(category_of(r, "transformers_notes.md"),
                         "ml_development")


class ContentKeywordTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_political_content_beats_neutral_name(self):
        write(self.tmp, "in/letter.txt",
              "The parliament voted on new sanctions against Moscow.")
        r = make_router(self.tmp)
        r.run()
        entry = r.state["entries"][str(self.tmp / "in" / "letter.txt")]
        self.assertEqual(entry["category"], "political")
        self.assertEqual(entry["source"], "content")

    def test_ml_content(self):
        write(self.tmp, "in/notes.txt",
              "Use gradient descent to train a neural network.")
        r = make_router(self.tmp)
        r.run()
        self.assertEqual(category_of(r, "notes.txt"), "ml_development")

    def test_weak_words_alone_do_not_decide(self):
        # "ai" and "training" are weak; a neutral document stays documents.
        write(self.tmp, "in/diary.txt",
              "I went to training today. My ai friend came too.")
        r = make_router(self.tmp)
        r.run()
        self.assertEqual(category_of(r, "diary.txt"), "documents")


class MediaAndFallbackTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_plain_photo_falls_back_to_pictures(self):
        write(self.tmp, "in/holiday.jpg", "img")
        r = make_router(self.tmp, extractor=StubExtractor())
        r.run()
        self.assertEqual(category_of(r, "holiday.jpg"), "pictures")

    def test_scene_labels_route_political_photo(self):
        # A rally photo with no readable text: Vision labels decide.
        write(self.tmp, "in/rally.jpg", "img")
        r = make_router(self.tmp, extractor=StubExtractor({
            "rally.jpg": {"text": "",
                          "meta": {"classifications":
                                   "crowd 0.82; flag 0.31; outdoor 0.2"}},
        }))
        r.run()
        entry = r.state["entries"][str(self.tmp / "in" / "rally.jpg")]
        self.assertEqual(entry["category"], "political")
        self.assertIn("political scene", entry["reason"])

    def test_ocr_political_text_in_photo(self):
        write(self.tmp, "in/protest_sign.jpg", "img")
        r = make_router(self.tmp, extractor=StubExtractor({
            "protest_sign.jpg": {"text": "CEASEFIRE NOW — free Palestine",
                                 "meta": {}},
        }))
        r.run()
        self.assertEqual(category_of(r, "protest_sign.jpg"), "political")

    def test_picture_of_document_by_name(self):
        write(self.tmp, "in/receipt_photo.jpg", "img")
        r = make_router(self.tmp, extractor=StubExtractor())
        r.run()
        self.assertEqual(category_of(r, "receipt_photo.jpg"), "documents")

    def test_unknown_type_goes_to_misc(self):
        write(self.tmp, "in/weird.xyz", "data")
        r = make_router(self.tmp)
        r.run()
        self.assertEqual(category_of(r, "weird.xyz"), "misc")

    def test_ocr_budget_settles_rest_by_fallback(self):
        write(self.tmp, "in/one.jpg", "img")
        write(self.tmp, "in/two.jpg", "img")
        r = make_router(self.tmp, extractor=StubExtractor(), ocr={
            "enabled": True, "budget_per_run": 1})
        r.run()
        entries = r.state["entries"]
        self.assertEqual(len(entries), 2)
        reasons = [e["reason"] for e in entries.values()]
        self.assertTrue(any("OCR budget exhausted" in x for x in reasons))
        self.assertTrue(
            all(e["source"] == "fallback" for e in entries.values()))


class StatePersistenceTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_decisions_survive_reruns(self):
        write(self.tmp, "in/song.mp3")
        r1 = make_router(self.tmp)
        r1.run()
        self.assertTrue((self.tmp / "router_state.json").exists())

        r2 = make_router(self.tmp)
        self.assertEqual(
            r2.state["entries"][str(self.tmp / "in" / "song.mp3")]["category"],
            "music",
        )

    def test_rerun_skips_unchanged_decided_files(self):
        write(self.tmp, "in/song.mp3")
        r1 = make_router(self.tmp)
        r1.run()
        r2 = make_router(self.tmp)
        stats = r2.run()
        self.assertEqual(stats["scanned"], 0)
        self.assertEqual(category_of(r2, "song.mp3"), "music")

    def test_changed_file_is_redecided(self):
        p = write(self.tmp, "in/notes.txt", "hello")
        r1 = make_router(self.tmp)
        r1.run()
        self.assertEqual(category_of(r1, "notes.txt"), "documents")
        p.write_text("Training a neural network with gradient descent.")
        r2 = make_router(self.tmp)
        r2.run()
        self.assertEqual(category_of(r2, "notes.txt"), "ml_development")


class InboxSweepTests(unittest.TestCase):
    """A sweep root that is also a target bucket (the ~/misc inbox) must
    be classified, not skipped as 'already placed'."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_inbox_files_are_classified(self):
        write(self.tmp, "misc/ukraine_war_notes.txt", "parliament sanctions")
        r = make_router(self.tmp, route_roots=[str(self.tmp / "misc")])
        stats = r.run()
        self.assertEqual(stats["scanned"], 1)
        self.assertEqual(
            r.state["entries"][str(self.tmp / "misc" /
                                   "ukraine_war_notes.txt")]["category"],
            "political",
        )

    def test_inbox_file_decided_misc_stays_put(self):
        write(self.tmp, "misc/song.mp3")
        write(self.tmp, "misc/weird.xyz", "data")
        targets = {
            "political": str(self.tmp / "out" / "pol"),
            "ml_development": str(self.tmp / "out" / "dev" / "ml"),
            "development": str(self.tmp / "out" / "dev"),
            "pictures": str(self.tmp / "out" / "Pictures"),
            "music": str(self.tmp / "out" / "Music"),
            "documents": str(self.tmp / "out" / "Documents"),
            "misc": str(self.tmp / "misc"),
        }
        r = make_router(self.tmp, route_roots=[str(self.tmp / "misc")],
                        target_folders=targets)
        stats = r.run(apply=True)
        # The mp3 left the inbox; the unknown type stayed put.
        self.assertEqual(stats["moved"], 1)
        self.assertTrue((self.tmp / "out" / "Music" / "song.mp3").exists())
        self.assertFalse((self.tmp / "misc" / "song.mp3").exists())
        self.assertTrue((self.tmp / "misc" / "weird.xyz").exists())


class ApplyUndoCollisionTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_apply_moves_and_undo_puts_back(self):
        write(self.tmp, "in/song.mp3")
        write(self.tmp, "in/ukraine_war_news.txt", "parliament sanctions")
        r = make_router(self.tmp)
        stats = r.run(apply=True)
        self.assertEqual(stats["moved"], 2)
        self.assertTrue((self.tmp / "out" / "Music" / "song.mp3").exists())
        self.assertTrue(
            (self.tmp / "out" / "pol" / "ukraine_war_news.txt").exists())
        self.assertFalse((self.tmp / "in" / "song.mp3").exists())

        undone = r.undo()
        self.assertEqual(len(undone), 2)
        self.assertTrue((self.tmp / "in" / "song.mp3").exists())
        self.assertTrue((self.tmp / "in" / "ukraine_war_news.txt").exists())

    def test_collision_never_overwrites(self):
        write(self.tmp, "in/report.pdf", "new content")
        dest = write(self.tmp, "out/Documents/report.pdf", "old content")
        r = make_router(self.tmp, extractor=StubExtractor())
        stats = r.run(apply=True)
        self.assertEqual(stats["collisions"], 1)
        self.assertEqual(dest.read_text(), "old content")
        self.assertTrue((self.tmp / "in" / "report.pdf").exists())

    def test_identical_destination_kept_once(self):
        write(self.tmp, "in/report.pdf", "same bytes")
        write(self.tmp, "out/Documents/report.pdf", "same bytes")
        r = make_router(self.tmp, extractor=StubExtractor())
        stats = r.run(apply=True)
        self.assertEqual(stats["duplicates"], 1)
        self.assertTrue((self.tmp / "in" / "report.pdf").exists())


class ExclusionTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_hidden_inprogress_and_already_placed_are_skipped(self):
        write(self.tmp, "in/.hidden.txt")
        write(self.tmp, "in/download.part")
        write(self.tmp, "out/pol/already_here.txt")
        r = make_router(self.tmp)
        stats = r.run()
        self.assertEqual(stats["scanned"], 0)

    def test_project_root_never_touched(self):
        fake_root = self.tmp / "checkout"
        write(fake_root, "in/src/hello.txt", "code")
        original = router_mod.find_project_root
        router_mod.find_project_root = lambda: fake_root
        try:
            r = make_router(self.tmp)
            stats = r.run()
            self.assertEqual(stats["scanned"], 0)
        finally:
            router_mod.find_project_root = original

    def test_symlink_skipped(self):
        write(self.tmp, "in/real.txt", "content")
        os.symlink(self.tmp / "in" / "real.txt", self.tmp / "in" / "link.txt")
        r = make_router(self.tmp)
        stats = r.run()
        self.assertEqual(stats["scanned"], 1)  # only real.txt
        self.assertNotIn(
            str(self.tmp / "in" / "link.txt"), r.state["entries"])


class WatchPassTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_watch_pass_routes_only_watch_folders(self):
        write(self.tmp, "watch/new_ukraine_story.txt", "sanctions parliament")
        write(self.tmp, "in/other.mp3")
        r = make_router(self.tmp)
        stats = r.watch_pass()
        self.assertEqual(stats["scanned"], 1)
        self.assertEqual(
            r.state["entries"][str(self.tmp / "watch" /
                                   "new_ukraine_story.txt")]["category"],
            "political",
        )


class DeepSeekFallbackTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_budget_zero_still_decides_by_rules(self):
        write(self.tmp, "in/mystery.txt", "some unusual content here")
        ds = FakeDeepSeek()
        r = make_router(self.tmp, extractor=StubExtractor({
            "mystery.txt": {"text": "some unusual content here", "meta": {}},
        }), deepseek={"enabled": True, "max_calls_per_run": 0})
        r.ds = ds
        r.run()
        entry = r.state["entries"][str(self.tmp / "in" / "mystery.txt")]
        self.assertEqual(entry["source"], "fallback")
        self.assertIn("budget exhausted", entry["reason"])
        self.assertEqual(ds.seen, [])

    def test_api_failure_falls_back(self):
        write(self.tmp, "in/mystery.txt", "some unusual content here")
        ds = FakeDeepSeek(results=[])
        r = make_router(self.tmp, extractor=StubExtractor({
            "mystery.txt": {"text": "some unusual content here", "meta": {}},
        }))
        r.ds = ds
        r.run()
        entry = r.state["entries"][str(self.tmp / "in" / "mystery.txt")]
        self.assertEqual(entry["source"], "fallback")
        self.assertIn("unsure or failed", entry["reason"])

    def test_deepseek_decision_wins(self):
        write(self.tmp, "in/mystery.txt", "some unusual content here")
        ds = FakeDeepSeek(results=[
            {"category": "documents", "confidence": 0.95,
             "reason": "looks like a form"},
        ])
        r = make_router(self.tmp, extractor=StubExtractor({
            "mystery.txt": {"text": "some unusual content here", "meta": {}},
        }))
        r.ds = ds
        r.run()
        entry = r.state["entries"][str(self.tmp / "in" / "mystery.txt")]
        self.assertEqual(entry["category"], "documents")
        self.assertEqual(entry["source"], "deepseek")
        # The text reached DeepSeek.
        self.assertEqual(ds.seen[0][0]["text"], "some unusual content here")


class DefaultDisabledTests(unittest.TestCase):
    def test_no_router_section_means_disabled(self):
        r = Router(FakeConfig({}), deepseek=None)
        self.assertFalse(r.enabled)
        stats = r.run()
        self.assertEqual(stats["scanned"], 0)


class NoHardcodedTargetsTests(unittest.TestCase):
    """Target folders come from config only. A missing target must be an
    explicit error, never a silent move into a built-in default."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_missing_target_folder_is_an_error_not_a_default(self):
        write(self.tmp, "in/song.mp3")
        r = make_router(self.tmp, target_folders={})
        stats = r.run(apply=True)
        self.assertEqual(stats["moved"], 0)
        entry = r.state["entries"][str(self.tmp / "in" / "song.mp3")]
        self.assertEqual(entry["status"], "error")
        self.assertIn("no target folder", entry["reason"])


if __name__ == "__main__":
    unittest.main()

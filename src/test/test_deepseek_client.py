"""Tests for the DeepSeek client's dynamic category handling.

The router builds the model's prompt from the CONFIGURED categories, so
these tests check that user-defined categories are offered, removed
categories never are, and off-list answers are coerced safely. No
network — the _chat call is stubbed.
"""

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

from file_organizer.deepseek_client import (  # noqa: E402
    CATEGORIES,
    DeepSeekClient,
    build_system_prompt,
)


class PromptTests(unittest.TestCase):
    def test_default_prompt_lists_all_builtins(self):
        prompt = build_system_prompt(list(CATEGORIES))
        for cat in CATEGORIES:
            self.assertIn(f'"{cat}"', prompt)

    def test_custom_category_gets_generic_description(self):
        prompt = build_system_prompt(["documents", "finance"])
        self.assertIn('"finance"', prompt)
        self.assertIn("files about finance", prompt)
        self.assertNotIn('"political"', prompt)

    def test_political_rule_only_when_political_configured(self):
        plain = build_system_prompt(["documents", "misc"])
        self.assertNotIn("Political content wins", plain)
        with_pol = build_system_prompt(
            ["political", "documents", "misc"])
        self.assertIn("Political content wins", with_pol)


class CoercionTests(unittest.TestCase):
    def _client(self, categories, payload):
        client = DeepSeekClient(api_key="k", categories=categories)
        client._chat = lambda messages, response_format: {  # noqa: ARG005
            "content": payload,
        }
        return client

    def _one(self, client):
        return client.classify_batch(
            [{"name": "a.txt", "kind": "file", "text": ""}]
        )[0]

    def test_off_list_category_coerced_to_misc(self):
        client = self._client(
            ["documents", "misc"],
            '{"results": [{"i": 0, "category": "political", '
            '"confidence": 0.9, "reason": "x"}]}',
        )
        self.assertEqual(self._one(client)["category"], "misc")

    def test_off_list_category_without_misc_uses_first_category(self):
        client = self._client(
            ["documents", "pictures"],
            '{"results": [{"i": 0, "category": "political", '
            '"confidence": 0.9, "reason": "x"}]}',
        )
        self.assertEqual(self._one(client)["category"], "documents")

    def test_on_list_custom_category_kept(self):
        client = self._client(
            ["finance", "misc"],
            '{"results": [{"i": 0, "category": "finance", '
            '"confidence": 0.8, "reason": "x"}]}',
        )
        self.assertEqual(self._one(client)["category"], "finance")

    def test_defaults_to_builtin_list_when_none_given(self):
        client = self._client(
            None,
            '{"results": [{"i": 0, "category": "political", '
            '"confidence": 0.9, "reason": "x"}]}',
        )
        self.assertEqual(client.categories, CATEGORIES)
        self.assertEqual(self._one(client)["category"], "political")

    def test_empty_category_list_falls_back_to_builtins(self):
        client = DeepSeekClient(api_key="k", categories=[])
        self.assertEqual(client.categories, CATEGORIES)


if __name__ == "__main__":
    unittest.main()

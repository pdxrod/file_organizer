"""deepseek_client.py — minimal DeepSeek chat-completions client (stdlib only).

The router uses this to classify files whose name alone isn't enough:
batches of {name, kind, text} are sent to the DeepSeek API, which answers
with a JSON list of {category, confidence, reason} per file.

DeepSeek's public API is text-only (deepseek-chat / deepseek-reasoner).
Images and videos reach it as OCR/metadata text produced by extractor.py.
The API key is read from DEEPSEEK_API_KEY, then from the key file named in
the config (never logged).
"""

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

# The seven buckets the router knows about. Keep in sync with router.py.
CATEGORIES = [
    "political", "ml_development", "development",
    "pictures", "music", "documents", "misc",
]

SYSTEM_PROMPT = """You are the classification engine of a personal file organizer.
You receive a batch of files (name, kind, and an excerpt of extracted text,
possibly empty). For each file pick exactly one category:

- "political": politics, geopolitics, wars, elections, politicians, ideology,
  activism, protests, news/commentary about politics, books or articles on
  political/culture-war topics.
- "ml_development": AI/ML material — papers, notes, notebooks, or code about
  machine learning, deep learning, neural networks, LLMs, transformers,
  training, data science, AI tools.
- "development": software development — programs, repos, dev notes, configs,
  documentation of software projects that are NOT about ML.
- "pictures": non-political images and videos — photos, screenshots, travel,
  family, clips, memes that are not political.
- "music": audio files.
- "documents": general documents — letters, receipts, forms, records, reports
  that are not political and not ML.
- "misc": none of the above, or genuinely impossible to tell.

Rules:
1. Political content wins over documents and pictures for text-bearing files.
2. Software/code files go to development (or ml_development if ML-related),
   never political.
3. Media without political content goes to pictures.
4. When unsure, choose the single most likely category and set confidence
   below 0.6 — the organizer falls back to filename-based rules for those.

Answer with JSON only, in exactly this shape:
{"results": [{"i": 0, "category": "political", "confidence": 0.9, "reason": "short reason"}]}
Every input file must appear exactly once."""


class DeepSeekError(Exception):
    """Any API failure the router should treat as 'fall back to rules'."""


class DeepSeekClient:
    """Stateless, stdlib-only DeepSeek chat client."""

    def __init__(
        self,
        api_key: str = "",
        api_key_path: str = "",
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: int = 90,
        max_retries: int = 2,
        max_tokens: int = 4096,
    ):
        self.api_key = (
            api_key
            or os.environ.get("DEEPSEEK_API_KEY", "")
            or self._read_key_file(api_key_path)
        ).strip()
        self.model = model or DEFAULT_MODEL
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_tokens = max_tokens

    @staticmethod
    def _read_key_file(path: str) -> str:
        if not path:
            return ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                first = f.readline().strip()
                # Accept `sk-...` alone or `KEY=sk-...` lines.
                return first.split("=", 1)[-1].strip() if first else ""
        except OSError:
            return ""

    # ── low-level call ───────────────────────────────────────────────

    def _chat(self, messages: list, response_format: dict) -> dict:
        """POST /chat/completions; returns the first choice's message."""
        if not self.api_key:
            raise DeepSeekError("no DeepSeek API key (set DEEPSEEK_API_KEY "
                                "or the router's deepseek.api_key_path)")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "response_format": response_format,
        }
        url = self.base_url + "/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        last_error: Exception = DeepSeekError("no response")
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout_seconds
                ) as response:
                    body = response.read()
                data = json.loads(body.decode("utf-8"))
                choices = data.get("choices") or []
                if not choices:
                    raise DeepSeekError("empty choices in API response")
                return choices[0].get("message") or {}
            except (urllib.error.URLError, urllib.error.HTTPError,
                    OSError, json.JSONDecodeError) as e:
                last_error = e
                if isinstance(e, urllib.error.HTTPError) and e.code == 401:
                    raise DeepSeekError(
                        "DeepSeek API rejected the key (401)") from e
                if attempt < self.max_retries:
                    logger.warning("DeepSeek call failed (%s); retrying %d/%d",
                                   e, attempt + 1, self.max_retries)
        raise DeepSeekError(f"DeepSeek call failed: {last_error}")

    # ── classification ───────────────────────────────────────────────

    def classify_batch(self, items: list) -> list:
        """Classify a batch of items [{name, kind, text}] → list of dicts
        [{category, confidence, reason}] in the same order. On any failure
        returns [] so the router falls back to local rules."""
        if not items:
            return []
        lines = []
        for i, item in enumerate(items):
            name = str(item.get("name", ""))[:300]
            kind = str(item.get("kind", ""))[:40]
            text = str(item.get("text", ""))[:2000]
            text = " ".join(text.split())  # collapse whitespace
            lines.append(f"[{i}] name: {name}\n    kind: {kind}\n"
                         f"    text: {text or '(no text extracted)'}")
        user = "Classify these files:\n\n" + "\n".join(lines)
        try:
            message = self._chat(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
            )
        except DeepSeekError as e:
            logger.warning("router: %s — using filename rules instead", e)
            return []

        content = message.get("content") or ""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("router: DeepSeek returned non-JSON; "
                           "using filename rules instead")
            return []
        raw_results = parsed.get("results")
        if not isinstance(raw_results, list):
            return []

        by_index = {}
        for entry in raw_results:
            if not isinstance(entry, dict):
                continue
            try:
                idx = int(entry.get("i", -1))
            except (TypeError, ValueError):
                continue
            category = str(entry.get("category", "")).strip().lower()
            if category not in CATEGORIES:
                category = "misc"
            try:
                confidence = float(entry.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            reason = str(entry.get("reason", ""))[:200]
            by_index[idx] = {
                "category": category,
                "confidence": max(0.0, min(1.0, confidence)),
                "reason": reason,
                "source": "deepseek",
            }

        return [by_index.get(i, {}) for i in range(len(items))]

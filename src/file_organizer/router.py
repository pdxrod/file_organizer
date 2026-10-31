"""router.py — the deep router: works out where a file should go from its
NAME and its CONTENTS, then moves it there.

Seven buckets (the user's target layout):
    political      -> ~/pol          (politics, geopolitics, culture war…)
    ml_development -> ~/dev/ml       (AI/ML projects, papers, notes, code)
    development    -> ~/dev          (software projects and dev notes)
    pictures       -> ~/Pictures     (non-political images AND videos)
    music          -> ~/Music
    documents      -> ~/Documents
    misc           -> ~/misc         (anything that can't be worked out)

Decision cascade, cheapest first:
    1. extension fast path          (music; code -> dev family)
    2. filename keyword signals     (political / ml / development)
    3. extracted content signals    (text files, PDFs, OCR'd media)
    4. DeepSeek API batch           (only for text-bearing files rules
                                     couldn't decide; batched, capped)
    5. fallback by extension        (pictures / documents / misc)

Every decision is persisted in router_state.json so re-runs skip what is
already decided and `route --undo` can put files back. Moves are
collision-safe: a same-named file with different content is never
overwritten — it stays put and is reported.

Safety: dotfiles, symlinks, excluded patterns, files already inside a
target folder, and the file_organizer checkout itself are never touched.
"""

import fnmatch
import hashlib
import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Optional

from file_organizer.config import find_project_root
from file_organizer.extractor import (
    Extractor, TEXT_EXTS, IMAGE_EXTS, VIDEO_EXTS, HTML_EXTS, OFFICE_EXTS,
)
from file_organizer.deepseek_client import DeepSeekClient, CATEGORIES

logger = logging.getLogger(__name__)

STATE_VERSION = 1

MUSIC_EXTS = {
    ".mp3", ".m4a", ".m4p", ".flac", ".wav", ".aac", ".ogg", ".wma",
    ".aiff", ".aif", ".opus",
}
CODE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".rb", ".php", ".java", ".c",
    ".cpp", ".h", ".hpp", ".rs", ".go", ".swift", ".kt", ".kts", ".sh",
    ".bash", ".zsh", ".fish", ".ex", ".exs", ".erl", ".hrl", ".clj",
    ".cljs", ".hs", ".nim", ".zig", ".sql", ".css", ".scss", ".sass",
    ".less", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".conf", ".dockerfile", ".lock", ".gradle", ".r", ".rmd", ".ipynb",
    ".el", ".lisp", ".scm", ".lua", ".pl", ".pm", ".asm", ".vbs", ".ps1",
    ".vb", ".cs", ".f", ".f90", ".jl", ".dart", ".groovy", ".m", ".mm",
}
DOC_EXTS = {
    ".pdf", ".doc", ".docx", ".odt", ".rtf", ".pages", ".rtfd",
    ".xls", ".xlsx", ".ods", ".ppt", ".pptx", ".odp", ".csv", ".tsv",
}
ARCHIVE_EXTS = {
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".dmg", ".iso",
    ".pkg", ".deb", ".rpm", ".gpx", ".kml", ".kmz", ".torrent", ".apk",
}

# In-progress download suffixes — never route these.
_IN_PROGRESS_SUFFIXES = (
    ".crdownload", ".part", ".download", ".tmp", ".temp", ".partial",
)

# ── keyword tiers ─────────────────────────────────────────────────────
# STRONG words/phrases: one hit decides. WEAK: need two distinct hits.
# Phrases with spaces are matched as substrings of the (lowercased) text;
# single words are matched on word boundaries. "ml"/"war" live in the weak
# tier so a stray letter pair can't decide on its own; "ai"/"training"
# proved too common in everyday prose and count for nothing.

_POLITICAL_STRONG_WORDS = [
    "gaza", "palestine", "palestinian", "israel", "israeli", "hamas",
    "hezbollah", "iran", "iraq", "syria", "afghanistan", "taliban",
    "ukraine", "ukrainian", "russia", "russian", "putin", "zelensky",
    "zelenskyy", "kremlin", "moscow", "nato", "trump", "biden", "harris",
    "musk", "starmer", "sunak", "corbyn", "farage", "macron", "scholz",
    "merkel", "assad", "saddam", "hussein", "gaddafi", "qaddafi",
    "osama", "qaeda", "isis", "houthi", "houthis", "yemen", "lebanon",
    "kurdish", "kurdistan", "kurds", "armenia", "azerbaijan", "chechnya",
    "serbia", "kosovo", "bosnia", "milosevic", "tito", "yugoslavia",
    "brexit", "tory", "tories", "maga", "wikileaks", "assange", "snowden",
    "epstein", "mossad", "cia", "fbi", "kgb", "nsa", "mi6", "genocide",
    "apartheid", "zionism", "zionist", "antisemitism", "anti-semitism",
    "fascism", "fascist", "communism", "communist", "marxism", "marxist",
    "antiracism", "anti-racism", "hatecrimes", "hate-crimes", "woke",
    "censorship", "propaganda", "geopolitics", "geopolitical", "diplomacy",
    "diplomat", "ceasefire", "sanctions", "election", "elections",
    "referendum", "congress", "senate", "parliament", "presidency",
    "presidential", "protest", "protests", "uprising", "coup",
    "revolution", "whistleblower", "espionage", "spying",
]
_POLITICAL_STRONG_PHRASES = [
    "prime minister", "secretary of state", "foreign affairs",
    "foreign policy", "state department", "white house", "downing street",
    "war crime", "war crimes", "human rights", "hate crime", "hate speech",
    "climate crisis", "net zero", "extinction rebellion", "supreme court",
    "un security council", "election interference", "border wall",
    "gun control", "vaccine mandate",
]
_POLITICAL_WEAK_WORDS = [
    "war", "wars", "politics", "political", "politician", "government",
    "policy", "policies", "freedom", "liberty", "rights", "ideology",
    "democracy", "dictatorship", "authoritarian", "totalitarian",
    "soviet", "pentagon", "military", "army", "navy", "troops", "soldiers",
    "invasion", "bombing", "airstrike", "missiles", "hostage", "captives",
    "torture", "corruption", "scandal", "lobbying", "campaign", "mayor",
    "governor", "lawmaker", "legislation", "abortion", "feminism",
    "feminist", "gender", "diversity", "carbon", "emissions", "pandemic",
    "lockdown", "vaccine", "vaccines", "constitution", "amendment",
    "citizenship", "immigrant", "immigration", "refugee", "refugees",
    "asylum", "candidate", "candidates", "ballot", "electorate",
    "diplomatic", "negotiations", "treaty", "alliance", "coalition",
]

_ML_STRONG_WORDS = [
    "tensorflow", "pytorch", "keras", "scikit", "sklearn", "neural",
    "transformer", "transformers", "llm", "llms", "gpt", "chatgpt",
    "deepseek", "llama", "mistral", "alphafold", "bert", "openai",
    "anthropic", "deepmind", "midjourney", "dall-e", "dalle", "jupyter",
    "kaggle", "rnn", "cnn", "lstm", "gan", "perceptron", "backprop",
    "autograd", "finetune", "fine-tune", "fine-tuning", "pretrained",
    "pre-trained", "embeddings", "cuda", "pytorch", "mlops",
]
_ML_STRONG_PHRASES = [
    "machine learning", "deep learning", "neural network", "neural net",
    "language model", "large language model", "reinforcement learning",
    "supervised learning", "unsupervised learning", "gradient descent",
    "stable diffusion", "computer vision", "hugging face", "model weights",
    "training data", "data science", "generative ai", "artificial intelligence",
]
# "ai" and "training" were dropped from the weak tier: they appear in
# everyday prose (a diary can mention an "ai friend" and a "training"
# day) and must never decide — not even as a pair.
_ML_WEAK_WORDS = [
    "ml", "gpu", "nvidia", "inference", "attention", "tokens",
    "tokenizer", "diffusion", "gemini", "claude", "copilot", "notebook",
    "pandas", "numpy", "matplotlib", "dataset", "datasets",
    "weights", "neuron", "neurons", "classifier", "prediction",
]

_DEV_STRONG_WORDS = [
    "rails", "django", "flask", "elixir", "erlang", "node", "javascript",
    "typescript", "react", "vue", "kotlin", "xcode", "docker", "kubernetes",
    "postgres", "mysql", "sqlite", "redis", "nginx", "github", "bitbucket",
    "gitlab", "devops", "webpack", "npm", "laravel", "rubygems",
    "gemfile", "backend", "frontend", "database", "deployment", "debugging",
    "refactoring", "codebase", "sass", "tailwind", "rust", "golang",
    "swiftui", "android", "ios", "mobile", "cli", "rest", "graphql",
    "socket", "websocket", "api", "apis", "sdk", "framework", "library",
    "compiler", "interpreter", "debugger", "profiler", "container",
    "microservice", "serverless", "terraform", "ansible", "jenkins",
]
_DEV_STRONG_PHRASES = [
    "ruby on rails", "web app", "web development", "software development",
    "app development", "version control", "unit test", "integration test",
    "continuous integration", "design pattern", "data structure",
    "operating system", "linux kernel", "command line", "source code",
]

# Media files whose NAME says they're pictures of documents.
_DOCNAME_WORDS = [
    "receipt", "invoice", "ticket", "e-ticket", "certificate", "cert",
    "letter", "form", "resume", "cv", "contract", "agreement", "statement",
    "payslip", "insurance", "prescription", "appointment", "passport",
    "visa", "licence", "license", "permit", "deed", "identity", "id-card",
    "idcard", "registration", "confirmation", "booking", "itinerary",
]

# Vision scene-classifier labels (extract.swift) that suggest a political
# photo/video even when there is no readable text in it. Matched as
# substrings of the lowercased label, e.g. "military_uniform 0.82".
_POLITICAL_SCENE_WORDS = [
    "military", "soldier", "tank", "warplane", "warship", "weapon",
    "firearm", "gun", "missile", "army", "naval", "uniform", "camouflage",
    "camo", "riot", "protest", "demonstration", "crowd", "march",
    "parade", "police", "flag", "banner", "candidate", "podium",
    "election", "rally", "government", "capitol", "parliament", "un",
]


def _lower_stem(name: str) -> str:
    """Stem of a filename, lowercased, separators and camelCase split."""
    stem = re.sub(r"[-_.,;:!?()\[\]{}]+", " ", name).lower()
    stem = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", stem)  # camelCase
    return stem


class Router:
    """Classifies files into the seven buckets and moves them."""

    def __init__(self, config, deepseek: Optional[DeepSeekClient] = None,
                 extractor: Optional[Extractor] = None):
        self.config = config
        raw = config.raw_config.get("router") or {}
        self.enabled = bool(raw.get("enabled", False))
        self.action = raw.get("action", "move")
        if self.action not in ("move", "copy"):
            self.action = "move"

        # Targets: config only — nothing is hardcoded. A category with
        # no target in the config gets an empty path, and _apply_entry
        # reports it as "no target folder configured" instead of moving
        # the file somewhere surprising.
        resolved_targets = raw.get("target_folders") or {}
        self.targets = {}
        for cat in CATEGORIES:
            val = resolved_targets.get(cat) or ""
            self.targets[cat] = Path(
                config._resolve_path(val) if val else ""
            ).expanduser()

        self.route_roots = [
            Path(config._resolve_path(p)).expanduser()
            for p in (raw.get("route_roots") or [])
        ]
        self.watch_folders = [
            Path(config._resolve_path(p)).expanduser()
            for p in (raw.get("watch_folders") or [])
        ]
        self.watch_on_cycles = bool(raw.get("watch_on_cycles", True))

        # Precompute resolved paths so _excluded stays cheap per file.
        # Empty targets are skipped: Path("").resolve() is the CWD, which
        # would make every file under it look "already placed".
        self._resolved_targets = {}
        for cat, target in self.targets.items():
            if str(target) in ("", "."):
                continue
            try:
                self._resolved_targets[cat] = target.resolve()
            except OSError:
                self._resolved_targets[cat] = target
        self._sweep_roots = set()
        for root in self.route_roots + self.watch_folders:
            try:
                self._sweep_roots.add(root.resolve())
            except OSError:
                self._sweep_roots.add(root)

        ds_cfg = raw.get("deepseek") or {}
        self.ds_enabled = bool(ds_cfg.get("enabled", True))
        self.ds_media_enabled = bool(ds_cfg.get("media_enabled", False))
        self.min_confidence = float(ds_cfg.get("min_confidence", 0.6))
        self.batch_size = int(ds_cfg.get("batch_size", 20) or 20)
        self.max_ds_calls = int(ds_cfg.get("max_calls_per_run", 50) or 0)
        self.max_chars = int(ds_cfg.get("max_chars_per_file", 1500) or 1500)
        if deepseek is not None:
            self.ds = deepseek
        elif self.ds_enabled:
            self.ds = DeepSeekClient(
                api_key_path=config._resolve_path(
                    ds_cfg.get("api_key_path", "") or ""
                ),
                model=ds_cfg.get("model", "deepseek-chat"),
                timeout_seconds=int(ds_cfg.get("timeout_seconds", 90) or 90),
            )
        else:
            self.ds = None

        ocr_cfg = raw.get("ocr") or {}
        self.ocr_enabled = bool(ocr_cfg.get("enabled", True))
        self.ocr_budget = int(ocr_cfg.get("budget_per_run", 300) or 0)

        self.max_extract_bytes = int(raw.get("max_extract_bytes", 262144))
        self.min_file_size = int(raw.get("min_file_size", 16) or 0)
        self.max_file_size = int(raw.get("max_file_size", 262144000))
        if extractor is not None:
            self.extractor = extractor
        else:
            self.extractor = Extractor(max_extract_bytes=self.max_extract_bytes)

        # Keywords: built-in tiers + config additions.
        kw = raw.get("keywords") or {}
        self._kw = {
            "political": self._compile(
                _POLITICAL_STRONG_WORDS + list(kw.get("political", [])),
                _POLITICAL_STRONG_PHRASES,
                _POLITICAL_WEAK_WORDS,
            ),
            "ml": self._compile(
                _ML_STRONG_WORDS + list(kw.get("ml", [])),
                _ML_STRONG_PHRASES,
                _ML_WEAK_WORDS,
            ),
            "development": self._compile(
                _DEV_STRONG_WORDS + list(kw.get("development", [])),
                _DEV_STRONG_PHRASES,
                [],
            ),
        }
        self._docname_re = [re.compile(rf"\b{re.escape(w)}\b", re.I)
                            for w in _DOCNAME_WORDS]

        # State.
        state_path = raw.get("state_file") or ""
        if state_path:
            self.state_path = Path(config._resolve_path(state_path)).expanduser()
        else:
            self.state_path = find_project_root() / "router_state.json"
        self.state = self._load_state()
        self._project_root = find_project_root()

        # Per-run bookkeeping.
        self._ds_calls = 0
        self._ocr_used = 0
        self._ocr_queue: list = []      # (path, entry)
        self._text_queue: list = []     # (path, entry) with extracted text
        self._ds_queue: list = []       # (path, entry, text, meta)
        self.stats = self._empty_stats()
        self._warned_perms: set = set()

    # ── keywords ─────────────────────────────────────────────────────

    @staticmethod
    def _compile(strong_words, strong_phrases, weak_words):
        return {
            "strong_words": [re.compile(rf"\b{re.escape(w)}\b", re.I)
                             for w in strong_words if w],
            "strong_phrases": [p.lower() for p in strong_phrases if p],
            "weak_words": [re.compile(rf"\b{re.escape(w)}\b", re.I)
                           for w in weak_words if w],
        }

    def _scene_signal(self, classifications: str) -> tuple[bool, list]:
        """(hit?, labels) — do the Vision scene labels look political?"""
        if not classifications:
            return False, []
        lower = classifications.lower()
        hits = [w for w in _POLITICAL_SCENE_WORDS if w in lower]
        return bool(hits), hits

    def _signal(self, stem_text: str, key: str) -> tuple[bool, list]:
        """(hit?, matched tokens) — one strong hit or two weak hits."""
        spec = self._kw[key]
        text = stem_text.lower()
        hits = []
        for rx in spec["strong_words"]:
            if rx.search(text):
                hits.append(rx.pattern)
        for phrase in spec["strong_phrases"]:
            if phrase in text:
                hits.append(phrase)
        if hits:
            return True, hits
        weak = []
        for rx in spec["weak_words"]:
            if rx.search(text):
                weak.append(rx.pattern)
        if len(set(weak)) >= 2:
            return True, weak
        return False, hits + weak

    # ── state ────────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": STATE_VERSION, "entries": {}}
        if not isinstance(data, dict):
            return {"version": STATE_VERSION, "entries": {}}
        return {
            "version": STATE_VERSION,
            "entries": data.get("entries") if isinstance(
                data.get("entries"), dict) else {},
        }

    def _save_state(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
            tmp.replace(self.state_path)
        except OSError as e:
            logger.warning("router: could not save state %s: %s",
                           self.state_path, e)

    # ── scanning / exclusion ─────────────────────────────────────────

    def _excluded(self, p: Path) -> str:
        """Return a skip reason, or '' when the file may be routed."""
        if p.is_symlink():
            return "symlink"
        name = p.name
        if name.startswith("."):
            return "hidden"
        lower = name.lower()
        if lower.endswith(_IN_PROGRESS_SUFFIXES):
            return "in-progress download"
        for pat in self.config.raw_config.get("exclude_patterns", []):
            if fnmatch.fnmatch(name, pat):
                return f"excluded ({pat})"
        try:
            resolved = p.resolve()
        except OSError:
            return "unresolvable"
        in_sweep = resolved in self._sweep_roots or any(
            r in resolved.parents for r in self._sweep_roots)
        for cat, target in self.targets.items():
            t = self._resolved_targets.get(cat)
            if t is None or t not in resolved.parents:
                continue
            if in_sweep:
                # A target bucket that doubles as a sweep root is the
                # inbox (e.g. ~/misc routes into the other buckets).
                # Files there are candidates; _apply_entry keeps them
                # put when the decision lands in the same bucket.
                continue
            return f"already in {cat} ({target})"
        if (self._project_root in resolved.parents
                or resolved == self._project_root):
            return "inside file_organizer"
        return ""

    def _collect(self, roots: list) -> list:
        out = []
        for root in roots:
            if not root.exists():
                if str(root) not in self._warned_perms:
                    logger.warning("router: root missing or unreadable: %s",
                                   root)
                    self._warned_perms.add(str(root))
                continue
            try:
                for p in root.rglob("*"):
                    if not p.is_file():
                        continue
                    reason = self._excluded(p)
                    if reason:
                        continue
                    out.append(p)
            except PermissionError:
                if str(root) not in self._warned_perms:
                    logger.warning(
                        "router: no permission to read %s — grant Full "
                        "Disk Access to your terminal and re-run", root)
                    self._warned_perms.add(str(root))
        out.sort(key=lambda p: (p.stat().st_size if p.exists() else 0))
        return out

    # ── decisions ────────────────────────────────────────────────────

    @staticmethod
    def _empty_stats() -> dict:
        return {
            "scanned": 0, "decided": 0, "moved": 0, "copied": 0,
            "duplicates": 0, "collisions": 0, "errors": 0, "skipped": 0,
            "ocr_used": 0, "deepseek_calls": 0,
            "by_category": {c: 0 for c in CATEGORIES},
            "by_source": {"extension": 0, "filename": 0, "content": 0,
                          "deepseek": 0, "fallback": 0},
        }

    def _entry(self, p: Path) -> dict:
        """Get or create the state entry for p (keyed by origin path)."""
        key = str(p)
        entry = self.state["entries"].get(key)
        if not isinstance(entry, dict):
            entry = {}
        try:
            st = p.stat()
            size, mtime = st.st_size, st.st_mtime_ns
        except OSError:
            size, mtime = 0, 0
        entry.setdefault("name", p.name)
        entry.setdefault("origin", key)
        entry["size"] = size
        entry["mtime_ns"] = mtime
        return entry

    def _decide(self, p: Path, category: str, confidence: float,
                source: str, reason: str, text_hits: list) -> dict:
        entry = self._entry(p)
        entry.update({
            "category": category, "confidence": round(confidence, 3),
            "source": source, "reason": reason[:200],
            "decided_at": time.time(),
            "status": "pending", "applied": False, "dest": "",
        })
        self.state["entries"][str(p)] = entry
        return entry

    def _already_applied(self, p: Path) -> bool:
        entry = self.state["entries"].get(str(p))
        if not isinstance(entry, dict):
            return False
        if entry.get("applied") and entry.get("status") in (
            "moved", "copied", "duplicate_kept",
        ):
            return True
        return False

    def _already_decided(self, p: Path) -> bool:
        """True when state holds a decision for p and the file hasn't
        changed since — re-runs then skip it without re-extracting."""
        entry = self.state["entries"].get(str(p))
        if not isinstance(entry, dict) or not entry.get("category"):
            return False
        try:
            st = p.stat()
        except OSError:
            return False
        return (entry.get("size") == st.st_size and
                entry.get("mtime_ns") == st.st_mtime_ns)

    # ── phase 1: extension + filename ────────────────────────────────

    def _phase1(self, p: Path) -> Optional[dict]:
        """Cheap decisions. Returns the decision entry, or None when the
        file needs content extraction / DeepSeek."""
        ext = p.suffix.lower()
        stem_text = _lower_stem(p.stem)
        self.stats["scanned"] += 1

        if ext in MUSIC_EXTS:
            return self._decide(p, "music", 1.0, "extension", "audio file", [])

        is_media = ext in IMAGE_EXTS or ext in VIDEO_EXTS
        is_code = ext in CODE_EXTS

        if is_media:
            # Media: only political names (or doc-like names) matter here.
            pol, hits = self._signal(stem_text, "political")
            if pol:
                return self._decide(p, "political", 0.9, "filename",
                                    "political name: " + ", ".join(hits[:4]),
                                    hits)
            if any(rx.search(stem_text) for rx in self._docname_re):
                return self._decide(p, "documents", 0.8, "filename",
                                    "picture of a document (name)", hits)
            return None  # -> OCR queue, else fallback pictures

        if is_code:
            ml, hits = self._signal(stem_text, "ml")
            if ml:
                return self._decide(p, "ml_development", 0.9, "filename",
                                    "ML name: " + ", ".join(hits[:4]), hits)
            dev, dhits = self._signal(stem_text, "development")
            if dev:
                return self._decide(p, "development", 0.9, "filename",
                                    "dev name: " + ", ".join(dhits[:4]), hits)
            return self._decide(p, "development", 0.7, "extension",
                                "code file", [])

        # Text-bearing files.
        pol, hits = self._signal(stem_text, "political")
        if pol:
            return self._decide(p, "political", 0.9, "filename",
                                "political name: " + ", ".join(hits[:4]), hits)
        ml, hits = self._signal(stem_text, "ml")
        if ml:
            return self._decide(p, "ml_development", 0.9, "filename",
                                "ML name: " + ", ".join(hits[:4]), hits)
        dev, hits = self._signal(stem_text, "development")
        if dev:
            return self._decide(p, "development", 0.9, "filename",
                                "dev name: " + ", ".join(hits[:4]), hits)

        if p.stat().st_size > self.max_file_size:
            # Too big for content extraction: decide by extension only.
            return self._decide(p, *self._fallback(p, "too large for "
                                                   "content analysis"), [])
        return None

    # ── phase 2: content extraction ──────────────────────────────────

    def _phase2(self, p: Path, entry: dict) -> Optional[dict]:
        """Extract text where cheap/needed; queue OCR + DeepSeek."""
        ext = p.suffix.lower()

        if ext in IMAGE_EXTS or ext in VIDEO_EXTS:
            # Media is never read as bytes: Vision scene labels can decide
            # even for tiny files, so the min_file_size guard doesn't apply.
            if self.ocr_enabled and self._ocr_used < self.ocr_budget:
                self._ocr_queue.append((p, entry))
                return None
            return self._decide(
                p, *self._fallback(p, "media (OCR off or budget spent)"),
                [])

        if p.stat().st_size < self.min_file_size:
            return self._decide(p, *self._fallback(p, "tiny file"), [])

        if ext == ".pdf":
            # PDF text extraction is cheap on macOS (PDFKit); OCR only for
            # scanned PDFs — counted against the OCR budget.
            data = self.extractor.extract(p)
            if data["text"]:
                return self._content_decision(p, entry, data["text"])
            if self.ocr_enabled and self._ocr_used < self.ocr_budget:
                self._ocr_queue.append((p, entry))
                return None
            return self._decide(
                p, *self._fallback(p, "no readable text in PDF"), [])

        if ext in TEXT_EXTS or ext in HTML_EXTS or ext in OFFICE_EXTS:
            data = self.extractor.extract(p)
            return self._content_decision(p, entry, data["text"])

        return self._decide(p, *self._fallback(p, "no content to read"), [])

    def _content_decision(self, p: Path, entry: dict,
                          text: str) -> Optional[dict]:
        """Keyword signals on extracted text; else queue for DeepSeek."""
        sample = " ".join(text.split())[: self.max_chars]
        if sample:
            pol, hits = self._signal(sample, "political")
            if pol:
                return self._decide(p, "political", 0.85, "content",
                                    "political content: " +
                                    ", ".join(hits[:4]), hits)
            ml, hits = self._signal(sample, "ml")
            if ml:
                return self._decide(p, "ml_development", 0.85, "content",
                                    "ML content: " + ", ".join(hits[:4]),
                                    hits)
            dev, hits = self._signal(sample, "development")
            if dev:
                return self._decide(p, "development", 0.85, "content",
                                    "dev content: " + ", ".join(hits[:4]),
                                    hits)
        # Undecided: DeepSeek if it has something to look at.
        if sample and self.ds is not None and self.ds.api_key:
            self._ds_queue.append((p, entry, sample, {}))
            return None
        return self._decide(p, *self._fallback(p, "no signal in content"),
                            [])

    def _phase2_ocr(self) -> None:
        """OCR budgeted media/PDFs; political signals decide, the rest
        fall back by type. Whatever the budget didn't cover is settled
        by local rules so no file stays unclassified."""
        processed = 0
        for p, entry in self._ocr_queue:
            if self._ocr_used >= self.ocr_budget:
                break
            self._ocr_used += 1
            processed += 1
            data = self.extractor.extract(p)
            text = " ".join(data["text"].split())[: self.max_chars]
            pol, hits = self._signal(text, "political")
            if pol:
                self._decide(p, "political", 0.85, "content",
                             "OCR political text: " + ", ".join(hits[:4]),
                             hits)
                continue
            # Scene labels: a political-looking photo/video with no text.
            scene, labels = self._scene_signal(
                str(data.get("meta", {}).get("classifications", ""))
            )
            if scene and p.suffix.lower() in IMAGE_EXTS | VIDEO_EXTS:
                self._decide(p, "political", 0.7, "content",
                             "political scene: " + ", ".join(labels[:4]),
                             labels)
                continue
            ml, hits = self._signal(text, "ml")
            if ml and p.suffix.lower() == ".pdf":
                self._decide(p, "ml_development", 0.85, "content",
                             "OCR ML text: " + ", ".join(hits[:4]), hits)
                continue
            # Media with OCR text DeepSeek can read when enabled.
            if (text and self.ds_media_enabled and self.ds is not None
                    and self.ds.api_key):
                self._ds_queue.append((p, entry, text, data.get("meta", {})))
                continue
            self._decide(p, *self._fallback(p, "no signal in OCR text"), [])
        for p, entry in self._ocr_queue[processed:]:
            self._decide(p, *self._fallback(p, "OCR budget exhausted"), [])

    # ── phase 4: DeepSeek batches ────────────────────────────────────

    def _phase3_deepseek(self) -> None:
        if not self._ds_queue or self.ds is None:
            return
        processed = 0
        for i in range(0, len(self._ds_queue), self.batch_size):
            if self._ds_calls >= self.max_ds_calls:
                logger.info("router: DeepSeek budget reached "
                            "(%d calls); remainder decided by rules",
                            self.max_ds_calls)
                break
            batch = self._ds_queue[i:i + self.batch_size]
            self._ds_calls += 1
            items = []
            for p, _, text, meta in batch:
                kind = ("media" if p.suffix.lower()
                        in IMAGE_EXTS | VIDEO_EXTS else "file")
                scene = str(meta.get("classifications", "")) if meta else ""
                combined = text
                if scene:
                    combined = (f"[scene labels: {scene}] " + text).strip()
                items.append({"name": p.name, "kind": kind,
                              "text": combined})
            results = self.ds.classify_batch(items)
            for j, (p, entry, text, _meta) in enumerate(batch):
                res = results[j] if j < len(results) else {}
                if (res.get("category")
                        and res.get("confidence", 0) >= self.min_confidence):
                    self._decide(
                        p, res["category"], res["confidence"],
                        "deepseek", res.get("reason", ""), [])
                else:
                    self._decide(
                        p, *self._fallback(p, "DeepSeek unsure or failed"),
                        [])
            processed = i + len(batch)
        # Whatever the budget left undecided is settled by local rules so
        # no file stays unclassified.
        for p, entry, _text, _meta in self._ds_queue[processed:]:
            self._decide(p, *self._fallback(p, "DeepSeek budget exhausted"),
                         [])

    # ── phase 5: fallback ────────────────────────────────────────────

    def _fallback(self, p: Path, reason: str) -> tuple[str, float, str, str]:
        ext = p.suffix.lower()
        note = f" ({reason})" if reason else ""
        if ext in MUSIC_EXTS:
            return "music", 0.6, "fallback", "audio extension" + note
        if ext in IMAGE_EXTS or ext in VIDEO_EXTS:
            return "pictures", 0.6, "fallback", \
                "media without political signal" + note
        if ext in CODE_EXTS:
            return "development", 0.6, "fallback", "code extension" + note
        if (ext in DOC_EXTS or ext in TEXT_EXTS or ext in HTML_EXTS
                or ext == ".pdf"):
            return "documents", 0.6, "fallback", "document extension" + note
        return "misc", 0.6, "fallback", f"{reason or 'unknown type'}"

    # ── apply (move/copy) ────────────────────────────────────────────

    def _files_equal(self, a: Path, b: Path) -> bool:
        try:
            if a.stat().st_size != b.stat().st_size:
                return False
            ha, hb = hashlib.sha256(), hashlib.sha256()
            for fa, fb in ((a, ha), (b, hb)):
                with open(fa, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        fb.update(chunk)
                        if fa.stat().st_size > (64 << 20):
                            break  # compare first 64 MB only
            return ha.digest() == hb.digest()
        except OSError:
            return False

    def _apply_entry(self, entry: dict) -> None:
        # run() only sends pending/undone/error entries here; applied
        # ones (moved, copied, already_there, duplicate_kept) never are.
        if entry.get("applied"):
            return
        p = Path(entry["origin"])
        if not p.exists() or p.is_symlink():
            entry["status"] = "gone"
            return
        category = entry.get("category", "misc")
        target = self.targets.get(category)
        if not target or str(target) in ("", "."):
            entry["status"] = "error"
            entry["reason"] = "no target folder configured"
            return
        dest = target / entry.get("name", p.name)
        if dest == p:
            entry["status"] = "already_there"
            entry["applied"] = True
            return
        if dest.exists():
            if dest.stat().st_size == p.stat().st_size and \
                    self._files_equal(dest, p):
                logger.info("router: duplicate kept in place (same content "
                            "as %s): %s", dest, p)
                entry["status"] = "duplicate_kept"
                entry["applied"] = True
                entry["dest"] = str(dest)
                self.stats["duplicates"] += 1
                return
            logger.warning("router: NOT overwriting %s with %s — different "
                           "content; source left in place", dest, p)
            entry["status"] = "collision"
            entry["dest"] = str(dest)
            self.stats["collisions"] += 1
            return
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if self.action == "copy":
                shutil.copy2(p, dest)
            else:
                shutil.move(str(p), str(dest))
        except OSError as e:
            logger.error("router: could not move %s -> %s: %s", p, dest, e)
            entry["status"] = "error"
            entry["reason"] = str(e)[:200]
            self.stats["errors"] += 1
            return
        entry["applied"] = True
        entry["dest"] = str(dest)
        entry["status"] = "moved" if self.action == "move" else "copied"
        self.stats["moved" if self.action == "move" else "copied"] += 1
        self.stats["by_category"][category] += 1
        logger.info("router: %s -> %s (%s)", p.name, dest,
                    entry.get("reason") or entry.get("source", ""))

    # ── public API ───────────────────────────────────────────────────

    def run(self, roots: Optional[list] = None, apply: bool = False,
            limit: Optional[int] = None, watch_only: bool = False) -> dict:
        """One routing pass. Returns stats. Safe: nothing moves unless
        apply=True."""
        if not self.enabled:
            logger.info("router: disabled in config")
            return self._empty_stats()
        roots = roots or (self.watch_folders if watch_only else self.route_roots)
        self.stats = self._empty_stats()
        self._ocr_used = 0
        self._ds_calls = 0

        undecided = []
        for p in self._collect(roots):
            if self._already_applied(p) or self._already_decided(p):
                continue
            decision = self._phase1(p)
            if decision is None:
                undecided.append(p)
            if limit and self.stats["scanned"] >= limit:
                break

        # Phase 2: content extraction for undecided files.
        self._ocr_queue = []
        self._text_queue = []
        self._ds_queue = []
        for p in undecided:
            entry = self._entry(p)
            self._phase2(p, entry)

        self._phase2_ocr()
        self._phase3_deepseek()
        self.stats["ocr_used"] = self._ocr_used
        self.stats["deepseek_calls"] = self._ds_calls

        # Apply moves when apply=True: pending decisions from this and
        # earlier dry-runs, moves that route-undo put back (undone), and
        # earlier failures (error) so a retry can succeed.
        if apply:
            for key, entry in list(self.state["entries"].items()):
                if entry.get("status") in ("pending", "undone", "error") \
                        and not entry.get("applied"):
                    self._apply_entry(entry)

        self.stats["decided"] = sum(
            1 for e in self.state["entries"].values()
            if e.get("category") and e.get("status") not in (
                "gone", "already_there")
        )
        self._save_state()
        logger.debug("router stats: %s", self.stats)
        return self.stats

    def watch_pass(self, apply: bool = False) -> dict:
        """Route new files in watch_folders (Downloads/Desktop)."""
        if not self.watch_folders:
            return self._empty_stats()
        return self.run(roots=self.watch_folders, apply=apply,
                        watch_only=True)

    def undo(self, count: Optional[int] = None) -> list:
        """Put the most recently routed files back where they came from."""
        entries = [
            (k, e) for k, e in self.state["entries"].items()
            if e.get("applied") and e.get("dest")
            and e.get("status") in ("moved", "copied")
        ]
        entries.sort(key=lambda kv: kv[1].get("decided_at", 0), reverse=True)
        if count:
            entries = entries[:count]
        undone = []
        for key, entry in entries:
            dest = Path(entry["dest"])
            origin = Path(entry["origin"])
            if not dest.exists():
                continue
            try:
                origin.parent.mkdir(parents=True, exist_ok=True)
                if origin.exists():
                    logger.warning("router undo: %s exists again — leaving "
                                   "%s in place", origin, dest)
                    continue
                shutil.move(str(dest), str(origin))
            except OSError as e:
                logger.error("router undo: %s -> %s failed: %s",
                             dest, origin, e)
                continue
            entry["status"] = "undone"
            entry["applied"] = False
            undone.append(f"{dest} -> {origin}")
            logger.info("router undo: %s -> %s", dest, origin)
        self._save_state()
        return undone

    def status(self) -> dict:
        """Aggregate state for `route --status`."""
        by_category = {c: 0 for c in CATEGORIES}
        by_status = {}
        pending = 0
        for entry in self.state["entries"].values():
            cat = entry.get("category")
            if cat in by_category:
                by_category[cat] += 1
            status = entry.get("status", "?")
            by_status[status] = by_status.get(status, 0) + 1
            if status == "pending":
                pending += 1
        return {
            "state_file": str(self.state_path),
            "total_decisions": len(self.state["entries"]),
            "pending": pending,
            "by_category": by_category,
            "by_status": by_status,
            "targets": {c: str(t) for c, t in self.targets.items()},
            "watch_folders": [str(f) for f in self.watch_folders],
            "route_roots": [str(r) for r in self.route_roots],
        }

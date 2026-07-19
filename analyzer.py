""" Content analyzer: extracts meaningful keywords from filenames and file contents.

Avoids trivial common words ("ready", "wrote", "that") using:
- Minimum word length filter (default 5 chars)
- Stopwords list
- Frequency-based filtering across the corpus
- Optional NLP for named entity recognition
"""

import re
import logging
from pathlib import Path
from collections import Counter
from typing import Optional
from .common_words import COMMON_ENGLISH_WORDS

logger = logging.getLogger(__name__)

# Extended stopwords — common English words that should never be categories
_STOPWORDS: set[str] = {
    # Articles, pronouns, prepositions
    "the", "and", "for", "that", "this", "with", "from", "have", "were",
    "their", "what", "when", "which", "where", "about", "each", "been",
    "would", "could", "there", "they", "will", "shall", "should", "these",
    "those", "other", "being", "into", "over", "under", "after", "before",
    "between", "through", "during", "above", "below",
    # Common verbs
    "have", "will", "would", "could", "should", "might", "shall", "made",
    "make", "said", "like", "come", "take", "know", "think", "want", "look",
    "need", "feel", "seem", "give", "find", "tell", "work", "call", "show",
    "keep", "hold", "bring", "leave", "write", "read", "hear", "move",
    "wrote", "ready", "done", "going", "doing", "thing",
    # Common adjectives
    "good", "great", "nice", "right", "wrong", "first", "last", "next",
    "much", "many", "more", "less", "some", "only", "very", "just", "also",
    "most", "real", "same", "able", "sure", "best", "better",
    # Common nouns
    "time", "year", "people", "thing", "world", "life", "part", "place",
    "case", "week", "company", "number", "group", "file", "files", "name",
    "page", "image", "data", "text", "content", "title", "email", "photo",
    "today", "yesterday", "tomorrow",
    # Technical/file related
    "download", "upload", "screenshot", "screen", "image", "images",
    "document", "video", "audio", "music", "photo", "picture",
    "original", "copy", "final", "draft", "version", "updated", "edited",
    "combined", "merged", "fixed", "added", "removed",
    # Date/time
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday",
}

# Words that ARE meaningful even if below min_length
_WHITELIST_SHORT: set[str] = {
    "nazi", "isis", "ciam", "nsa", "fbi", "cia", "mi6", "kgb",
    "gaza", "iran", "iraq", "mali", "peru", "cuba", "chad",
    "kurd", "moss", "nato", "unix", "java", "ruby", "rust",
    "goog", "meta", "xorg", "gimp",
}


class ContentAnalyzer:
    """Extracts meaningful keywords from file paths and contents."""

    def __init__(self, config):
        self.config = config
        ml = config.raw_config.get("ml_content_analysis", {})
        self._min_word_length: int = ml.get("min_word_length", 5)
        self._min_keyword_frequency: int = ml.get("min_keyword_frequency", 10)
        self._min_category_size: int = ml.get("min_category_size", 5)
        self._max_categories: int = min(ml.get("max_categories", 50), 200)
        self._use_clip: bool = ml.get("use_clip", False)
        self._stopwords_enabled: bool = ml.get("stop_words_enabled", True)

        # Global frequency counter for corpus-level filtering
        self._global_freq: Counter = Counter()

    def extract_keywords_from_text(self, text: str) -> list[str]:
        """Extract meaningful keywords from a text string."""
        if not text:
            return []

        # Normalize: lowercase, split on non-alpha
        words = re.findall(r"[a-z]{2,}", text.lower())
        keywords = []

        for w in words:
            # Length filter (unless whitelisted)
            if len(w) < self._min_word_length and w not in _WHITELIST_SHORT:
                continue

            # Stopwords filter
            if self._stopwords_enabled and w in _STOPWORDS:
                continue

            keywords.append(w)

        # Deduplicate while preserving order
        seen = set()
        result = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                result.append(kw)

        return result

    def extract_keywords_from_filename(self, filepath: Path) -> list[str]:
        """Extract keywords from a filename stem."""
        stem = filepath.stem  # filename without extension

        # Replace common separators with spaces
        stem = re.sub(r"[-_.,;:!?()\[\]{}]+", " ", stem)
        # Split camelCase and PascalCase
        stem = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)
        # Split on numbers
        stem = re.sub(r"(\d+)", r" \1 ", stem)

        return self.extract_keywords_from_text(stem)

    def extract_keywords_from_content(
        self, filepath: Path, max_bytes: int = 65536
    ) -> list[str]:
        """Extract keywords from file contents (text files only)."""
        # Check if it's a text file
        ext = filepath.suffix.lower()
        text_exts = {
            ".txt", ".md", ".rst", ".py", ".js", ".ts", ".html", ".htm",
            ".css", ".scss", ".sass", ".json", ".xml", ".yaml", ".yml",
            ".csv", ".tsv", ".rb", ".php", ".java", ".c", ".cpp", ".h",
            ".hpp", ".rs", ".go", ".swift", ".kt", ".sh", ".bash", ".zsh",
            ".ex", ".exs", ".erl", ".hrl", ".clj", ".cljs", ".edn",
            ".r", ".rmd", ".tex", ".bib", ".org", ".el", ".lisp",
            ".scm", ".hs", ".ml", ".mli", ".nim", ".zig", ".odin",
            ".toml", ".cfg", ".ini", ".conf", ".env", ".lock",
        }

        if ext not in text_exts and ext not in {".pdf", ".docx", ".odt", ".rtf"}:
            # Not a text file we can read
            return []

        try:
            content = filepath.read_text(encoding="utf-8", errors="ignore")[:max_bytes]
            return self.extract_keywords_from_text(content)
        except (OSError, UnicodeDecodeError):
            return []

    def analyze_file(self, filepath: Path) -> list[str]:
        """Extract keywords from both filename and content."""
        kw_filename = self.extract_keywords_from_filename(filepath)

        # Content analysis is optional and slow
        if self.config.raw_config.get("enable_content_analysis", False):
            kw_content = self.extract_keywords_from_content(filepath)
        else:
            kw_content = []

        # Combine: filename keywords first (higher priority), then content
        combined = kw_filename.copy()
        for kw in kw_content:
            if kw not in combined:
                combined.append(kw)

        return combined

    def build_category_frequencies(
        self, file_keywords: list[tuple[Path, list[str]]]
    ) -> Counter:
        """Build global keyword frequencies across all files."""
        self._global_freq.clear()
        for _, keywords in file_keywords:
            for kw in set(keywords):  # count each keyword once per file
                self._global_freq[kw] += 1
        return self._global_freq

    def filter_meaningful_categories(self, freq: Counter) -> list[str]:
        """Filter keywords to only those that are meaningful as categories."""
        categories = []

        for kw, count in freq.most_common():
            if count < self._min_keyword_frequency:
                continue
            if len(categories) >= self._max_categories:
                break
            categories.append(kw)

        return categories

    def categorize_file(
        self, filepath: Path, categories: list[str]
    ) -> list[str]:
        """Determine which categories a file belongs to."""
        keywords = self.analyze_file(filepath)
        matched = [cat for cat in categories if cat in keywords]
        return matched

"""extractor.py — text + metadata extraction for the router.

The router needs TEXT to decide where files go. This module gets it
without third-party dependencies:

  text files        -> read directly (byte-capped)
  .html/.htm        -> tags stripped locally
  .docx/.doc/.pages/.rtf/.odt -> macOS textutil (cross-platform: minimal
                       docx/rtf parsing in pure Python)
  .pdf              -> macOS PDFKit via the compiled src/tools/extract
                       Swift helper (fallback: pdftotext if installed)
  images            -> macOS Vision OCR via the same Swift helper
  video             -> frame grab + OCR via the same Swift helper

The Swift helper is compiled on first use (cached next to the source) so
the organizer itself stays pure Python.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
_SWIFT_SRC = _TOOLS_DIR / "extract.swift"
_SWIFT_BIN = _TOOLS_DIR / "extract"

# Extensions read directly as text (see also organizer.py _TYPE_MAP).
TEXT_EXTS = {
    ".txt", ".md", ".rst", ".csv", ".tsv", ".tex", ".org", ".log", ".ini",
    ".cfg", ".conf", ".toml", ".yaml", ".yml", ".json", ".xml", ".py",
    ".js", ".ts", ".jsx", ".tsx", ".rb", ".php", ".java", ".c", ".cpp",
    ".h", ".hpp", ".rs", ".go", ".swift", ".kt", ".sh", ".bash", ".zsh",
    ".ex", ".exs", ".erl", ".hrl", ".sql", ".css", ".scss", ".sass",
}
# Office-ish documents that textutil (macOS) can convert.
OFFICE_EXTS = {".docx", ".doc", ".pages", ".rtf", ".odt", ".rtfd"}
IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp", ".heic",
    ".heif", ".webp",
}
VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv",
    ".3gp", ".3g2",
}
HTML_EXTS = {".html", ".htm"}


class _TextFromHTML(HTMLParser):
    """Collect visible text from HTML, skipping script/style blocks."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip_depth:
            self._skip_depth -= 1
        if tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3",
                   "h4", "h5", "h6", "title"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.parts)).strip()


def strip_html(raw: str) -> str:
    parser = _TextFromHTML()
    try:
        parser.feed(raw)
    except Exception:
        pass
    return parser.text()


def _strip_rtf(raw: str) -> str:
    """Very small RTF control-word stripper (Linux fallback)."""
    raw = re.sub(r"\\'[0-9a-fA-F]{2}", " ", raw)
    raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", raw)
    raw = raw.replace("{", " ").replace("}", " ").replace("\\", " ")
    return re.sub(r"\s+", " ", raw).strip()


def _docx_text(path: Path) -> str:
    """Extract text from .docx with stdlib zipfile (Linux fallback)."""
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", "ignore")
    except (OSError, KeyError, zipfile.BadZipFile):
        return ""
    xml = re.sub(r"<w:p[ >]", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return re.sub(r"\s+", " ", xml).strip()


class Extractor:
    """Extract {text, meta} from files using local, dependency-free tools."""

    def __init__(
        self,
        max_extract_bytes: int = 262144,
        ocr_timeout_seconds: int = 90,
        tools_dir: Optional[Path] = None,
    ):
        self.max_extract_bytes = max_extract_bytes
        self.ocr_timeout_seconds = ocr_timeout_seconds
        self.tools_dir = Path(tools_dir) if tools_dir else _TOOLS_DIR
        self._swift_bin: Optional[str] = None
        self._swift_missing = False
        self._cache = {}

    # ── Swift helper (PDF / OCR / video frames) ─────────────────────

    def _swift_tool(self) -> Optional[str]:
        """Compile src/tools/extract.swift on demand; cache the binary."""
        if self._swift_bin:
            return self._swift_bin
        if self._swift_missing:
            return None
        if sys.platform != "darwin" or not shutil.which("swiftc"):
            self._swift_missing = True
            return None
        src = self.tools_dir / "extract.swift"
        bin_path = self.tools_dir / "extract"
        if not src.exists():
            logger.warning("extractor: %s missing — PDF/OCR/video text "
                           "extraction unavailable", src)
            self._swift_missing = True
            return None
        needs_build = (
            not bin_path.exists()
            or src.stat().st_mtime > bin_path.stat().st_mtime
        )
        if needs_build:
            logger.info("extractor: compiling %s (first use)…", src.name)
            try:
                result = subprocess.run(
                    ["swiftc", "-O", str(src), "-o", str(bin_path)],
                    capture_output=True, text=True, timeout=180,
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                logger.warning("extractor: swiftc failed: %s", e)
                self._swift_missing = True
                return None
            if result.returncode != 0:
                logger.warning("extractor: swiftc failed: %s",
                               (result.stderr or "")[:300])
                self._swift_missing = True
                return None
        self._swift_bin = str(bin_path)
        return self._swift_bin

    def _run_swift(self, path: Path) -> dict:
        tool = self._swift_tool()
        if not tool:
            return {"text": "", "meta": {}}
        try:
            result = subprocess.run(
                [tool, str(path)],
                capture_output=True, text=True, timeout=self.ocr_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {"text": "", "meta": {}}
        if result.returncode != 0:
            return {"text": "", "meta": {}}
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"text": "", "meta": {}}
        return {"text": data.get("text", ""), "meta": data.get("meta", {})}

    # ── metadata (macOS Spotlight) ───────────────────────────────────

    def _mdls_meta(self, path: Path) -> dict:
        """Spotlight metadata: title/authors/where-froms (macOS only)."""
        if sys.platform != "darwin" or not shutil.which("mdls"):
            return {}
        try:
            result = subprocess.run(
                ["mdls", "-name", "kMDItemTitle", "-name", "kMDItemAuthors",
                 "-name", "kMDItemWhereFroms", str(path)],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}
        if result.returncode != 0:
            return {}
        meta = {}
        key = None
        for line in result.stdout.splitlines():
            m = re.match(r"^(\S+)\s*=\s*(.*)$", line.strip())
            if m:
                key, value = m.group(1), m.group(2).strip()
                value = value.strip('"')
                if value and value != "(null)":
                    meta[key.split(".")[-1]] = value
        return meta

    # ── main entry ───────────────────────────────────────────────────

    def extract(self, path: Path) -> dict:
        """Return {"text": str, "meta": dict} for a file. Never raises."""
        path = Path(path)
        try:
            key = (str(path), path.stat().st_size, path.stat().st_mtime_ns)
        except OSError:
            return {"text": "", "meta": {}}
        if key in self._cache:
            return self._cache[key]

        result = self._extract_uncached(path)
        if len(self._cache) > 2000:  # keep memory bounded on huge sweeps
            self._cache.clear()
        self._cache[key] = result
        return result

    def _extract_uncached(self, path: Path) -> dict:
        ext = path.suffix.lower()

        if ext in TEXT_EXTS:
            text = self._read_text(path)
            return {"text": text[: self.max_extract_bytes], "meta": {}}

        if ext in HTML_EXTS:
            text = strip_html(self._read_text(path))
            return {"text": text[: self.max_extract_bytes], "meta": {}}

        if ext in OFFICE_EXTS:
            text = self._office_text(path)
            return {"text": text[: self.max_extract_bytes], "meta": {}}

        if ext == ".pdf":
            data = self._run_swift(path)
            if not data["text"] and shutil.which("pdftotext"):
                data["text"] = self._pdftotext(path)
            return {"text": data["text"][: self.max_extract_bytes],
                    "meta": data.get("meta", {})}

        if ext in IMAGE_EXTS:
            data = self._run_swift(path)
            meta = self._mdls_meta(path)
            meta.update(data.get("meta", {}))
            return {"text": data["text"][: self.max_extract_bytes],
                    "meta": meta}

        if ext in VIDEO_EXTS:
            data = self._run_swift(path)
            meta = self._mdls_meta(path)
            meta.update(data.get("meta", {}))
            return {"text": data["text"][: self.max_extract_bytes],
                    "meta": meta}

        # Anything else: filename-only classification.
        return {"text": "", "meta": {}}

    def _read_text(self, path: Path) -> str:
        try:
            raw = path.read_bytes()[: self.max_extract_bytes]
        except OSError:
            return ""
        for encoding in ("utf-8", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "ignore")

    def _office_text(self, path: Path) -> str:
        if sys.platform == "darwin" and shutil.which("textutil"):
            try:
                result = subprocess.run(
                    ["textutil", "-convert", "txt", "-stdout", str(path)],
                    capture_output=True, text=True, timeout=60,
                )
            except (OSError, subprocess.TimeoutExpired):
                result = None
            if result is not None and result.returncode == 0:
                return result.stdout or ""
        # Cross-platform fallbacks.
        if path.suffix.lower() == ".docx":
            return _docx_text(path)
        if path.suffix.lower() == ".rtf":
            return _strip_rtf(self._read_text(path))
        return ""

    def _pdftotext(self, path: Path) -> str:
        try:
            result = subprocess.run(
                ["pdftotext", "-q", str(path), "-"],
                capture_output=True, text=True, timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return result.stdout if result.returncode == 0 else ""

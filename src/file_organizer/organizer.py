""" Organizer: creates and maintains the soft-link tree under ~/organized.

Groups files by:
- Type (documents, images, music, video, code, archives, other)
- Year (from file modification time)
- Content categories (from analyzer)
"""

import os
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional
from collections import defaultdict

logger = logging.getLogger(__name__)

# File type categories by extension
_TYPE_MAP: dict[str, str] = {
    # Documents
    ".pdf": "documents", ".doc": "documents", ".docx": "documents",
    ".odt": "documents", ".rtf": "documents", ".txt": "documents",
    ".md": "documents", ".rst": "documents", ".tex": "documents",
    ".pages": "documents", ".xls": "documents", ".xlsx": "documents",
    ".ods": "documents", ".ppt": "documents", ".pptx": "documents",
    ".odp": "documents", ".csv": "documents", ".tsv": "documents",
    # Images
    ".jpg": "images", ".jpeg": "images", ".png": "images", ".gif": "images",
    ".bmp": "images", ".tiff": "images", ".tif": "images", ".webp": "images",
    ".svg": "images", ".ico": "images", ".heic": "images", ".heif": "images",
    ".raw": "images", ".psd": "images", ".ai": "images",
    # Music
    ".mp3": "music", ".m4a": "music", ".m4p": "music", ".flac": "music",
    ".wav": "music", ".aac": "music", ".ogg": "music", ".wma": "music",
    ".aiff": "music", ".aif": "music", ".opus": "music",
    # Video
    ".mp4": "video", ".m4v": "video", ".mov": "video", ".avi": "video",
    ".mkv": "video", ".webm": "video", ".flv": "video", ".wmv": "video",
    ".3gp": "video", ".3g2": "video",
    # Code
    ".py": "code", ".js": "code", ".ts": "code", ".jsx": "code",
    ".tsx": "code", ".rb": "code", ".php": "code", ".java": "code",
    ".c": "code", ".cpp": "code", ".h": "code", ".hpp": "code",
    ".rs": "code", ".go": "code", ".swift": "code", ".kt": "code",
    ".sh": "code", ".bash": "code", ".zsh": "code", ".fish": "code",
    ".ex": "code", ".exs": "code", ".erl": "code", ".hrl": "code",
    ".clj": "code", ".cljs": "code", ".hs": "code", ".ml": "code",
    ".nim": "code", ".zig": "code", ".odin": "code", ".sql": "code",
    ".html": "code", ".htm": "code", ".css": "code", ".scss": "code",
    ".sass": "code", ".less": "code", ".json": "code", ".xml": "code",
    ".yaml": "code", ".yml": "code", ".toml": "code", ".ini": "code",
    ".cfg": "code", ".conf": "code", ".dockerfile": "code",
    # Archives
    ".zip": "archives", ".tar": "archives", ".gz": "archives",
    ".bz2": "archives", ".xz": "archives", ".7z": "archives",
    ".rar": "archives", ".dmg": "archives", ".iso": "archives",
    ".pkg": "archives", ".deb": "archives", ".rpm": "archives",
}


class Organizer:
    """Creates and maintains the soft-link tree under output_base."""

    def __init__(self, config, analyzer=None):
        self.config = config
        self.analyzer = analyzer
        self._output_base: Optional[Path] = None
        self._max_links_per_file: int = config.raw_config.get(
            "max_soft_links_per_file", 10
        )

    @property
    def output_base(self) -> Path:
        if self._output_base is None:
            self._output_base = Path(
                os.path.expanduser(self.config.get("output_base", "~/organized"))
            )
        return self._output_base

    def _file_type(self, filepath: Path) -> str:
        """Determine the type category for a file."""
        ext = filepath.suffix.lower()
        return _TYPE_MAP.get(ext, "other")

    def _file_year(self, mtime: float) -> str:
        """Get the year from a file's modification time."""
        try:
            return str(datetime.fromtimestamp(mtime).year)
        except (OSError, ValueError):
            return "unknown"

    def _build_link_paths(
        self, filepath: Path, mtime: float, categories: list[str]
    ) -> list[Path]:
        """Build all soft-link paths for a file."""
        links = []
        rel = filepath.name  # just the filename for the link

        # 1. By type
        ftype = self._file_type(filepath)
        links.append(self.output_base / ftype / rel)

        # 2. By year
        year = self._file_year(mtime)
        links.append(self.output_base / year / rel)

        # 3. By content categories
        for cat in categories:
            links.append(self.output_base / cat / rel)

        return links

    def create_links(
        self,
        filepath: Path,
        mtime: float,
        categories: list[str],
        dry_run: bool = False,
    ) -> int:
        """Create soft links for a file. Returns number of links created."""
        link_paths = self._build_link_paths(filepath, mtime, categories)

        # Limit total links per file
        if len(link_paths) > self._max_links_per_file:
            # Prioritize: categories first, then type, then year
            cat_links = [p for p in link_paths if p.parent.name not in
                         set(_TYPE_MAP.values()) | {"unknown"} | {str(y) for y in range(1970, 2100)}]
            type_links = [p for p in link_paths if p.parent.name in _TYPE_MAP.values()]
            year_links = [p for p in link_paths if p.parent.name.isdigit()]
            other_links = [p for p in link_paths if p not in cat_links + type_links + year_links]
            link_paths = (cat_links + type_links + year_links + other_links)[
                :self._max_links_per_file
            ]

        created = 0
        for link_path in link_paths:
            if dry_run:
                created += 1
                continue

            try:
                link_path.parent.mkdir(parents=True, exist_ok=True)

                # Remove existing link if it points elsewhere
                if link_path.is_symlink():
                    link_path.unlink()
                elif link_path.exists():
                    # Don't overwrite real files
                    logger.debug("Not overwriting existing file: %s", link_path)
                    continue

                link_path.symlink_to(filepath)
                created += 1
            except OSError as e:
                logger.warning("Failed to create link %s -> %s: %s",
                               link_path, filepath, e)

        return created

    def clean_orphaned_links(self, valid_files: set[Path]) -> int:
        """Remove soft links in output_base that point to files no longer in source."""
        removed = 0
        if not self.output_base.exists():
            return 0

        for link_path in self.output_base.rglob("*"):
            if not link_path.is_symlink():
                continue
            try:
                target = link_path.resolve()
            except OSError:
                # Broken symlink
                link_path.unlink(missing_ok=True)
                removed += 1
                continue

            if target not in valid_files:
                link_path.unlink(missing_ok=True)
                removed += 1

        # Remove empty directories
        for dirpath in sorted(
            self.output_base.rglob("*"), key=lambda p: -len(str(p))
        ):
            if dirpath.is_dir() and not any(dirpath.iterdir()):
                try:
                    dirpath.rmdir()
                except OSError:
                    pass

        return removed

    def organize(
        self,
        file_entries: list[tuple[Path, float, list[str]]],
        dry_run: bool = False,
    ) -> dict:
        """Run a full organization cycle. Returns stats dict."""
        stats = {"links_created": 0, "files_processed": 0,
                  "orphans_removed": 0, "errors": 0}

        valid_files: set[Path] = set()

        for filepath, mtime, categories in file_entries:
            try:
                created = self.create_links(filepath, mtime, categories, dry_run)
                stats["links_created"] += created
                stats["files_processed"] += 1
                valid_files.add(filepath)
            except Exception as e:
                logger.error("Error organizing %s: %s", filepath, e)
                stats["errors"] += 1

        # Clean orphaned links
        if not dry_run:
            stats["orphans_removed"] = self.clean_orphaned_links(valid_files)

        return stats

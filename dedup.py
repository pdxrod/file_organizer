""" Dedup engine: content-hash based duplicate file detection and removal. """

import hashlib
import logging
import os
from pathlib import Path
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)

# Size threshold: skip hashing files smaller than this (just compare by size)
_MIN_HASH_SIZE = 4096


class DedupEngine:
    """Finds and removes duplicate files based on content hash."""

    def __init__(self, config):
        self.config = config
        self._min_size: int = config.raw_config.get("min_file_size", 1024)
        self._max_size: int = config.raw_config.get("max_file_size", 104857600)

    def _hash_file(self, filepath: Path, partial: bool = True) -> Optional[str]:
        """
        Compute SHA-256 hash of a file.
        If partial=True, only hash first 64KB for large files.
        """
        try:
            size = filepath.stat().st_size
            sha = hashlib.sha256()

            with open(filepath, "rb") as f:
                if partial and size > 65536:
                    # Hash first 64KB for quick comparison
                    sha.update(f.read(65536))
                else:
                    # Read in chunks
                    while chunk := f.read(8192):
                        sha.update(chunk)

            return sha.hexdigest()
        except (OSError, PermissionError) as e:
            logger.debug("Could not hash %s: %s", filepath, e)
            return None

    def find_duplicates(
        self, folders: list[Path], dry_run: bool = True
    ) -> list[dict]:
        """
        Find duplicate files across the given folders.
        Returns list of duplicate groups with info.
        """
        # Phase 1: Group by size
        by_size: dict[int, list[Path]] = defaultdict(list)

        for folder in folders:
            if not folder.exists():
                continue
            for filepath in folder.rglob("*"):
                if not filepath.is_file():
                    continue
                try:
                    size = filepath.stat().st_size
                except OSError:
                    continue

                if size < self._min_size or size > self._max_size:
                    continue

                by_size[size].append(filepath)

        # Phase 2: For sizes with multiple files, hash and compare
        duplicates = []

        for size, files in by_size.items():
            if len(files) < 2:
                continue

            by_hash: dict[str, list[Path]] = defaultdict(list)

            for fp in files:
                # For tiny files, just use size as proxy
                if size < _MIN_HASH_SIZE:
                    h = f"size:{size}"
                else:
                    h = self._hash_file(fp)
                    if h is None:
                        continue

                by_hash[h].append(fp)

            for h, group in by_hash.items():
                if len(group) < 2:
                    continue

                # Sort by mtime: keep the newest
                group.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                keeper = group[0]
                to_remove = group[1:]

                duplicates.append({
                    "hash": h,
                    "size": size,
                    "keeper": keeper,
                    "duplicates": to_remove,
                })

        logger.info(
            "Found %d duplicate groups (%d files to remove)",
            len(duplicates),
            sum(len(g["duplicates"]) for g in duplicates),
        )

        return duplicates

    def remove_duplicates(
        self, duplicates: list[dict], dry_run: bool = True
    ) -> dict:
        """
        Remove duplicate files, keeping the newest copy.
        Returns summary dict.
        """
        summary = {"groups": len(duplicates), "removed": 0, "errors": 0, "bytes_freed": 0}

        for group in duplicates:
            for dup in group["duplicates"]:
                if dry_run:
                    logger.info("[DRY RUN] Would remove: %s", dup)
                    summary["removed"] += 1
                    summary["bytes_freed"] += group["size"]
                    continue

                try:
                    dup.unlink()
                    logger.info("Removed duplicate: %s", dup)
                    summary["removed"] += 1
                    summary["bytes_freed"] += group["size"]
                except OSError as e:
                    logger.error("Failed to remove %s: %s", dup, e)
                    summary["errors"] += 1

        return summary

    def run(self, dry_run: bool = True) -> dict:
        """Full dedup cycle: find and remove duplicates."""
        source_folders = self.config.get("source_folders") or []

        if not source_folders:
            logger.info("No source_folders configured — skipping dedup.")
            return {"groups": 0, "removed": 0, "errors": 0, "bytes_freed": 0}

        folders = [Path(f) for f in source_folders if Path(f).exists()]
        if not folders:
            logger.warning("None of the configured source_folders exist.")
            return {"groups": 0, "removed": 0, "errors": 0, "bytes_freed": 0}

        logger.info(
            "Dedup scanning %d folders%s...",
            len(folders),
            " (DRY RUN)" if dry_run else "",
        )

        duplicates = self.find_duplicates(folders, dry_run=dry_run)
        return self.remove_duplicates(duplicates, dry_run=dry_run)

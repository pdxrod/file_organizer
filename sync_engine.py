""" Folder sync engine: bidirectional + one-way sync using rsync. """

import os
import subprocess
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class SyncEngine:
    """Synchronizes folder pairs bidirectionally or one-way using rsync."""

    def __init__(self, config):
        self.config = config
        self._use_rsync: bool = config.raw_config.get("use_rsync", True)
        self._checksum_mode: str = config.raw_config.get("rsync_checksum_mode", "timestamp")
        self._size_only: bool = config.raw_config.get("rsync_size_only", False)
        self._additional_args: list[str] = config.raw_config.get("rsync_additional_args", []) or []
        self._disable_mmap: bool = config.raw_config.get("rsync_disable_mmap", True)
        self._timeout: int = (config.raw_config.get("sync_timeout_minutes", 60) or 60) * 60
        self._chunk_subfolders: int = config.raw_config.get("sync_chunk_subfolders", 30)
        self._exclude_patterns: list[str] = config.raw_config.get("exclude_patterns", []) or []

    def _build_rsync_args(self, source: Path, target: Path, delete: bool = False) -> list[str]:
        """Build rsync argument list."""
        args = ["rsync", "-av", "--progress"]

        # Checksum mode
        if self._checksum_mode == "checksum":
            args.append("--checksum")
        elif self._size_only:
            args.append("--size-only")

        # Disable mmap for network/cloud drives
        if self._disable_mmap:
            args.append("--no-whole-file")

        # Exclude patterns
        for pat in self._exclude_patterns:
            args.extend(["--exclude", pat])

        # Additional args from config
        args.extend(self._additional_args)

        # Delete extraneous files from target
        if delete:
            args.append("--delete")

        # Source (with trailing slash for contents)
        args.append(f"{source}/")
        # Target
        args.append(str(target))

        return args

    def _run_rsync(self, source: Path, target: Path, delete: bool = False) -> bool:
        """Run rsync from source to target. Returns True on success."""
        args = self._build_rsync_args(source, target, delete)

        # Ensure target directory exists
        target.mkdir(parents=True, exist_ok=True)

        logger.info("Syncing: %s → %s", source, target)
        logger.debug("rsync args: %s", " ".join(args))

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
            if result.returncode == 0:
                logger.debug("Sync complete: %s → %s", source, target)
                return True
            else:
                logger.error("rsync failed (%d): %s", result.returncode, result.stderr[:500])
                return False
        except subprocess.TimeoutExpired:
            logger.error("rsync timed out after %ds: %s → %s", self._timeout, source, target)
            return False
        except FileNotFoundError:
            logger.error("rsync not found — please install rsync")
            return False

    def sync_bidirectional(self, folder_a: Path, folder_b: Path) -> dict:
        """Bidirectional sync: copy new/changed files in both directions."""
        result = {"a_to_b": False, "b_to_a": False}

        # Sync A → B
        result["a_to_b"] = self._run_rsync(folder_a, folder_b)

        # Sync B → A
        result["b_to_a"] = self._run_rsync(folder_b, folder_a)

        return result

    def sync_one_way(self, source: Path, target: Path, delete: bool = False) -> bool:
        """One-way sync: copy new/changed files from source to target only."""
        return self._run_rsync(source, target, delete=delete)

    def run_all_sync_pairs(self) -> dict:
        """Run all configured sync pairs. Returns summary dict."""
        summary = {
            "bidirectional": [],
            "one_way": [],
            "errors": [],
        }

        # Bidirectional sync pairs
        sync_pairs = self.config.raw_config.get("sync_pairs", []) or []
        for pair in sync_pairs:
            folders = pair.get("folders", [])
            if len(folders) != 2:
                logger.warning("Invalid sync_pair (need exactly 2 folders): %s", pair)
                continue

            folder_a = Path(folders[0])
            folder_b = Path(folders[1])

            if not folder_a.exists() or not folder_b.exists():
                missing = [str(f) for f in [folder_a, folder_b] if not f.exists()]
                logger.warning("Skipping sync pair — missing: %s", missing)
                continue

            logger.info("Bidirectional sync: %s ↔ %s", folder_a, folder_b)
            res = self.sync_bidirectional(folder_a, folder_b)
            summary["bidirectional"].append({
                "a": str(folder_a),
                "b": str(folder_b),
                "result": res,
            })

        # One-way sync pairs
        one_way_pairs = self.config.raw_config.get("one_way_pairs", []) or []
        for pair in one_way_pairs:
            folders = pair.get("folders", [])
            if len(folders) != 2:
                logger.warning("Invalid one_way_pair (need exactly 2 folders): %s", pair)
                continue

            source = Path(folders[0])
            target = Path(folders[1])

            if not source.exists():
                logger.warning("Skipping one-way — source missing: %s", source)
                continue

            target.mkdir(parents=True, exist_ok=True)

            logger.info("One-way sync: %s → %s", source, target)
            ok = self.sync_one_way(source, target)
            summary["one_way"].append({
                "source": str(source),
                "target": str(target),
                "ok": ok,
            })
            if not ok:
                summary["errors"].append(f"One-way sync failed: {source} → {target}")

        return summary

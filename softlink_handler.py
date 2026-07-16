""" Soft-link folder handler: manages .git, .venv, node_modules etc. during sync.

PROBLEM: The old approach copied .git to ~/organised and replaced it with a soft link.
This BREAKS git — git commands fail when .git is a symlink.

NEW APPROACH:
1. For .git, .hg, .svn: NEVER softlink them. Exclude from sync entirely.
   Git repos should be backed up via `git push` to a remote, not file sync.
   For local redundancy, use `git clone --mirror` or `git bundle`.

2. For .venv, venv, env, node_modules: Exclude from sync.
   These are reproducible from requirements.txt / package.json / Pipfile.
   Optionally, create a manifest file (pip freeze > requirements.txt) before sync.

3. For __pycache__, .pytest_cache, .mypy_cache, .tox, etc:
   These are build artifacts — handled by empty_folder_patterns (contents deleted).
"""

import os
import shutil
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Folders that MUST NOT be softlinked (would break their tools)
_NEVER_SOFTLINK: set[str] = {
    ".git", ".hg", ".svn", ".cvs",
}

# Folders that are reproducible from lock files
_REPRODUCIBLE: set[str] = {
    ".venv", "venv", "env", ".env",
    "node_modules", ".next", ".nuxt",
}

# Folders that are just build caches — safe to delete
_BUILD_CACHE: set[str] = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".tox",
    ".cache", ".parcel-cache", "coverage", ".nyc_output",
    "elm-stuff", ".elixir_ls", ".stack-work", "_build",
    "deps", "ebin", "dist", "build", "target",
    ".bundle", "vendor/bundle", "priv/static",
    ".gradle", ".m2", "tmp/cache",
}


class SoftlinkHandler:
    """Handles soft-link folder patterns intelligently."""

    def __init__(self, config):
        self.config = config
        self._backup_base: Optional[Path] = None
        backup = config.raw_config.get("softlink_backup_base")
        if backup:
            self._backup_base = Path(os.path.expanduser(backup))

    def get_rsync_exclude_patterns(self) -> list[str]:
        """Return patterns that should be excluded from rsync.

        This is the PRIMARY mechanism: just exclude these folders from sync.
        No softlink replacement needed.
        """
        patterns = []

        # All softlink_folder_patterns become rsync excludes
        raw_patterns = self.config.raw_config.get("softlink_folder_patterns", []) or []
        for pat in raw_patterns:
            # Convert to rsync exclude pattern
            # e.g., ".git" → "--exclude=.git"
            patterns.append(pat)
            # Also exclude nested occurrences
            patterns.append(f"**/{pat}")

        return patterns

    def should_never_softlink(self, folder_name: str) -> bool:
        """Check if this folder should NEVER be softlinked."""
        return folder_name in _NEVER_SOFTLINK

    def is_reproducible(self, folder_name: str) -> bool:
        """Check if this folder can be reproduced from lock files."""
        return folder_name in _REPRODUCIBLE

    def is_build_cache(self, folder_name: str) -> bool:
        """Check if this folder is just a build cache."""
        return folder_name in _BUILD_CACHE

    def backup_and_replace(self, folder: Path) -> bool:
        """
        Backup a folder to ~/organised and replace with soft link.
        Only for folders NOT in _NEVER_SOFTLINK.

        WARNING: This is the LEGACY approach. Prefer excluding from sync instead.
        """
        name = folder.name

        if self.should_never_softlink(name):
            logger.warning(
                "Refusing to softlink %s — this would break %s. "
                "Exclude it from sync instead.",
                folder, name,
            )
            return False

        if not self._backup_base:
            logger.debug("No softlink_backup_base configured — skipping backup.")
            return False

        backup_path = self._backup_base / folder.relative_to(folder.anchor)
        backup_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Move the folder to backup
            shutil.move(str(folder), str(backup_path))
            # Create soft link from original location to backup
            os.symlink(str(backup_path), str(folder))
            logger.info("Backed up and softlinked: %s → %s", folder, backup_path)
            return True
        except OSError as e:
            logger.error("Failed to backup %s: %s", folder, e)
            return False

    def restore_from_backup(self, folder: Path) -> bool:
        """
        Restore a softlinked folder from backup.
        Reverses backup_and_replace.
        """
        if not folder.is_symlink():
            return False

        target = Path(os.readlink(str(folder)))
        if not target.exists():
            logger.warning("Backup target missing: %s", target)
            return False

        try:
            folder.unlink()  # remove the symlink
            shutil.move(str(target), str(folder))
            logger.info("Restored from backup: %s", folder)
            return True
        except OSError as e:
            logger.error("Failed to restore %s: %s", folder, e)
            return False

    def create_reproducible_manifest(self, parent_folder: Path) -> None:
        """
        For folders with .venv or node_modules: ensure a lock file exists
        so the environment can be recreated later.
        """
        # Check for Python venv
        requirements_txt = parent_folder / "requirements.txt"
        pipfile = parent_folder / "Pipfile"
        venv = parent_folder / ".venv" if (parent_folder / ".venv").exists() else parent_folder / "venv"

        if venv.exists() and not requirements_txt.exists() and not pipfile.exists():
            logger.info(
                "Found %s without requirements.txt. "
                "Run: pip freeze > requirements.txt to capture dependencies.",
                venv,
            )

        # Check for Node.js
        package_json = parent_folder / "package.json"
        node_modules = parent_folder / "node_modules"
        if node_modules.exists() and not package_json.exists():
            logger.info(
                "Found node_modules without package.json in %s. "
                "Run: npm init to create one.",
                parent_folder,
            )

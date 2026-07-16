""" File scanner: walks source folders, applies exclude/empty/softlink patterns. """

import os
import fnmatch
import logging
from pathlib import Path
from typing import Iterator, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ScannedFile:
    """A file discovered during scanning."""
    path: Path
    size: int
    mtime: float
    is_symlink: bool
    relative_to: Path  # the source root it was found under


class FileScanner:
    """Scans directories, applying exclude/empty/softlink patterns from config."""

    def __init__(self, config):
        self.config = config
        self._exclude_globs: list[str] = []
        self._empty_patterns: list[str] = []
        self._softlink_patterns: list[str] = []

    def _compile_patterns(self) -> None:
        """Pre-compile pattern lists from config."""
        raw = self.config.raw_config

        self._exclude_globs = raw.get("exclude_patterns", []) or []
        self._empty_patterns = raw.get("empty_folder_patterns", []) or []
        self._softlink_patterns = raw.get("softlink_folder_patterns", []) or []

        # Always exclude the organised backup base
        organised = self.config.get("softlink_backup_base")
        if organised:
            self._exclude_globs.append(os.path.expanduser(organised))

        # Always exclude the organized output base
        output = self.config.get("output_base")
        if output:
            self._exclude_globs.append(os.path.expanduser(output))

    def should_exclude(self, rel_path: str, is_dir: bool = False) -> bool:
        """Check if a relative path should be excluded from scanning."""
        name = os.path.basename(rel_path)

        for pattern in self._exclude_globs:
            # fnmatch against full relative path
            if fnmatch.fnmatch(rel_path, pattern):
                return True
            # fnmatch against just the name
            if fnmatch.fnmatch(name, pattern):
                return True
            # Check if path contains the pattern as a component
            for part in rel_path.split(os.sep):
                if fnmatch.fnmatch(part, pattern):
                    return True

        return False

    def is_empty_folder_pattern(self, name: str) -> bool:
        """Check if a folder name matches empty_folder_patterns."""
        for pattern in self._empty_patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    def is_softlink_folder_pattern(self, name: str) -> bool:
        """Check if a folder name matches softlink_folder_patterns."""
        for pattern in self._softlink_patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    def scan_directory(
        self, root: Path, relative_to: Optional[Path] = None
    ) -> Iterator[tuple[Path, bool]]:
        """
        Walk a directory, yielding (path, is_softlink_target) tuples.
        Directories matching empty_folder_patterns have contents skipped.
        Directories matching softlink_folder_patterns are flagged.
        """
        if relative_to is None:
            relative_to = root

        self._compile_patterns()

        for dirpath_str, dirnames, filenames in os.walk(root, followlinks=False):
            dirpath = Path(dirpath_str)
            rel_dir = str(dirpath.relative_to(relative_to)) if dirpath != relative_to else "."

            # Filter dirnames in-place to skip excluded directories
            new_dirnames = []
            for d in dirnames:
                d_rel = os.path.join(rel_dir, d) if rel_dir != "." else d

                if self.should_exclude(d_rel, is_dir=True):
                    logger.debug("Excluding directory: %s", d_rel)
                    continue

                if self.is_empty_folder_pattern(d):
                    logger.info("Emptying folder contents: %s", dirpath / d)
                    self._empty_folder(dirpath / d)
                    # Still include the folder itself (now empty)
                    new_dirnames.append(d)
                    continue

                if self.is_softlink_folder_pattern(d):
                    logger.debug("Softlink folder found: %s", d_rel)
                    new_dirnames.append(d)
                    continue

                new_dirnames.append(d)

            dirnames[:] = new_dirnames

            # Yield files
            for f in filenames:
                f_rel = os.path.join(rel_dir, f) if rel_dir != "." else f
                if self.should_exclude(f_rel, is_dir=False):
                    continue

                filepath = dirpath / f
                yield (filepath, False)

            # Yield flagged softlink folders
            for d in dirnames:
                d_rel = os.path.join(rel_dir, d) if rel_dir != "." else d
                if self.is_softlink_folder_pattern(d):
                    yield (dirpath / d, True)

    def scan_all_sources(self) -> Iterator[ScannedFile]:
        """Scan all source_folders from config, yielding ScannedFile entries."""
        source_folders = self.config.get("source_folders") or []

        if not source_folders:
            logger.info("No source_folders configured — nothing to scan.")
            return

        for src in source_folders:
            root = Path(src)
            if not root.exists():
                logger.warning("Source folder does not exist: %s", root)
                continue
            if not root.is_dir():
                logger.warning("Source folder is not a directory: %s", root)
                continue

            for filepath, is_softlink_target in self.scan_directory(root, root):
                if is_softlink_target:
                    # This is a directory flagged for softlink handling
                    # Yield all files within it
                    if filepath.is_dir():
                        for sub in filepath.rglob("*"):
                            if sub.is_file() and not self.should_exclude(
                                str(sub.relative_to(root))
                            ):
                                yield ScannedFile(
                                    path=sub,
                                    size=sub.stat().st_size,
                                    mtime=sub.stat().st_mtime,
                                    is_symlink=sub.is_symlink(),
                                    relative_to=root,
                                )
                else:
                    try:
                        st = filepath.stat()
                    except OSError:
                        continue
                    yield ScannedFile(
                        path=filepath,
                        size=st.st_size,
                        mtime=st.st_mtime,
                        is_symlink=filepath.is_symlink(),
                        relative_to=root,
                    )

    def _empty_folder(self, folder: Path) -> None:
        """Delete all contents of a folder, leaving the folder itself."""
        if not folder.exists() or not folder.is_dir():
            return

        for item in folder.iterdir():
            try:
                if item.is_dir() and not item.is_symlink():
                    import shutil
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
            except OSError as e:
                logger.warning("Could not delete %s: %s", item, e)

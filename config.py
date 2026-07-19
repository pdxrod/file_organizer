"""
Config loader with drive resolution, validation, and defaults.

Resolves drive placeholders like MAIN_DRIVE, PROTON_DRIVE (including nested
references) into absolute paths. After resolution, no code should ever see
the literal strings 'MAIN_DRIVE', 'PROTON_DRIVE', etc.
"""

import os
import re
import sys
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── defaults ──────────────────────────────────────────────────────────
DEFAULTS: Dict[str, Any] = {
    "drives": {},
    "sync_pairs": [],
    "one_way_pairs": [],
    "source_folders": [],
    "output_base": "~/organized",
    "softlink_backup_base": "~/organised",
    "exclude_patterns": [
        ".DS_Store", "*.pyc", "*.log",
        ".Spotlight-V100", ".TemporaryItems", ".fseventsd",
        ".DocumentRevisions-V100", ".Trash", ".Trashes", "*_files",
    ],
    "empty_folder_patterns": [
        "node_modules", "_build", "deps", "ebin", "dist", "build",
        "target", ".next", ".nuxt", ".cache", ".parcel-cache",
        "coverage", ".nyc_output", "elm-stuff", ".elixir_ls",
        ".stack-work", ".bundle", "vendor", "bundle", "priv/static",
        ".gradle", ".m2", "tmp/cache", ".tmp*",
    ],
    "softlink_folder_patterns": [
        ".git", ".github", ".vscode", ".idea", ".cursor",
        ".cursorrules", ".cursorignore", ".hg", ".svn", ".cvs",
        "__pycache__", ".pytest_cache", ".mypy_cache",
        ".tox", ".venv", "venv", "env",
    ],
    "min_file_size": 1024,
    "max_file_size": 104857600,  # 100 MB
    "enable_content_analysis": True,
    "enable_duplicate_detection": False,
    "enable_folder_sync": True,
    "scan_interval": 3600,
    "flaky_volume_retries": 3,
    "retry_delay": 5,
    "use_rsync": True,
    "rsync_checksum_mode": "timestamp",
    "rsync_size_only": False,
    "rsync_additional_args": [],
    "max_drive_usage_percent": 90,
    "sync_chunk_subfolders": 30,
    "sync_chunk_concurrency": 1,
    "sync_timeout_minutes": 60,
    "rsync_disable_mmap": True,
    "max_soft_links_per_file": 10,
    "enable_semantic_categories": True,
    "semantic_confidence_threshold": 0.35,
    "ml_content_analysis": {
        "enabled": True,
        "min_keyword_frequency": 10,
        "min_category_size": 5,
        "max_categories": 500,
        "min_word_length": 5,
        "stop_words_enabled": True,
        "use_clip": False,
    },
    "auto_git": False,
    "auto_git_folders": [],
}


class ConfigError(Exception):
    """Configuration error."""


class Config:
    """Loaded and validated configuration with resolved drive paths."""

    def __init__(self, path: Optional[str] = None):
        self._raw: Dict[str, Any] = {}
        self._path = path
        self.drives: Dict[str, str] = {}
        self.data: Dict[str, Any] = {}
        if self._path:
            self.load(self._path)

    @property
    def raw_config(self) -> Dict[str, Any]:
        """Access merged config as a plain dict (backwards compat)."""
        return self.data

    # ── loading ──────────────────────────────────────────────────

    def load(self, path: Optional[str] = None) -> "Config":
        """Load config from yaml file, falling back to defaults."""
        target = path or self._path or "config.yaml"
        self._path = target

        if os.path.exists(target):
            with open(target, "r") as f:
                self._raw = yaml.safe_load(f) or {}
        else:
            self._raw = {}

        # Merge with defaults (user values take precedence)
        merged = dict(DEFAULTS)
        merged.update(self._raw)

        # Resolve drives
        self.drives = self._resolve_drives(merged.get("drives", {}))

        # Resolve all paths that contain drive placeholders
        merged["drives"] = self.drives
        merged["sync_pairs"] = self._resolve_sync_pairs(merged.get("sync_pairs", []))
        merged["one_way_pairs"] = self._resolve_sync_pairs(merged.get("one_way_pairs", []))
        merged["source_folders"] = [
            self._resolve_path(p) for p in merged.get("source_folders", [])
        ]
        merged["output_base"] = self._resolve_path(merged["output_base"])
        merged["softlink_backup_base"] = self._resolve_path(merged.get("softlink_backup_base", "~/organised"))
        merged["auto_git_folders"] = [
            self._resolve_path(p) for p in merged.get("auto_git_folders", [])
        ]

        self.data = merged
        self._validate()
        return self

    # ── drive resolution ─────────────────────────────────────────

    def _resolve_drives(self, raw_drives: Dict[str, str]) -> Dict[str, str]:
        """Resolve drive placeholders, allowing nested references like
        PROTON_DRIVE: MAIN_DRIVE/ProtonDrive."""
        resolved: Dict[str, str] = {}
        max_iterations = 10

        for _ in range(max_iterations):
            changed = False
            for key, value in raw_drives.items():
                if key in resolved:
                    continue
                # Check if value references another drive
                m = re.match(r"^([A-Z_]+)/(.+)$", value)
                if m:
                    ref_key = m.group(1)
                    if ref_key in resolved:
                        resolved[key] = os.path.join(resolved[ref_key], m.group(2))
                        changed = True
                    elif ref_key == key:
                        raise ConfigError(f"Circular drive reference: {key} -> {key}")
                else:
                    # Absolute path or relative to nothing
                    resolved[key] = os.path.expanduser(value)
                    changed = True
            if not changed:
                # Resolve any remaining as-is
                for key, value in raw_drives.items():
                    if key not in resolved:
                        resolved[key] = os.path.expanduser(value)
                break

        return resolved

    def _resolve_path(self, path: str) -> str:
        """Replace MAIN_DRIVE, PROTON_DRIVE etc. with real paths."""
        if not isinstance(path, str):
            return path
        # Expand ~
        path = os.path.expanduser(path)
        # Replace known drives
        for drive_key, drive_path in self.drives.items():
            path = path.replace(drive_key, drive_path)
        return path

    def _resolve_sync_pairs(self, pairs: List[Dict]) -> List[Dict]:
        """Resolve drive placeholders in sync/one_way pair folder lists."""
        resolved = []
        for pair in pairs:
            if "folders" in pair:
                folders = [self._resolve_path(f) for f in pair["folders"]]
                resolved.append({"folders": folders})
            elif "source" in pair and "target" in pair:
                # Legacy format
                resolved.append({
                    "folders": [
                        self._resolve_path(pair["source"]),
                        self._resolve_path(pair["target"]),
                    ]
                })
        return resolved

    # ── validation ───────────────────────────────────────────────

    def _validate(self) -> None:
        """Check config sanity."""
        d = self.data

        # Check output_base
        out = d.get("output_base", "")
        if not out:
            raise ConfigError("output_base must be set")
        if out in ("/", os.path.expanduser("~")):
            raise ConfigError(f"output_base must not be {out} — too dangerous")

        # Check sync pairs have pairs
        for i, pair in enumerate(d.get("sync_pairs", [])):
            folders = pair.get("folders", [])
            if len(folders) != 2:
                raise ConfigError(f"sync_pairs[{i}] must have exactly 2 folders, got {len(folders)}")

        for i, pair in enumerate(d.get("one_way_pairs", [])):
            folders = pair.get("folders", [])
            if len(folders) != 2:
                raise ConfigError(f"one_way_pairs[{i}] must have exactly 2 folders, got {len(folders)}")

        # Check drives exist (warn, don't fail — removable drives come and go)
        for key, path in self.drives.items():
            if not os.path.exists(path):
                print(f"⚠️  Drive '{key}' ({path}) does not exist — will skip when needed", file=sys.stderr)

        # Validate numeric ranges
        for field, low, high in [
            ("min_file_size", 0, 10**9),
            ("max_file_size", 1, 10**12),
            ("scan_interval", 60, 86400 * 30),
            ("max_drive_usage_percent", 50, 100),
            ("max_soft_links_per_file", 1, 1000),
        ]:
            val = d.get(field)
            if val is not None and not (low <= val <= high):
                print(f"⚠️  {field}={val} is outside recommended range [{low}, {high}]", file=sys.stderr)

    # ── accessors ────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __contains__(self, key: str) -> bool:
        return key in self.data

    def is_test_mode(self) -> bool:
        return self.data.get("_test_mode", False)

    def output_path(self) -> str:
        return self.data["output_base"]

    def all_sync_pairs(self) -> List[Tuple[str, str, str]]:
        """Return all sync pairs as list of (source, target, mode).
        mode is 'bidirectional' or 'one_way_source_to_target'."""
        pairs = []
        for pair in self.data.get("sync_pairs", []):
            folders = pair["folders"]
            pairs.append((folders[0], folders[1], "bidirectional"))
        for pair in self.data.get("one_way_pairs", []):
            folders = pair["folders"]
            pairs.append((folders[0], folders[1], "one_way_source_to_target"))
        return pairs

    def is_excluded(self, name: str) -> bool:
        """Check if a file/folder name matches any exclude pattern."""
        import fnmatch
        for pat in self.data.get("exclude_patterns", []):
            if fnmatch.fnmatch(name, pat):
                return True
        return False

    def is_softlink_folder(self, name: str) -> bool:
        """Check if a folder name matches softlink_folder_patterns."""
        import fnmatch
        for pat in self.data.get("softlink_folder_patterns", []):
            if fnmatch.fnmatch(name, pat):
                return True
        return False

    def is_empty_folder(self, name: str) -> bool:
        """Check if a folder name matches empty_folder_patterns."""
        import fnmatch
        for pat in self.data.get("empty_folder_patterns", []):
            if fnmatch.fnmatch(name, pat):
                return True
        return False


def load_config(path: Optional[str] = None) -> Config:
    """Convenience: load and return a Config."""
    return Config(path).load(path)

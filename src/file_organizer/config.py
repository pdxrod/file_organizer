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
    "organize_folders": [],  # folders to scan for the organized tree (defaults to source_folders)
    "organize_file_types": ["documents", "images", "music", "video"],  # which type categories to link
    # Item 15: the organized soft-link folder lives inside the file_organizer
    # folder by default; set output_base in config.yaml to move it anywhere
    # else (e.g. ~/organized for the old behaviour).
    "output_base": "organized",
    # Safety defaults. User config lists are MERGED on top of these (see
    # _union_list in load()) so junk like .git is never synced even with a
    # config file created before these rules existed.
    "exclude_patterns": [
        # Junk files
        ".DS_Store", "*.pyc", "*.pyo", "*.log", "*.crypt14",
        ".Spotlight-V100", ".TemporaryItems", ".fseventsd",
        ".DocumentRevisions-V100", ".Trash", ".Trashes", "*_files",
        # Cloud apps' own artifacts — reading these placeholders is what
        # made Proton syncs hang (item 11). The * catches Proton's
        # '.trashed-<timestamp>-<name>' trash markers.
        ".trashed*", ".pending*", ".thumbnails*",
        # Version control — back these up with git push, never file sync
        ".git", ".hg", ".svn", ".cvs",
        # Environments and dependencies — recreate from lock files
        ".venv", "venv", "env", ".env",
        "node_modules", ".next", ".nuxt",
        "vendor", "bundle", ".bundle", "priv/static",
        # Build output and caches
        "__pycache__", ".pytest_cache", ".mypy_cache", ".tox",
        ".cache", ".parcel-cache", "coverage", ".nyc_output",
        "elm-stuff", ".elixir_ls", ".stack-work", "_build",
        "deps", "ebin", "dist", "build", "target",
        ".gradle", ".m2", "tmp/cache", ".tmp*",
    ],
    # Legacy softlink-handling classification (see softlink_handler.py).
    # The modern behaviour is exclusion via exclude_patterns above; these
    # lists only matter if softlink_folder_patterns is used.
    "softlink_never_patterns": [".git", ".hg", ".svn", ".cvs"],
    "softlink_reproducible_patterns": [
        ".venv", "venv", "env", ".env",
        "node_modules", ".next", ".nuxt",
    ],
    "softlink_build_cache_patterns": [
        "__pycache__", ".pytest_cache", ".mypy_cache", ".tox",
        ".cache", ".parcel-cache", "coverage", ".nyc_output",
        "elm-stuff", ".elixir_ls", ".stack-work", "_build",
        "deps", "ebin", "dist", "build", "target",
        ".bundle", "vendor/bundle", "priv/static",
        ".gradle", ".m2", "tmp/cache",
    ],
    "empty_folder_patterns": [
        "node_modules", "_build", "deps", "ebin", "dist", "build",
        "target", ".next", ".nuxt", ".cache", ".parcel-cache",
        "coverage", ".nyc_output", "elm-stuff", ".elixir_ls",
        ".stack-work", ".bundle", "vendor", "bundle", "priv/static",
        ".gradle", ".m2", "tmp/cache", ".tmp*",
    ],
    "min_file_size": 1024,
    "max_file_size": 104857600,  # 100 MB
    "enable_content_analysis": True,
    "enable_duplicate_detection": False,
    "enable_folder_sync": True,
    "scan_interval": 3600,
    # Idle backoff (item 14): after cycles that change nothing, the daemon
    # sleeps longer — each idle cycle multiplies scan_interval by the
    # factor, never exceeding scan_interval * idle_backoff_max_multiplier.
    "idle_backoff_factor": 2,
    "idle_backoff_max_multiplier": 8,
    "flaky_volume_retries": 3,
    "retry_delay": 5,
    "use_rsync": True,
    "rsync_checksum_mode": "timestamp",
    "rsync_size_only": False,
    # Item 11: whole-file transfers by default — the mmap-heavy delta
    # algorithm hangs reading Proton Drive placeholder files.
    "rsync_whole_file": True,
    # Item 11: per-I/O stall guard (rsync --timeout, seconds).
    "rsync_io_timeout_seconds": 300,
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
    # Item 10: collapse foo (1).png rename families during production cycles.
    # Off by default — it deletes the losing siblings; preview with
    # `organize collapse-renames`.
    "collapse_rename_families": False,
    "log_path": "~/.file_organizer.log",
    "pid_path": "/tmp/file_organizer.pid",
    "phone_pull": {
        "local_stage": "~/misc",
        "remote_stage": "~/misc",
        "adb_path": "adb",
        "temp_stage": "",  # empty = <local_stage>/.tmp_pull
        "android_base": "/storage/emulated/0",
        "min_age_minutes": 5,
        "state_file": "~/.phone_pull_state",
        "phone_source_dirs": ["file_organizer_staging"],
        "phone_push_dir": "file_organizer_staging",
        "include_extensions": [],
        "exclude_extensions": [
            ".tmp", ".temp", ".partial", ".crdownload", ".part", ".download",
            ".crypt14", ".pyc", ".log",
        ],
        # Item 12b: phone staging subfolder → computer folder mapping.
        # Keys are paths relative to the phone's file_organizer_staging
        # folder (e.g. "Music", "DCIM/Camera"). Values are computer folders
        # (drive names allowed). Longest matching prefix wins; unlisted
        # folders go to a same-named folder under MAIN_DRIVE when it
        # exists, otherwise MAIN_DRIVE/misc (created on demand).
        "staging_map": {},
    },
}


class ConfigError(Exception):
    """Configuration error."""


# Template placeholders that mean "this config has not been personalised yet".
# A config copied from config_template.yaml and not edited still contains
# paths like /Users/janedoe or /Volumes/YourExternalDrive. If such a path
# does not exist at startup, file_organizer stops and tells the user to edit
# the config instead of silently doing nothing. Keep in sync with the
# PLACEHOLDER_MARKERS list in phone_sync.py.
TEMPLATE_PLACEHOLDER_MARKERS = (
    "janedoe",
    "yourexternaldrive",
    "yourgoogledrive",
    "yourname",
)


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
                content = f.read()
            _check_no_tabs(content, target)
            loaded = yaml.safe_load(content) or {}
            # Since the single-template merge (item 5), config.yaml is
            # generated from the `computer:` section of config_template.yaml.
            # Accept both the sectioned format and legacy flat configs.
            if isinstance(loaded, dict) and isinstance(loaded.get("computer"), dict):
                self._raw = loaded["computer"]
            else:
                self._raw = loaded
        else:
            self._raw = {}

        # Merge with defaults (user values take precedence)
        merged = dict(DEFAULTS)
        merged.update(self._raw)

        # Exclusion safety: the built-in defaults must never be removable by
        # an old or minimal config, so user lists are merged ON TOP of them.
        merged["exclude_patterns"] = _union_list(
            DEFAULTS["exclude_patterns"],
            self._raw.get("exclude_patterns") or [],
        )

        # Resolve drives
        self.drives = self._resolve_drives(merged.get("drives", {}))

        # Resolve all paths that contain drive placeholders
        merged["drives"] = self.drives
        merged["sync_pairs"] = self._resolve_sync_pairs(merged.get("sync_pairs", []))
        merged["one_way_pairs"] = self._resolve_sync_pairs(merged.get("one_way_pairs", []))
        merged["source_folders"] = [
            self._resolve_path(p) for p in merged.get("source_folders", [])
        ]
        merged["organize_folders"] = [
            self._resolve_path(p) for p in merged.get("organize_folders", [])
        ]
        merged["output_base"] = self._resolve_output_base(merged.get("output_base"))
        merged["auto_git_folders"] = [
            self._resolve_path(p) for p in merged.get("auto_git_folders", [])
        ]

        # Deep-merge nested phone_pull defaults so a partial phone_pull:
        # section in YAML still gets the remaining defaults.
        pp = dict(DEFAULTS.get("phone_pull", {}))
        pp.update(merged.get("phone_pull") or {})
        for key in ("local_stage", "remote_stage", "adb_path", "state_file", "temp_stage"):
            if pp.get(key):
                pp[key] = self._resolve_path(pp[key])
        if not pp.get("temp_stage"):
            pp["temp_stage"] = os.path.join(
                pp.get("local_stage") or os.path.expanduser("~/misc"), ".tmp_pull"
            )
        elif not os.path.isabs(pp["temp_stage"]):
            # Relative temp_stage values are relative to local_stage
            pp["temp_stage"] = os.path.join(
                pp.get("local_stage") or os.path.expanduser("~/misc"), pp["temp_stage"]
            )
        merged["phone_pull"] = pp

        merged["log_path"] = self._resolve_path(merged.get("log_path", DEFAULTS["log_path"]))
        merged["pid_path"] = self._resolve_path(merged.get("pid_path", DEFAULTS["pid_path"]))

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

    def _resolve_output_base(self, value) -> str:
        """Item 15: the organized soft-link folder defaults to inside the
        file_organizer folder; the config can point it anywhere else.

        Values that mean "use the default" — missing, empty, the literal
        'organized', or the pre-item-15 default '~/organized' — resolve to
        <file_organizer folder>/organized. Any other value is a deliberate
        choice and is resolved like any other path (~ and drive names).
        """
        if value in (None, "", "organized", "~/organized"):
            return str(find_project_root() / "organized")
        return self._resolve_path(value)

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

        # Template placeholders in paths mean the config was never
        # personalised — fail fast with instructions (item 7).
        self._validate_path_placeholders()

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

    def _validate_path_placeholders(self) -> None:
        """Raise ConfigError if a configured path is a template placeholder
        that does not exist (item 7).

        Removable drives genuinely come and go, so REAL missing paths only
        warn (see above). But a missing path that still contains a template
        placeholder (e.g. /Users/janedoe) means the config is an unedited
        copy of config_template.yaml — exit with instructions instead of
        silently doing nothing.
        """
        def _is_placeholder(path: str) -> bool:
            return any(m in path.lower() for m in TEMPLATE_PLACEHOLDER_MARKERS)

        def _check(path: str, what: str) -> None:
            if not path or os.path.exists(os.path.expanduser(path)):
                return
            if _is_placeholder(path):
                raise ConfigError(
                    f"{what} '{path}' does not exist and still contains a "
                    f"template placeholder (e.g. /Users/janedoe). "
                    f"This looks like an unedited copy of "
                    f"config_template.yaml. Edit config.yaml and set your "
                    f"real paths, then run again."
                )

        d = self.data
        for key, path in d.get("drives", {}).items():
            _check(path, f"Drive '{key}'")
        all_pairs = d.get("sync_pairs", []) + d.get("one_way_pairs", [])
        for i, pair in enumerate(all_pairs):
            for folder in pair.get("folders", []):
                _check(folder, f"sync pair {i}")
        for key in ("source_folders", "organize_folders", "auto_git_folders"):
            for path in d.get(key, []) or []:
                _check(path, key)
        pp = d.get("phone_pull") or {}
        _check(pp.get("local_stage") or "", "phone_pull.local_stage")
        _check(pp.get("remote_stage") or "", "phone_pull.remote_stage")

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


    @classmethod
    def create_starter(cls, target_path: str) -> str:
        """Create a starter config.yaml from the template with most features
        turned off for safety.  Returns the path to the created file."""
        template_path = str(find_project_root() / "config_template.yaml")

        if not os.path.exists(template_path):
            raise ConfigError(
                f"Template not found at {template_path}. "
                "Please reinstall file_organizer."
            )

        with open(template_path, "r") as f:
            template = yaml.safe_load(f) or {}

        # The merged template has computer:/phone: sections — the computer
        # program uses only the computer: part (item 5). Legacy flat
        # templates are accepted as-is.
        if isinstance(template, dict) and isinstance(template.get("computer"), dict):
            starter = template["computer"]
        else:
            starter = template

        # ── turn off almost everything for a safe starter config ──
        starter["enable_content_analysis"] = False
        starter["enable_folder_sync"] = False
        starter["enable_duplicate_detection"] = False
        starter["enable_semantic_categories"] = False
        starter["auto_git"] = False
        if "ml_content_analysis" in starter:
            starter["ml_content_analysis"]["enabled"] = False

        with open(target_path, "w") as f:
            yaml.safe_dump(starter, f, default_flow_style=False, sort_keys=False)

        print(
            f"\n{'='*60}\n"
            f"  Starter config created: {target_path}\n"
            f"{'='*60}\n"
            f"\n"
            f"  Most features are OFF by default for safety.\n"
            f"\n"
            f"  To get started:\n"
            f"    1. Edit {target_path}\n"
            f"    2. Set your drive paths under 'drives:'\n"
            f"    3. Configure 'source_folders' to point at folders you\n"
            f"       want to scan\n"
            f"    4. Enable the features you want (set to true):\n"
            f"         - enable_content_analysis\n"
            f"         - enable_folder_sync\n"
            f"         - enable_duplicate_detection  (careful!)  \n"
            f"         - enable_semantic_categories\n"
            f"         - auto_git\n"
            f"    5. Run './organize test' to dry-run\n"
            f"    6. When ready, run './organize start'\n"
            f"{'='*60}\n",
            file=sys.stderr,
        )

        return str(target_path)


def _union_list(base: List[str], extra: List[str]) -> List[str]:
    """Return base items followed by any extra items not already present.

    Used for exclusion lists: the built-in safety defaults must always be in
    force, and user config only ever ADDS patterns on top of them.
    """
    seen = set()
    out: List[str] = []
    for item in list(base or []) + list(extra or []):
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _check_no_tabs(content: str, path: str) -> None:
    """YAML forbids TAB characters in indentation, and the resulting parser
    errors are cryptic. Detect tabs early and say it in plain language
    (item 9)."""
    for i, line in enumerate(content.splitlines(), start=1):
        # Leading whitespace (spaces AND tabs) — tabs anywhere in it are fatal.
        indent = line[:len(line) - len(line.lstrip())]
        if "\t" in indent:
            raise ConfigError(
                f"{path} line {i}: TAB characters are not allowed in YAML "
                f"indentation. Use SPACES instead. Open the file in "
                f"TextEdit/Notepad/nano and replace the tabs with spaces, "
                f"then run again."
            )


def report_first_run(config_path: str) -> None:
    """Print a 'will it work?' report for a freshly created config (item 9).

    Reads the config file (which may still contain template placeholders),
    and for every drive explains whether the path exists, is a placeholder
    that must be edited, or is simply not currently available.
    Never raises.
    """
    try:
        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f) or {}
        if isinstance(cfg, dict) and isinstance(cfg.get("computer"), dict):
            cfg = cfg["computer"]
    except Exception:
        return

    drives = cfg.get("drives", {}) or {}
    print("\nWill it work? Checking the drive paths in your new config:", file=sys.stderr)
    for key, raw in drives.items():
        path = os.path.expanduser(str(raw))
        if os.path.exists(path):
            print(f"  ✓ {key}: {path} exists", file=sys.stderr)
        elif any(m in str(raw).lower() for m in TEMPLATE_PLACEHOLDER_MARKERS):
            print(f"  ✗ {key}: {path} — template placeholder, "
                  f"replace it with your real path", file=sys.stderr)
        else:
            print(f"  ? {key}: {path} — not available right now "
                  f"(drive not plugged in?); it will be skipped", file=sys.stderr)
    print(
        "\nTo finish setup:\n"
        f"  1. Open {config_path} with TextEdit (Mac), Notepad (Windows) "
        f"or nano (Termux).\n"
        "  2. Replace the placeholder paths (anything with 'janedoe' or\n"
        "     'YourExternalDrive') with YOUR real paths.\n"
        "  3. Use SPACES for indentation — YAML does not allow TABs.\n"
        "  4. Run again: ./organize test\n",
        file=sys.stderr,
    )


def find_project_root() -> Path:
    """Locate the project root: the directory containing the shared
    config_template.yaml. Walks up from this file
    (src/file_organizer/config.py → src → project root), and falls back to
    this file's directory when no template is found (e.g. old flat layout).
    """
    cur = Path(__file__).resolve().parent
    for _ in range(6):
        if (cur / "config_template.yaml").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return Path(__file__).resolve().parent


def load_config(path: Optional[str] = None) -> Config:
    """Convenience: load and return a Config."""
    return Config(path).load(path)


def resolve_staging_map(staging_map, drives) -> Dict[str, str]:
    """Item 12b: resolve drive names in a staging_map {prefix: dest}."""
    out: Dict[str, str] = {}
    for prefix, dest in (staging_map or {}).items():
        resolved = str(dest)
        for drive_key, drive_path in (drives or {}).items():
            resolved = resolved.replace(drive_key, drive_path)
        out[prefix] = os.path.expanduser(resolved)
    return out


def map_staging_path(rel_path, drives, staging_map, misc_dir: str):
    """Item 12b: map a phone staging-relative path to a computer folder.

    Returns (dest_dir, remainder):

    1. The longest configured staging_map prefix wins, e.g.
       "Music" → "MAIN_DRIVE/Entertainment" sends Music/foo.mp3 to
       MAIN_DRIVE/Entertainment/foo.mp3 (override of the default).
    2. Otherwise the longest prefix whose same-named folder exists under
       MAIN_DRIVE wins, e.g. Music/foo.mp3 → MAIN_DRIVE/Music/foo.mp3.
    3. Otherwise everything lands in misc_dir (created on demand by the
       caller) with the full staging path preserved underneath it.
    """
    rel_path = (rel_path or "").strip("/")
    mapping = resolve_staging_map(staging_map, drives)

    best = None
    for prefix, dest in mapping.items():
        p = str(prefix).strip("/")
        if not p:
            continue
        if rel_path == p or rel_path.startswith(p + "/"):
            if best is None or len(p) > len(best[0]):
                best = (p, dest)
    if best:
        prefix, dest = best
        return dest, rel_path[len(prefix):].lstrip("/")

    main = (drives or {}).get("MAIN_DRIVE")
    if main:
        parts = [p for p in rel_path.split("/") if p]
        for i in range(len(parts), 0, -1):
            cand = os.path.join(main, *parts[:i])
            if os.path.isdir(cand):
                return cand, "/".join(parts[i:])

    return os.path.expanduser(misc_dir), rel_path


def ensure_output_dir(config: Config) -> Path:
    """Item 15: make sure the organized soft-link folder exists.

    The folder is the replacement for ~/organized and lives inside the
    file_organizer folder by default (configurable via output_base).
    Creating it eagerly — even in dry-run mode — means users can always
    see where their links will go. Returns the folder path.
    """
    raw = config.data.get("output_base") if config is not None else None
    path = Path(raw) if raw else Path(find_project_root() / "organized")
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_log_path(config_path: Optional[str] = None) -> str:
    """Read log_path from a config.yaml, falling back to the default.

    Safe to call before a full Config is loaded (e.g. during logging setup
    in cli.py, or from the GUI/web apps). Never raises.
    """
    default = os.path.expanduser("~/.file_organizer.log")
    target = config_path or str(find_project_root() / "config.yaml")
    try:
        with open(target, "r") as f:
            cfg = yaml.safe_load(f) or {}
        if isinstance(cfg, dict) and isinstance(cfg.get("computer"), dict):
            cfg = cfg["computer"]
        val = cfg.get("log_path")
        if val:
            return os.path.expanduser(str(val))
    except Exception:
        pass
    return default

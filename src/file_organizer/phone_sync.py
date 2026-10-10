#!/usr/bin/env python3
"""
Phone Sync — lightweight file monitor and sync for Android (Termux) or macOS.

Monitors configured source directories and copies new/changed files to a
target cloud drive folder (Proton Drive, Google Drive, Dropbox, …). Tracks
copied files by content hash (SHA-256) so renames and moves are handled
correctly.

Key features:
- Content-hash tracking (SQLite) — never copies the same file twice
- min_age_minutes grace period — gives you time to delete unwanted photos
  before they get backed up (solves the cloud "too eager" backup problem)
- Configurable source directories and target path
- Dry-run mode for safe testing
- Runs on Android via Termux, or on macOS
- PID-file management for start/stop/status

Usage:
    python3 phone_sync.py              # start in the foreground
    python3 phone_sync.py --background # start in the background
    python3 phone_sync.py --scan-once  # single scan then exit
    python3 phone_sync.py --dry-run    # scan but don't copy
    python3 phone_sync.py --find-cloud-drives  # locate cloud drive folders
    python3 phone_sync.py --config /path/to/config.yaml
"""

import os
import sys
import json
import re
import time
import signal
import hashlib
import sqlite3
import logging
import argparse
import shutil
import fnmatch
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Iterator

# (N)-family collapse (item 10). Relative import for package execution with a
# fallback for standalone execution (python3 phone_sync.py on the phone).
try:
    from .rename_family import parse_family, plan_collapse, apply_collapse, \
        plan_underscore_duplicates
except ImportError:  # pragma: no cover - standalone script execution
    from rename_family import parse_family, plan_collapse, apply_collapse, \
        plan_underscore_duplicates

# App-generated name gate + leading-date normalisation (item 13).
try:
    from .filename_rules import is_app_generated, normalise_leading_date
except ImportError:  # pragma: no cover - standalone script execution
    from filename_rules import is_app_generated, normalise_leading_date

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = "phone_sync_config.yaml"
DEFAULT_DB = "~/.phone_sync.db"
DEFAULT_LOG = "~/.phone_sync.log"
DEFAULT_PID = "~/.phone_sync.pid"

# Built-in safety defaults. The live, editable copies of these lists live in
# the phone: section of config_template.yaml (cloud_drive_paths,
# exclude_extensions, exclude_patterns, exclude_folder_names). User config
# lists are MERGED on top of these rather than replacing them, so junk never
# gets copied even with a config file created before these rules existed.

# Common cloud drive paths on various platforms (fallback for the
# cloud_drive_paths config key). The finder reports ALL of them — the user
# may use Proton Drive, Google Drive, Dropbox, iCloud, OneDrive, or any
# other cloud app.
DEFAULT_CLOUD_DRIVE_CANDIDATES = [
    # macOS — Apple File Provider mounts
    "~/Library/CloudStorage/ProtonDrive-*",
    "~/Library/CloudStorage/GoogleDrive-*",
    "~/Library/CloudStorage/Dropbox-*",
    "~/Library/CloudStorage/OneDrive-*",
    "~/Library/CloudStorage/Box-*",
    # macOS — classic sync-app folders
    "~/Google Drive",
    "~/GoogleDrive",
    "~/Dropbox",
    "~/OneDrive",
    "~/iCloud Drive",
    "~/Library/Mobile Documents/com~apple~CloudDocs",  # iCloud Drive
    # Windows
    "C:/Users/*/Google Drive",
    "C:/Users/*/Dropbox",
    "C:/Users/*/OneDrive",
    # Android — shared storage
    "/storage/emulated/0/ProtonDrive",
    "/storage/emulated/0/Proton Drive",
    "/storage/emulated/0/GoogleDrive",
    "/storage/emulated/0/Google Drive",
    "/storage/emulated/0/Dropbox",
    "/storage/emulated/0/OneDrive",
    "/storage/emulated/0/Documents/ProtonDrive",
    "/storage/emulated/0/Download/ProtonDrive",
    # Android — app media (scoped storage)
    "/storage/emulated/0/Android/media/com.protontech.android.drive",
    "/storage/emulated/0/Android/media/com.google.android.apps.docs",
    "/storage/emulated/0/Android/media/com.dropbox.android",
    # Android — app data (may need root)
    "/storage/emulated/0/Android/data/com.protontech.android.drive/files",
    # Termux shared
    "~/storage/shared/ProtonDrive",
    "~/storage/shared/Proton Drive",
    "~/storage/shared/GoogleDrive",
    "~/storage/shared/Google Drive",
    "~/storage/shared/Dropbox",
]

# Extensions never copied. Keep in sync with the phone: section of
# config_template.yaml.
DEFAULT_EXCLUDE_EXTENSIONS = [
    ".tmp", ".temp", ".partial", ".crdownload", ".part", ".download",
    ".crypt14", ".pyc", ".log",
]

# Substring patterns matched against file AND folder names.
DEFAULT_EXCLUDE_PATTERNS = [
    ".thumbnails", ".thumbdata", ".face", ".pending",
    ".trashed", ".trash", "thumbdata", ".cache", ".tmp",
    # Junk files (macOS leftovers end up on phones too)
    ".DS_Store", ".Spotlight-V100", ".TemporaryItems",
    ".fseventsd", ".DocumentRevisions-V100",
]

# Exact folder-name patterns (case-insensitive, fnmatch). Matched against the
# folder name and its path relative to the source root, so "priv/static" and
# "tmp/cache" work. Exact matching means "build" never hits a folder called
# "mybuildings". Keep in sync with the phone: section of config_template.yaml.
DEFAULT_EXCLUDE_FOLDER_NAMES = [
    # Version control — back these up with git push, never file sync
    ".git", ".hg", ".svn", ".cvs",
    # Environments and dependencies — recreate from lock files
    ".venv", "venv", "env", ".env",
    "node_modules", ".next", ".nuxt",
    # Build output and caches
    "__pycache__", ".pytest_cache", ".mypy_cache", ".tox",
    ".cache", ".parcel-cache", "coverage", ".nyc_output",
    "elm-stuff", ".elixir_ls", ".stack-work", "_build",
    "deps", "ebin", "dist", "build", "target",
    ".bundle", "vendor", "bundle", "priv/static",
    ".gradle", ".m2", "tmp/cache",
]


def _merged_list(*lists: list) -> list:
    """Merge lists in order, dropping duplicates.

    Built-in safety defaults come first; user config lists only ADD to them.
    """
    seen = set()
    out = []
    for lst in lists:
        for item in lst or []:
            if item not in seen:
                seen.add(item)
                out.append(item)
    return out


def compute_idle_sleep(base: int, factor: int, max_multiplier: int, streak: int) -> int:
    """Sleep duration for a daemon cycle after `streak` idle cycles.

    Each idle cycle multiplies the base interval by `factor`, capped at
    base * max_multiplier. Any action resets the streak to 0 (item 14).
    Kept in sync with file_organizer.cli.compute_idle_sleep.
    """
    if streak <= 0 or factor <= 1:
        return base
    multiplier = min(factor ** min(streak, 12), max_multiplier)
    return base * multiplier


# Template placeholders that mean "this config has not been personalised yet".
# Keep in sync with TEMPLATE_PLACEHOLDER_MARKERS in config.py.
PLACEHOLDER_MARKERS = (
    "janedoe",
    "yourexternaldrive",
    "yourgoogledrive",
    "yourname",
)

# ── Logging ──────────────────────────────────────────────────────────────────

logger = logging.getLogger("phone_sync")


def setup_logging(log_path: str, verbose: bool = False) -> None:
    """Configure file + console logging."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    log_file = Path(log_path).expanduser().resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    fh = logging.FileHandler(str(log_file))
    fh.setLevel(level)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)

    logger.setLevel(level)
    logger.handlers.clear()
    logger.addHandler(fh)
    logger.addHandler(ch)


# ── Config ───────────────────────────────────────────────────────────────────

def _expand_path(raw: str) -> str:
    """Expand ~ and environment variables in a path string."""
    return os.path.expandvars(os.path.expanduser(raw))


def _find_template(config_path: Path) -> Optional[Path]:
    """Locate the config template next to the config path or this script.

    There is exactly one template: config_template.yaml, whose `phone:`
    section is extracted (item 5 merged the old phone_daemon_config
    template into it).
    """
    bases = [config_path.parent, Path(__file__).resolve().parent]
    # Walk up from this file (src/file_organizer → src → project root) so
    # the shared config_template.yaml at the project root is found.
    for parent in Path(__file__).resolve().parent.parents:
        bases.append(parent)
    candidates = [base / "config_template.yaml" for base in bases]
    return next((t for t in candidates if t.exists()), None)


def _extract_phone_section(content: str) -> str:
    """Extract the `phone:` section of a merged config template as flat YAML.

    The merged config_template.yaml has `computer:` and `phone:` sections.
    The phone daemon only needs the phone: part, and its built-in YAML
    parser only understands the flat subset, so we slice the phone block
    textually (top-level keys at column 0 mark section boundaries) and
    de-dent it.

    If there is no `phone:` section, the content is returned unchanged
    (a flat config with no section header passes through).
    """
    lines = content.splitlines()
    out = []
    in_phone = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            # Top-level key: enter/leave the phone section.
            in_phone = stripped == "phone:" or stripped.startswith("phone: ")
            continue
        if in_phone:
            # De-dent the two-space section indent.
            out.append(line[2:] if line.startswith("  ") else line)
    if not out:
        return content
    return "\n".join(out)


def _select_phone_config(cfg):
    """Return the phone-relevant config dict.

    Accepts either a merged template dict (with a `phone:` key) or a legacy
    flat phone config dict.
    """
    if isinstance(cfg, dict) and isinstance(cfg.get("phone"), dict):
        cfg = cfg["phone"]
    return cfg


def _parse_simple_scalar(token: str):
    """Parse one YAML scalar value (no nested collections)."""
    token = token.strip()
    if not token:
        return None
    # Quoted string — take verbatim, quotes included.
    if len(token) >= 2 and token[0] in "'\"" and token[-1] == token[0]:
        return token[1:-1]
    # Strip a trailing comment that is not inside quotes (none are, here).
    comment_at = token.find(" #")
    if comment_at != -1:
        token = token[:comment_at].strip()
    low = token.lower()
    if low in ("null", "~", "none", ""):
        return None
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if re.match(r"^[+-]?\d+$", token):
        return int(token)
    if re.match(r"^[+-]?\d*\.\d+$", token):
        return float(token)
    return token


def _parse_simple_yaml(content: str) -> dict:
    """Parse the flat YAML subset used by phone_sync_config.yaml.

    Used when PyYAML is not installed (Termux no longer ships python-pyyaml,
    and a pip install would need the clang toolchain). Supports:
      - comment lines (# …) anywhere, including inside block lists
      - flat `key: value` mappings
      - block lists:
          key:
            - item
      - inline lists:  key: []  /  key: [a, b]   (naive comma split — no
        commas inside quoted items)
      - scalars: quoted strings, bare strings, ints, floats, bools, null

    Raises ValueError on anything outside this subset; callers then fall
    back to JSON or fail with an install hint.
    """
    cfg: dict = {}
    lines = content.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if line[0] in " \t":
            raise ValueError(
                f"line {i + 1}: indented line outside of a list: {stripped!r}"
            )
        if ":" not in stripped:
            raise ValueError(f"line {i + 1}: expected 'key: value', got {stripped!r}")
        key, _, rest = stripped.partition(":")
        key = key.strip()
        rest = rest.strip()
        if not key:
            raise ValueError(f"line {i + 1}: empty key in {stripped!r}")
        if rest in ("", "-"):
            # Block list — collect following `- item` lines.
            items = []
            j = i + 1
            while j < n:
                lj = lines[j].rstrip()
                sj = lj.strip()
                if not sj or sj.startswith("#"):
                    j += 1
                    continue
                if not sj.startswith("-"):
                    break
                items.append(_parse_simple_scalar(sj[1:]))
                j += 1
            cfg[key] = items
            i = j
            continue
        if rest.startswith("["):
            if not rest.endswith("]"):
                raise ValueError(
                    f"line {i + 1}: unterminated inline list for {key!r}"
                )
            inner = rest[1:-1].strip()
            if not inner:
                cfg[key] = []
            else:
                cfg[key] = [_parse_simple_scalar(p) for p in inner.split(",")]
            i += 1
            continue
        cfg[key] = _parse_simple_scalar(rest)
        i += 1
    return cfg


def _make_phone_config_from_template(template: Path, config_path: Path) -> None:
    """Write a live phone config from a template.

    The merged config_template.yaml contributes only its `phone:` section
    (de-dented, so it stays inside the built-in parser's flat subset).
    """
    content = template.read_text(encoding="utf-8")
    header = (
        "# Generated from config_template.yaml (the phone: section).\n"
        "# Edit THIS file for per-device settings — the template's\n"
        "# phone: section is the source of defaults. No tabs in YAML!\n"
    )
    config_path.write_text(
        header + _extract_phone_section(content), encoding="utf-8"
    )


def _check_no_tabs(content: str, path: str) -> None:
    """YAML forbids TAB characters in indentation, and the parser errors
    are cryptic. Detect tabs early and say it in plain language (item 9)."""
    for i, line in enumerate(content.splitlines(), start=1):
        # Leading whitespace (spaces AND tabs) — tabs anywhere in it are fatal.
        indent = line[:len(line) - len(line.lstrip())]
        if "\t" in indent:
            msg = (
                f"\nERROR: {path} line {i}: TAB characters are not allowed "
                f"in YAML indentation. Use SPACES instead. Open the file "
                f"with nano, replace the tabs with spaces, then run again.\n"
            )
            logger.error(msg)
            print(msg, file=sys.stderr)
            sys.exit(1)


def _report_phone_first_run(config_path: Path, content: str) -> None:
    """Item 9: after the phone config was just created from the template,
    tell the user whether the paths will work on this device."""
    try:
        cfg = _parse_simple_yaml(_extract_phone_section(content))
    except Exception:
        return
    print("\nWill it work? Checking the paths in your new config:",
          file=sys.stderr)
    target = cfg.get("target_directory")
    if target:
        t_path = _expand_path(str(target))
        if os.path.exists(t_path):
            print(f"  ✓ target_directory: {target} exists", file=sys.stderr)
        else:
            print(f"  ? target_directory: {target} does not exist yet — "
                  f"it will be created on first copy, or edit it",
                  file=sys.stderr)
    for raw in cfg.get("source_directories", []) or []:
        p = _expand_path(str(raw))
        if os.path.exists(p):
            print(f"  ✓ source: {raw} exists", file=sys.stderr)
        elif any(m in str(raw).lower() for m in PLACEHOLDER_MARKERS):
            print(f"  ✗ source: {raw} — template placeholder, "
                  f"replace it with your real path", file=sys.stderr)
        else:
            print(f"  ? source: {raw} — not found right now "
                  f"(skipped until it exists)", file=sys.stderr)
    print(
        f"\nEdit {config_path} with nano (use SPACES, never tabs) and "
        f"run again.\n",
        file=sys.stderr,
    )


def load_config(path: str) -> dict:
    """Load config from YAML (preferred) or JSON file. Returns dict.

    If the config file does not exist and a template is available next to
    the config path or next to this script, a live config is created from
    the template automatically (for the merged config_template.yaml, only
    its `phone:` section) so the daemon has a real, editable config.
    """
    config_path = Path(path).expanduser().resolve()
    created_from_template = False

    if not config_path.exists():
        template = _find_template(config_path)
        if template is not None:
            _make_phone_config_from_template(template, config_path)
            created_from_template = True
            logger.warning(
                "No config found at %s — created it from %s. "
                "Review source_directories and target_directory, then re-run.",
                config_path,
                template,
            )
        else:
            logger.warning(
                "No config found at %s and no template available — using "
                "built-in defaults. Copy config_template.yaml next to this "
                "script to get an editable config.",
                config_path,
            )
            return _default_config()

    content = config_path.read_text(encoding="utf-8")
    _check_no_tabs(content, str(config_path))

    # An existing-but-empty config (truncated copy, stray `touch`, …) would
    # otherwise die later with a JSON decode error at "line 1 column 1".
    # Recover from the template instead.
    if not content.strip():
        template = _find_template(config_path)
        if template is not None:
            _make_phone_config_from_template(template, config_path)
            content = config_path.read_text(encoding="utf-8")
            created_from_template = True
            logger.warning(
                "Config at %s was empty — replaced it from %s. "
                "Review source_directories and target_directory, then re-run.",
                config_path,
                template,
            )
        else:
            logger.warning(
                "Config at %s was empty and no template was found — "
                "using built-in defaults.",
                config_path,
            )
            return _default_config()

    # First-run UX (item 9): if we just created the config, tell the user
    # whether the paths will work on this device.
    if created_from_template:
        _report_phone_first_run(config_path, content)

    # Try YAML first
    try:
        import yaml
        cfg = yaml.safe_load(content)
        if cfg:
            return _select_phone_config(cfg)
    except ImportError:
        # Termux no longer ships a python-pyyaml package and a pip install
        # would need the clang toolchain, so use the built-in parser for the
        # flat config subset this daemon uses (unless the content is JSON).
        if not content.lstrip().startswith(("{", "[")):
            try:
                cfg = _parse_simple_yaml(_extract_phone_section(content))
                logger.info("PyYAML not installed — using built-in YAML parser.")
                return cfg
            except ValueError as e:
                logger.warning(
                    "PyYAML is not installed and the built-in YAML parser could "
                    "not read the config (%s) — trying JSON…\n"
                    "For full YAML support: pip install pyyaml "
                    "(Termux may also need: pkg install clang)",
                    e,
                )
    except Exception as e:
        logger.warning("YAML parse failed (%s), trying JSON…", e)

    # Fall back to JSON
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        logger.error("Cannot parse config as YAML or JSON: %s", e)
        stripped = content.lstrip()
        if not stripped.startswith(("{", "[")):
            logger.error(
                "The config does not start like JSON and the built-in YAML "
                "parser could not read it either. Check the file for syntax "
                "errors, or install full YAML support with: "
                "pip install pyyaml (Termux may also need: pkg install clang)"
            )
        sys.exit(1)


def resolve_config_path(requested: str) -> str:
    """Resolve the requested config path.

    If the path is a bare relative filename that does not exist in the
    current working directory, fall back to the same filename next to this
    script. This lets `python3 /path/to/phone_sync.py` find a config that
    ships alongside the script regardless of where it is launched from.
    """
    p = Path(requested)
    if p.is_absolute() or p.exists():
        return requested
    candidate = Path(__file__).resolve().parent / requested
    if candidate.exists():
        return str(candidate)
    return requested


def _default_config() -> dict:
    """Return sensible default configuration."""
    return {
        "source_directories": [
            "/storage/emulated/0/DCIM",
            "/storage/emulated/0/Pictures",
            "/storage/emulated/0/Documents",
            "/storage/emulated/0/Download",
        ],
        "target_directory": "/storage/emulated/0/file_organizer_staging",
        "min_age_minutes": 10,
        "scan_interval_seconds": 60,
        "idle_backoff_factor": 2,
        "idle_backoff_max_multiplier": 8,
        "max_file_size_mb": 500,
        "include_extensions": [],  # empty = all files
        "exclude_extensions": list(DEFAULT_EXCLUDE_EXTENSIONS),
        "exclude_patterns": list(DEFAULT_EXCLUDE_PATTERNS),
        "exclude_folder_names": list(DEFAULT_EXCLUDE_FOLDER_NAMES),
        "exclude_folder_patterns": [],  # folder NAMES only — e.g. "line", "twitter"
        "cloud_drive_paths": list(DEFAULT_CLOUD_DRIVE_CANDIDATES),
        # Phone-side cloud sync: the daemon looks for cloud drive folders on
        # the device and copies new files from source_directories into each
        # one (under cloud_sync_subfolder), so files are backed up to the
        # cloud even without the computer. Off by default.
        "sync_cloud_drives": False,
        "cloud_sync_subfolder": "file_organizer",
        # Free-space guard: stop copying when the target has less than this
        # much free space (MB) — never fill the phone up.
        "min_free_space_mb": 200,
        "prune_max_age_days": 30,
        # Item 10: collapse foo (1).png rename families in the source dirs.
        # Off by default because it deletes files; preview with --dry-run.
        "collapse_rename_families": False,
        # Item 13: hold app-generated filenames (Screenshot_…,
        # YYYYMMDD_HHMMSS.jpg, IMG-YYYYMMDD-WA0001.jpg, …) until renamed;
        # normalise leading dates like 2026-09-23-… to YYYYMMDD when copying.
        "hold_app_generated_names": True,
        "normalise_leading_dates": True,
        "delete_after_copy": False,  # DANGER: only set true if you know what you're doing
        "db_path": DEFAULT_DB,
        "log_path": DEFAULT_LOG,
        "pid_path": DEFAULT_PID,
    }


# ── Database ─────────────────────────────────────────────────────────────────

class SyncDB:
    """SQLite database tracking which files have been synced."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path).expanduser().resolve()
        # Migration from the pre-rename default (~/.phone_daemon.db): keep
        # using the old database if the new one does not exist yet, so the
        # sync history survives the phone_sync rename (item 2).
        if not self.db_path.exists():
            legacy = self.db_path.with_name(
                self.db_path.name.replace("phone_sync", "phone_daemon")
            )
            if legacy.exists():
                logger.info(
                    "Using existing sync database from the old name: %s", legacy
                )
                self.db_path = legacy
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._migrate()
        return self._conn

    def _migrate(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS synced_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_path TEXT NOT NULL,
                target_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                file_mtime REAL NOT NULL,
                copied_at TEXT NOT NULL,
                UNIQUE(source_path, content_hash, target_path)
            )
        """)
        # Pre-cloud-sync databases have UNIQUE(source_path, content_hash),
        # which would block recording the same source synced to a second
        # destination (staging AND a found cloud drive). Recreate the table
        # with a target-aware uniqueness constraint.
        has_old_unique = False
        for row in self.conn.execute("PRAGMA index_list(synced_files)"):
            if not row[2]:  # unique flag
                continue
            idx_cols = [c[2] for c in
                        self.conn.execute(f"PRAGMA index_info({row[1]})")]
            if set(idx_cols) == {"source_path", "content_hash"}:
                has_old_unique = True
        if has_old_unique:
            logger.info("Migrating sync database to target-aware tracking…")
            self.conn.execute("ALTER TABLE synced_files RENAME TO synced_files_old")
            self.conn.execute("""
                CREATE TABLE synced_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_path TEXT NOT NULL,
                    target_path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    file_mtime REAL NOT NULL,
                    copied_at TEXT NOT NULL,
                    UNIQUE(source_path, content_hash, target_path)
                )
            """)
            self.conn.execute("""
                INSERT INTO synced_files
                    (source_path, target_path, content_hash, file_size,
                     file_mtime, copied_at)
                SELECT source_path, target_path, content_hash, file_size,
                       file_mtime, copied_at
                FROM synced_files_old
            """)
            self.conn.execute("DROP TABLE synced_files_old")
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_synced_hash
            ON synced_files(content_hash, file_size)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_synced_source
            ON synced_files(source_path)
        """)
        self.conn.commit()

    def is_synced(self, source_path: str, content_hash: str, file_size: int,
                  target_path: Optional[str] = None) -> bool:
        """Check if a file with this hash+size has already been synced from
        this path (to the given target when target_path is set)."""
        if target_path is not None:
            row = self.conn.execute(
                "SELECT 1 FROM synced_files WHERE source_path=? "
                "AND content_hash=? AND file_size=? AND target_path=?",
                (source_path, content_hash, file_size, target_path),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT 1 FROM synced_files WHERE source_path=? "
                "AND content_hash=? AND file_size=?",
                (source_path, content_hash, file_size),
            ).fetchone()
        return row is not None

    def mark_synced(
        self, source_path: str, target_path: str,
        content_hash: str, file_size: int, file_mtime: float,
    ) -> None:
        """Record a successful sync to a specific target path."""
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        # Target-aware upsert: the same source may be synced to several
        # destinations (staging + each found cloud drive).
        self.conn.execute(
            "DELETE FROM synced_files WHERE source_path=? "
            "AND content_hash=? AND target_path=?",
            (source_path, content_hash, target_path),
        )
        self.conn.execute(
            """INSERT INTO synced_files
               (source_path, target_path, content_hash, file_size, file_mtime, copied_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (source_path, target_path, content_hash, file_size, file_mtime, now),
        )
        # Keep only the newest version per source+target so stats and
        # target lookups stay accurate after in-place updates.
        self.conn.execute(
            "DELETE FROM synced_files WHERE source_path=? "
            "AND target_path=? AND content_hash != ?",
            (source_path, target_path, content_hash),
        )
        self.conn.commit()

    def latest_target_for_source(self, source_path: str) -> Optional[str]:
        """Return the most recently staged target path for a source file."""
        row = self.conn.execute(
            "SELECT target_path FROM synced_files WHERE source_path=? "
            "ORDER BY id DESC LIMIT 1",
            (source_path,),
        ).fetchone()
        return row[0] if row else None

    def forget_source(self, source_path: str) -> None:
        """Remove tracking for a specific source path (e.g. file was deleted)."""
        self.conn.execute(
            "DELETE FROM synced_files WHERE source_path=?", (source_path,)
        )
        self.conn.commit()

    def prune_missing(self, target_dir: Path, max_age_days: int = 30) -> int:
        """Remove entries for files that no longer exist at source, and delete
        their staged copies from target_dir so deleted files never get backed
        up. Only prunes entries older than max_age_days to avoid race
        conditions with in-flight scans."""
        cutoff = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(days=max_age_days)
        ).isoformat()
        rows = self.conn.execute(
            "SELECT id, source_path, target_path FROM synced_files WHERE copied_at < ?",
            (cutoff,),
        ).fetchall()

        target_root = target_dir.resolve()
        removed = 0
        for row_id, source_path, target_path in rows:
            if Path(source_path).exists():
                continue
            # Delete the staged copy if it is still a file under our target root.
            try:
                tgt = Path(target_path)
                if tgt.is_file():
                    tgt.resolve().relative_to(target_root)
                    tgt.unlink(missing_ok=True)
                    logger.info("Pruned staged copy of deleted source: %s", tgt)
            except (OSError, ValueError):
                pass
            self.conn.execute(
                "DELETE FROM synced_files WHERE id=?", (row_id,)
            )
            removed += 1

        if removed:
            self.conn.commit()
        return removed

    def stats(self) -> dict:
        """Return summary statistics."""
        total = self.conn.execute("SELECT COUNT(*) FROM synced_files").fetchone()[0]
        total_size = self.conn.execute(
            "SELECT COALESCE(SUM(file_size), 0) FROM synced_files"
        ).fetchone()[0]
        return {"total_files": total, "total_bytes": total_size}

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# ── File hashing ─────────────────────────────────────────────────────────────

def hash_file(filepath: Path, max_size_mb: int = 500) -> Optional[str]:
    """Compute SHA-256 hash of a file. Returns None if file is too large or unreadable."""
    max_bytes = max_size_mb * 1024 * 1024

    try:
        size = filepath.stat().st_size
        if size > max_bytes:
            logger.debug("Skipping large file: %s (%.1f MB)", filepath, size / 1e6)
            return None
        if size == 0:
            return "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # SHA-256 of empty

        sha = hashlib.sha256()
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(65536)  # 64KB chunks
                if not chunk:
                    break
                sha.update(chunk)
        return sha.hexdigest()
    except (PermissionError, OSError) as e:
        logger.debug("Cannot hash %s: %s", filepath, e)
        return None


def human_bytes(n: int) -> str:
    """Format byte count for display."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ── Scanner ──────────────────────────────────────────────────────────────────

def scan_sources(
    source_dirs: list[str],
    include_extensions: list[str],
    exclude_extensions: list[str],
    exclude_patterns: list[str],
    exclude_folder_patterns: list[str],
    exclude_folder_names: list[str],
    max_file_size_mb: int,
    min_age_seconds: float,
) -> Iterator[tuple[Path, float]]:
    """Yield (filepath, mtime) for eligible files in source directories.

    A file is eligible if:
    - It's a regular file (not a symlink or directory)
    - Its extension passes the include/exclude filters
    - Its name doesn't match any exclude pattern
    - It's not too large
    - It's old enough (min_age_seconds)

    exclude_patterns matches file names AND folder names (substring);
    exclude_folder_patterns matches FOLDER names only (used to skip whole
    app folders like LINE without dropping files whose names merely
    contain the word);
    exclude_folder_names matches folder names EXACTLY (case-insensitive,
    fnmatch), against both the folder name and its path relative to the
    source root, so nested patterns like "priv/static" work.
    """
    now = time.time()
    include_set = {e.lower() for e in include_extensions} if include_extensions else None
    exclude_set = {e.lower() for e in exclude_extensions}
    folder_name_patterns = [p.lower() for p in (exclude_folder_names or [])]

    for src_dir in source_dirs:
        root = Path(src_dir).expanduser().resolve()
        if not root.exists():
            logger.debug("Source directory not found: %s", root)
            continue
        if not root.is_dir():
            logger.debug("Source path is not a directory: %s", root)
            continue

        for dirpath_str, dirnames, filenames in os.walk(root, followlinks=False):
            # Filter directory names in-place to skip excluded dirs.
            # exclude_patterns applies here too (thumbnails etc.);
            # exclude_folder_patterns is folder-names-only (substring);
            # exclude_folder_names is exact-name matching.
            kept = []
            for d in dirnames:
                lower_d = d.lower()
                if any(pat.lower() in lower_d for pat in exclude_patterns):
                    continue
                if any(pat.lower() in lower_d for pat in exclude_folder_patterns):
                    continue
                if folder_name_patterns:
                    rel_d = os.path.relpath(
                        os.path.join(dirpath_str, d), root
                    ).replace(os.sep, "/").lower()
                    if any(
                        fnmatch.fnmatch(lower_d, pat)
                        or fnmatch.fnmatch(rel_d, pat)
                        for pat in folder_name_patterns
                    ):
                        continue
                kept.append(d)
            dirnames[:] = kept

            for fname in filenames:
                # Age check
                try:
                    st = os.stat(os.path.join(dirpath_str, fname))
                    age = now - st.st_mtime
                    if age < min_age_seconds:
                        continue
                except OSError:
                    continue

                # Regular file check
                if not os.path.isfile(os.path.join(dirpath_str, fname)):
                    continue

                # Extension filters
                ext = os.path.splitext(fname)[1].lower()
                if exclude_set and ext in exclude_set:
                    continue
                if include_set and ext not in include_set:
                    continue

                # Pattern filters on filename
                if any(pat.lower() in fname.lower() for pat in exclude_patterns):
                    continue

                # Size check
                if st.st_size > max_file_size_mb * 1024 * 1024:
                    continue

                yield (Path(dirpath_str) / fname, st.st_mtime)


# ── Cloud drive finder ──────────────────────────────────────────────────────

def find_cloud_drives(candidates: Optional[list] = None) -> list[Path]:
    """Search common locations for cloud drive folders (Google Drive, Proton
    Drive, Dropbox, iCloud, OneDrive, …). Returns list of found paths.

    candidates defaults to the built-in list; configs can override it with
    the cloud_drive_paths key.
    """
    import glob as glob_mod

    found = []
    for candidate in (candidates if candidates is not None else DEFAULT_CLOUD_DRIVE_CANDIDATES):
        expanded = os.path.expanduser(candidate)
        if "*" in expanded:
            matches = glob_mod.glob(expanded)
            for m in matches:
                p = Path(m)
                if p.exists() and p.is_dir():
                    found.append(p)
        else:
            p = Path(expanded)
            if p.exists() and p.is_dir():
                found.append(p)

    # Deduplicate resolved paths
    seen = set()
    unique = []
    for p in found:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return unique


def free_space_mb(path) -> Optional[int]:
    """Free space in MB on the volume containing path, or None if unknown."""
    try:
        target = Path(path)
        anchor = target if target.is_dir() else target.parent
        usage = shutil.disk_usage(anchor if anchor.exists() else anchor.parent)
        return usage.free // (1024 * 1024)
    except OSError:
        return None


# ── Main daemon ──────────────────────────────────────────────────────────────

class PhoneSync:
    """Monitors phone directories and syncs new files to your cloud drive."""

    def __init__(self, config_path: str):
        self.config = load_config(config_path)
        self.db = SyncDB(self.config.get("db_path", DEFAULT_DB))
        self._running = True
        self._scan_count = 0
        self._total_copied = 0
        self._total_bytes = 0

        # Resolve paths
        self.target_dir = Path(
            _expand_path(self.config["target_directory"])
        )

        self.source_dirs = [
            _expand_path(d) for d in self.config.get("source_directories", [])
        ]

        # Fail fast if the config still holds unedited template placeholders
        # (item 7).
        self._validate_template_paths()

        self.min_age = self.config.get("min_age_minutes", 10) * 60
        self.scan_interval = self.config.get("scan_interval_seconds", 60)
        self.max_size_mb = self.config.get("max_file_size_mb", 500)
        # Item 10: collapse foo (1).png rename families in the source dirs.
        # Off by default — it deletes the losing siblings. The collapse
        # respects min_age (grace period) so fresh files are never touched.
        self.collapse_rename_families = bool(
            self.config.get("collapse_rename_families", False)
        )
        # Item 13: app-generated names are held until renamed; leading
        # dates are normalised to YYYYMMDD on the staged copy.
        self.hold_app_generated_names = bool(
            self.config.get("hold_app_generated_names", True)
        )
        self.normalise_leading_dates = bool(
            self.config.get("normalise_leading_dates", True)
        )
        # Phone-side cloud sync: find cloud drives on the device and copy
        # new files into each one (under cloud_sync_subfolder). The
        # free-space guard applies to every copy target.
        self.sync_cloud_drives = bool(
            self.config.get("sync_cloud_drives", False)
        )
        self.cloud_sync_subfolder = str(
            self.config.get("cloud_sync_subfolder", "file_organizer")
        ) or "file_organizer"
        try:
            self.min_free_space_mb = int(
                self.config.get("min_free_space_mb", 200) or 0
            )
        except (TypeError, ValueError):
            self.min_free_space_mb = 200
        # Idle backoff (item 14).
        self.backoff_factor = int(
            self.config.get("idle_backoff_factor", 2) or 2
        )
        self.backoff_max = int(
            self.config.get("idle_backoff_max_multiplier", 8) or 8
        )
        self.idle_streak = 0
        self.exclude_folder_patterns = (
            self.config.get("exclude_folder_patterns", []) or []
        )
        # Safety defaults + user config lists, merged (user only adds).
        self.exclude_folder_names = _merged_list(
            DEFAULT_EXCLUDE_FOLDER_NAMES,
            self.config.get("exclude_folder_names"),
        )
        self.prune_max_age_days = int(
            self.config.get("prune_max_age_days", 30) or 30
        )

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        logger.info("Received signal %s — shutting down gracefully…", signum)
        self._running = False

    def _validate_template_paths(self) -> None:
        """Warn (never fail) about unedited template paths (revised item 7).

        Real missing folders are skipped silently at scan time (they come
        and go on phones). A configured path that contains a template
        placeholder (e.g. /Users/janedoe) and does not exist gets a LOUD
        warning so the user knows the config looks unedited — but the
        program keeps running and skips it.
        """
        problems = []
        target = self.config.get("target_directory")
        if target and not os.path.exists(_expand_path(target)) and any(
            m in str(target).lower() for m in PLACEHOLDER_MARKERS
        ):
            problems.append(f"target_directory: {target}")
        for raw in self.config.get("source_directories", []) or []:
            if not os.path.exists(_expand_path(raw)) and any(
                m in str(raw).lower() for m in PLACEHOLDER_MARKERS
            ):
                problems.append(f"source_directories: {raw}")
        if problems:
            msg = (
                "\n⚠️  your config still contains template placeholder "
                "paths that do not exist on this device — they are "
                "skipped:\n  "
                + "\n  ".join(problems)
                + "\n\nIf this is an unedited copy of "
                "config_template.yaml, edit phone_sync_config.yaml and "
                "set your real paths.\n"
            )
            logger.warning(msg)
            print(msg, file=sys.stderr)

    def _write_pid(self) -> None:
        pid_path = Path(_expand_path(self.config.get("pid_path", DEFAULT_PID)))
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(os.getpid()))

    def _remove_pid(self) -> None:
        pid_path = Path(_expand_path(self.config.get("pid_path", DEFAULT_PID)))
        pid_path.unlink(missing_ok=True)

    def copy_file(self, source: Path, dry_run: bool = False,
                  dest_name: Optional[str] = None,
                  target_root: Optional[Path] = None) -> Optional[Path]:
        """Copy a file to the target directory, preserving relative structure.

        The target path mirrors the source structure under target_dir.
        For example, DCIM/Camera/IMG_001.jpg → <target>/DCIM/Camera/IMG_001.jpg
        dest_name overrides the final filename (used by item 13's leading
        date normalisation: 2026-09-23-party.jpg → 20260923-party.jpg).
        target_root overrides the destination root (phone-side cloud sync:
        copies into a found cloud drive folder instead of staging).

        Returns the final target path on success (or the would-be target in
        dry-run mode), or None on failure. Copies are atomic: data is written
        to a '.partial' temp name and renamed into place, so concurrent
        readers (e.g. an ADB tar stream) never see a half-written file.
        """
        # Determine relative path: find which source dir the file is under
        source_resolved = source.resolve()
        rel_path = None

        for sd in self.source_dirs:
            sd_path = Path(sd).resolve()
            try:
                rel_path = source_resolved.relative_to(sd_path)
                break
            except ValueError:
                continue

        if rel_path is None:
            # File is not under any configured source dir — use just the filename
            rel_path = Path(source.name)

        if dest_name:
            rel_path = rel_path.parent / dest_name

        target = (target_root or self.target_dir) / rel_path

        if dry_run:
            logger.info("[DRY-RUN] Would copy: %s → %s", source, target)
            return target

        try:
            target.parent.mkdir(parents=True, exist_ok=True)

            # If target already exists, check if it's the same content
            if target.exists():
                if target.stat().st_size == source.stat().st_size:
                    src_hash = hash_file(source, self.max_size_mb)
                    tgt_hash = hash_file(target, self.max_size_mb)
                    if src_hash and tgt_hash and src_hash == tgt_hash:
                        logger.debug("Target already exists with same content: %s", target)
                        return target  # already synced, not an error

                # Target exists with different content. If this source was
                # staged before, this is an UPDATE: overwrite the previously
                # staged copy in place so the name stays stable across
                # versions. Otherwise it's a name collision from a DIFFERENT
                # source — keep the old suffix behaviour.
                known = self.db.latest_target_for_source(str(source))
                update_path = None
                if known is not None:
                    try:
                        cand = Path(_expand_path(known))
                        cand.resolve().relative_to(self.target_dir.resolve())
                        if cand.exists():
                            update_path = cand
                    except (ValueError, OSError):
                        pass
                if update_path is not None:
                    target = update_path
                    logger.info("Updating staged copy in place: %s", target)
                else:
                    # Item 10 — never create _1/_N renames. When a staged
                    # copy already exists, overwrite it only if the incoming
                    # file is NEWER and a DIFFERENT SIZE; otherwise keep the
                    # staged copy.
                    try:
                        tgt_stat = target.stat()
                        src_stat = source.stat()
                        newer = src_stat.st_mtime > tgt_stat.st_mtime
                        diff_size = src_stat.st_size != tgt_stat.st_size
                    except OSError:
                        newer = False
                        diff_size = False
                    if newer and diff_size:
                        logger.info(
                            "Overwriting staged copy (newer, different size): %s",
                            target,
                        )
                    elif newer:
                        # Same size but different content (identical content
                        # returned above): overwrite so the newer bytes get
                        # backed up. Still no _1 rename.
                        logger.info(
                            "Overwriting staged copy (newer content): %s", target
                        )
                    else:
                        logger.warning(
                            "Keeping existing staged copy (incoming is not "
                            "newer): %s — this variant is not backed up", source
                        )
                        return target

            # Atomic copy: write to a temp name, then rename into place.
            partial = target.parent / (target.name + ".partial")
            try:
                shutil.copy2(source, partial)
                os.replace(partial, target)
            finally:
                if partial.exists():
                    partial.unlink(missing_ok=True)
            logger.info("Copied: %s → %s (%.1f KB)",
                        source, target, source.stat().st_size / 1024)
            return target
        except (PermissionError, OSError) as e:
            logger.error("Failed to copy %s → %s: %s", source, target, e)
            return None

    def _collapse_rename_families_cycle(self, dry_run: bool = False) -> dict:
        """Item 10: collapse foo (1).png rename families in the source dirs.

        The newest member of each family becomes the base name and the
        other members are deleted. Families whose newest file is younger
        than min_age are skipped (grace period). Dry-run only plans.
        Returns stats {"renamed", "deleted"}.
        """
        stats = {"renamed": 0, "deleted": 0}
        if not self.collapse_rename_families:
            return stats
        mode = "DRY-RUN" if dry_run else "PRODUCTION"
        logger.info("=== Rename-family collapse (%s) ===", mode)
        for sd in self.source_dirs:
            root = Path(_expand_path(sd))
            if not root.is_dir():
                continue
            for dirpath, _dirnames, _filenames in os.walk(root):
                actions = plan_collapse(
                    dirpath, min_age_seconds=self.min_age
                )
                # Item 10: also delete identical-content '_N' duplicates
                # (foo_1.png whose foo.png exists). Content check protects
                # numbered files that are really different.
                actions.extend(plan_underscore_duplicates(
                    dirpath, min_age_seconds=self.min_age,
                ))
                if not actions:
                    continue
                for a in actions:
                    if dry_run:
                        if a.kind == "rename":
                            logger.info(
                                "[DRY-RUN] Would rename %s → %s",
                                a.path, a.target.name,
                            )
                        else:
                            logger.info("[DRY-RUN] Would delete %s", a.path)
                    else:
                        try:
                            if a.kind == "rename" and a.target:
                                os.replace(a.path, a.target)
                                logger.info("Renamed %s → %s", a.path, a.target.name)
                                stats["renamed"] += 1
                            elif a.kind == "delete":
                                a.path.unlink(missing_ok=True)
                                logger.info("Deleted %s", a.path)
                                stats["deleted"] += 1
                        except OSError as e:
                            logger.error("Collapse failed for %s: %s", a.path, e)
        if not dry_run and (stats["renamed"] or stats["deleted"]):
            logger.info("Collapse done: renamed=%d deleted=%d",
                        stats["renamed"], stats["deleted"])
        return stats

    def _scan_filters(self):
        """Extension/pattern filters shared by the staging and cloud scans."""
        include_ext = self.config.get("include_extensions", []) or []
        exclude_ext = _merged_list(
            DEFAULT_EXCLUDE_EXTENSIONS, self.config.get("exclude_extensions")
        )
        exclude_pat = _merged_list(
            DEFAULT_EXCLUDE_PATTERNS, self.config.get("exclude_patterns")
        )
        return include_ext, exclude_ext, exclude_pat

    def _sync_cloud_drives_cycle(self, dry_run: bool = False) -> dict:
        """Look for cloud drive folders on the device and copy new files
        from the source directories into each one (under
        cloud_sync_subfolder), so files reach the cloud even without the
        computer. The free-space guard applies to every copy. Returns
        stats {"found", "copied", "skipped"}.
        """
        stats = {"found": 0, "copied": 0, "skipped": 0}
        if not self.sync_cloud_drives:
            return stats

        candidates = self.config.get("cloud_drive_paths") or \
            DEFAULT_CLOUD_DRIVE_CANDIDATES
        drives = find_cloud_drives(candidates)
        if not drives:
            logger.info("Cloud sync: no cloud drive folders found on this device")
            return stats

        include_ext, exclude_ext, exclude_pat = self._scan_filters()
        for drive in drives:
            # Never sync back into the staging folder itself (loop guard).
            try:
                dr = drive.resolve()
                if dr == self.target_dir.resolve() or \
                        self.target_dir.resolve() in dr.parents:
                    continue
            except OSError:
                pass
            stats["found"] += 1
            dest_root = drive / self.cloud_sync_subfolder
            logger.info("Cloud sync: copying new files to %s", dest_root)
            for filepath, mtime in scan_sources(
                self.source_dirs, include_ext, exclude_ext,
                exclude_pat, self.exclude_folder_patterns,
                self.exclude_folder_names, self.max_size_mb, self.min_age,
            ):
                if self.hold_app_generated_names and \
                        is_app_generated(filepath.name):
                    continue  # item 13 — held until renamed
                file_hash = hash_file(filepath, self.max_size_mb)
                if file_hash is None:
                    continue
                size = filepath.stat().st_size
                dest_name = None
                if self.normalise_leading_dates:
                    new_name = normalise_leading_date(filepath.name)
                    if new_name != filepath.name:
                        dest_name = new_name
                target_path = self.copy_file(
                    filepath, dry_run=dry_run, dest_name=dest_name,
                    target_root=dest_root,
                )
                if target_path is None:
                    continue
                if self.db.is_synced(str(filepath), file_hash, size,
                                     target_path=str(target_path)):
                    stats["skipped"] += 1
                    continue
                if not dry_run:
                    self.db.mark_synced(
                        str(filepath), str(target_path),
                        file_hash, size, mtime,
                    )
                stats["copied"] += 1
        return stats

    def scan_and_sync(self, dry_run: bool = False) -> dict:
        """Run one scan-and-sync cycle. Returns stats dict."""
        stats = {"scanned": 0, "copied": 0, "skipped": 0, "errors": 0,
                 "bytes": 0, "held": 0, "low_disk_space": 0}

        # Item 10: (N)-family collapse before copying, when enabled.
        collapse = self._collapse_rename_families_cycle(dry_run=dry_run)
        stats["collapsed"] = collapse["renamed"] + collapse["deleted"]

        include_ext, exclude_ext, exclude_pat = self._scan_filters()

        # Free-space guard: report free space and stop the cycle when the
        # target volume has less than min_free_space_mb left.
        free_mb = free_space_mb(self.target_dir)
        if free_mb is not None:
            logger.info("Free space on target volume: %d MB", free_mb)

        for filepath, mtime in scan_sources(
            self.source_dirs, include_ext, exclude_ext,
            exclude_pat, self.exclude_folder_patterns, self.exclude_folder_names,
            self.max_size_mb, self.min_age,
        ):
            stats["scanned"] += 1

            # Free-space guard — never fill the phone up.
            if not dry_run and self.min_free_space_mb > 0:
                free_mb = free_space_mb(self.target_dir)
                if free_mb is not None and free_mb < self.min_free_space_mb:
                    stats["low_disk_space"] += 1
                    logger.warning(
                        "Low disk space (%d MB free < %d MB) — stopping this "
                        "cycle before %s",
                        free_mb, self.min_free_space_mb, filepath,
                    )
                    break

            # Item 13: hold app-generated names until the user renames them.
            if self.hold_app_generated_names and is_app_generated(filepath.name):
                stats["held"] += 1
                logger.info(
                    "Held until renamed (app-generated name): %s", filepath
                )
                continue

            # Hash the file
            file_hash = hash_file(filepath, self.max_size_mb)
            if file_hash is None:
                stats["skipped"] += 1
                continue

            size = filepath.stat().st_size

            # Check if already synced
            if self.db.is_synced(str(filepath), file_hash, size):
                stats["skipped"] += 1
                continue

            # Copy it. Item 13: normalise a leading date (2026-09-23-party.jpg
            # → 20260923-party.jpg) on the STAGED copy; the source keeps its
            # name.
            dest_name = None
            if self.normalise_leading_dates:
                new_name = normalise_leading_date(filepath.name)
                if new_name != filepath.name:
                    dest_name = new_name
            target_path = self.copy_file(filepath, dry_run=dry_run,
                                         dest_name=dest_name)
            if target_path is not None:
                if not dry_run:
                    self.db.mark_synced(
                        str(filepath), str(target_path),
                        file_hash, size, mtime,
                    )
                stats["copied"] += 1
                stats["bytes"] += size
            else:
                stats["errors"] += 1

        # Phone-side cloud sync: found cloud drive folders each get a copy
        # of new files (when sync_cloud_drives is on).
        cloud = self._sync_cloud_drives_cycle(dry_run=dry_run)
        stats["cloud_found"] = cloud["found"]
        stats["cloud_copied"] = cloud["copied"]

        return stats

    def run_once(self, dry_run: bool = False) -> dict:
        """Run a single scan cycle. Returns stats."""
        mode = "DRY-RUN" if dry_run else "PRODUCTION"
        logger.info("=== Phone Sync scan (%s) ===", mode)
        logger.info("Source dirs: %s", self.source_dirs)
        logger.info("Target dir:  %s", self.target_dir)
        logger.info("Min age:     %d minutes", self.min_age // 60)
        logger.info("DB entries:  %d synced files", self.db.stats()["total_files"])

        stats = self.scan_and_sync(dry_run=dry_run)

        logger.info(
            "Scan complete: scanned=%d copied=%d skipped=%d held=%d "
            "errors=%d bytes=%s",
            stats["scanned"], stats["copied"], stats["skipped"],
            stats["held"], stats["errors"], human_bytes(stats["bytes"]),
        )

        # Prune stale entries periodically (deletes staged copies too)
        pruned = self.db.prune_missing(
            self.target_dir, max_age_days=self.prune_max_age_days
        )
        if pruned:
            logger.info("Pruned %d stale DB entries (files no longer exist)", pruned)

        return stats

    def run_daemon(self) -> None:
        """Run continuously with a polling interval."""
        self._write_pid()
        logger.info("Phone Sync started (PID %d)", os.getpid())
        logger.info("Scan interval: %d seconds", self.scan_interval)

        try:
            while self._running:
                self._scan_count += 1
                logger.info("--- Scan cycle #%d ---", self._scan_count)

                stats = self.scan_and_sync(dry_run=False)

                self._total_copied += stats["copied"]
                self._total_bytes += stats["bytes"]

                db_stats = self.db.stats()
                logger.info(
                    "Cycle #%d done: copied=%d, total_copied=%d, total_bytes=%s, db_size=%d",
                    self._scan_count, stats["copied"],
                    self._total_copied, human_bytes(self._total_bytes),
                    db_stats["total_files"],
                )

                # Idle backoff (item 14): cycles that copy nothing sleep
                # progressively longer, up to scan_interval * backoff_max.
                if stats["copied"] == 0:
                    self.idle_streak += 1
                else:
                    self.idle_streak = 0
                sleep_for = compute_idle_sleep(
                    self.scan_interval, self.backoff_factor,
                    self.backoff_max, self.idle_streak,
                )
                logger.info(
                    "Sleeping %ds (idle streak %d)",
                    sleep_for, self.idle_streak,
                )

                # Sleep in small increments so we can respond to signals
                if self._running:
                    for _ in range(sleep_for):
                        if not self._running:
                            break
                        time.sleep(1)

        except Exception as e:
            logger.exception("Phone sync error: %s", e)
        finally:
            self._remove_pid()
            self.db.close()
            logger.info("Phone Sync stopped. Total copied: %d files, %s",
                        self._total_copied, human_bytes(self._total_bytes))


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Phone Sync - monitor and sync phone files to your cloud drive",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python3 phone_sync.py --scan-once --dry-run     # test without copying
  python3 phone_sync.py --scan-once               # one-shot real run
  python3 phone_sync.py --background              # start in the background
  python3 phone_sync.py --find-cloud-drives       # locate your cloud drive folders
  python3 phone_sync.py --config my_config.yaml # use custom config
        """,
    )
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG,
        help=f"Config file path (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--scan-once", action="store_true",
        help="Run a single scan cycle then exit",
    )
    parser.add_argument(
        "--background", "--daemon", dest="background", action="store_true",
        help="Start as a background process (forks to background). "
             "--daemon is the old name and still works.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scan but do not actually copy files",
    )
    parser.add_argument(
        "--find-cloud-drives", dest="find_cloud_drives",
        action="store_true",
        help="Search for cloud drive folders (Google Drive, Proton Drive, "
             "Dropbox, iCloud, OneDrive, …) and exit",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Show sync database statistics and exit",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Check if phone sync is running and exit",
    )
    parser.add_argument(
        "--stop", action="store_true",
        help="Stop a running phone sync",
    )
    parser.add_argument(
        "--make-config", action="store_true",
        help="Create phone_sync_config.yaml from the template if missing, then exit",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose logging",
    )
    args = parser.parse_args(argv)
    args.config = resolve_config_path(args.config)

    # ── Special commands ──

    if args.make_config:
        cfg = load_config(args.config)
        print(f"Config ready at {args.config} "
              f"({len(cfg)} settings). Edit it and run again.")
        return

    if args.find_cloud_drives:
        cfg = load_config(args.config)
        candidates = cfg.get("cloud_drive_paths") or DEFAULT_CLOUD_DRIVE_CANDIDATES
        found = find_cloud_drives(candidates)
        if found:
            print(f"Found {len(found)} cloud drive folder(s):")
            for p in found:
                print(f"  {p}")
        else:
            print("No cloud drive folders found at common locations.")
            print("Checked:")
            for c in candidates:
                print(f"  {c}")
            print("\nTip: Manually set target_directory in phone_sync_config.yaml")
            print("Tip: Or add your path to cloud_drive_paths in that file.")
        return

    if args.stats:
        cfg = load_config(args.config)
        db = SyncDB(cfg.get("db_path", DEFAULT_DB))
        s = db.stats()
        print(f"Sync database: {s['total_files']} files, {human_bytes(s['total_bytes'])}")
        db.close()
        return

    if args.status:
        cfg = load_config(args.config)
        pid_path = Path(_expand_path(cfg.get("pid_path", DEFAULT_PID)))
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text().strip())
                os.kill(pid, 0)  # signal 0 = check existence
                print(f"Phone sync is running (PID {pid})")
            except (OSError, ValueError):
                print("Phone sync is NOT running (stale PID file)")
                pid_path.unlink(missing_ok=True)
        else:
            print("Phone sync is NOT running")
        return

    if args.stop:
        cfg = load_config(args.config)
        pid_path = Path(_expand_path(cfg.get("pid_path", DEFAULT_PID)))
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                print(f"Sent SIGTERM to PID {pid}")
                time.sleep(1)
                try:
                    os.kill(pid, 0)
                    os.kill(pid, signal.SIGKILL)
                    print(f"Force-killed PID {pid}")
                except OSError:
                    print(f"Phone sync stopped (PID {pid})")
            except (OSError, ValueError):
                print("Stale PID file — removing")
            pid_path.unlink(missing_ok=True)
        else:
            print("No PID file found — phone sync is not running")
        return

    # ── Normal operation ──

    setup_logging(
        _expand_path(load_config(args.config).get("log_path", DEFAULT_LOG)),
        verbose=args.verbose,
    )

    sync = PhoneSync(args.config)

    if args.scan_once:
        sync.run_once(dry_run=args.dry_run)
    elif args.background:
        # Fork to background
        pid = os.fork()
        if pid > 0:
            print(f"Phone sync started (PID {pid})")
            sys.exit(0)
        # Child continues
        os.setsid()
        # Redirect stdio
        sys.stdout = open(os.devnull, "w")
        sys.stderr = open(os.devnull, "w")
        sys.stdin = open(os.devnull, "r")
        # Remove console handler, keep file handler
        for h in logger.handlers[:]:
            if isinstance(h, logging.StreamHandler) and h.stream in (sys.stdout, sys.stderr):
                logger.removeHandler(h)
        sync.run_daemon()
    else:
        # Foreground (default)
        sync.run_daemon()


if __name__ == "__main__":
    main()

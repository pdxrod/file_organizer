#!/usr/bin/env python3
"""
Phone Daemon — lightweight file monitor and sync for Android (Termux) or macOS.

Monitors configured source directories and copies new/changed files to a
target Proton Drive folder. Tracks copied files by content hash (SHA-256)
so renames and moves are handled correctly.

Key features:
- Content-hash tracking (SQLite) — never copies the same file twice
- min_age_minutes grace period — gives you time to delete unwanted photos
  before they get backed up (solves the Proton Drive "too eager" problem)
- Configurable source directories and target path
- Dry-run mode for safe testing
- Runs on Android via Termux, or on macOS
- PID-file daemon management for start/stop/status

Usage:
    python3 phone_daemon.py              # start daemon (foreground)
    python3 phone_daemon.py --daemon     # start daemon (background)
    python3 phone_daemon.py --scan-once  # single scan then exit
    python3 phone_daemon.py --dry-run    # scan but don't copy
    python3 phone_daemon.py --find-proton  # locate Proton Drive folder
    python3 phone_daemon.py --config /path/to/config.yaml
"""

import os
import sys
import json
import time
import signal
import hashlib
import sqlite3
import logging
import argparse
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Iterator

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = "phone_daemon_config.yaml"
DEFAULT_DB = "~/.phone_daemon.db"
DEFAULT_LOG = "~/.phone_daemon.log"
DEFAULT_PID = "~/.phone_daemon.pid"

# Common Proton Drive paths on various platforms
PROTON_CANDIDATES = [
    # macOS
    "~/Library/CloudStorage/ProtonDrive-*",
    # Android — shared storage
    "/storage/emulated/0/ProtonDrive",
    "/storage/emulated/0/Proton Drive",
    "/storage/emulated/0/Documents/ProtonDrive",
    "/storage/emulated/0/Download/ProtonDrive",
    # Android — app media (scoped storage)
    "/storage/emulated/0/Android/media/com.protontech.android.drive",
    # Android — app data (may need root)
    "/storage/emulated/0/Android/data/com.protontech.android.drive/files",
    # Termux shared
    "~/storage/shared/ProtonDrive",
    "~/storage/shared/Proton Drive",
]

# ── Logging ──────────────────────────────────────────────────────────────────

logger = logging.getLogger("phone_daemon")


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


def load_config(path: str) -> dict:
    """Load config from YAML (preferred) or JSON file. Returns dict."""
    config_path = Path(path).expanduser().resolve()

    if not config_path.exists():
        logger.warning(
            "No config found at %s — using built-in defaults. "
            "Copy phone_daemon_config.template.yaml to phone_daemon_config.yaml "
            "to set your own paths.",
            config_path,
        )
        return _default_config()

    content = config_path.read_text(encoding="utf-8")

    # Try YAML first
    try:
        import yaml
        cfg = yaml.safe_load(content)
        if cfg:
            return cfg
    except ImportError:
        pass
    except Exception as e:
        logger.warning("YAML parse failed (%s), trying JSON…", e)

    # Fall back to JSON
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        logger.error("Cannot parse config as YAML or JSON: %s", e)
        sys.exit(1)


def resolve_config_path(requested: str) -> str:
    """Resolve the requested config path.

    If the path is a bare relative filename that does not exist in the
    current working directory, fall back to the same filename next to this
    script. This lets `python3 /path/to/phone_daemon.py` find a config that
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
        "max_file_size_mb": 500,
        "include_extensions": [],  # empty = all files
        "exclude_extensions": [
            ".tmp", ".temp", ".partial", ".crdownload", ".part",
        ],
        "exclude_patterns": [
            ".thumbnails", ".thumbdata", ".face", ".pending",
            ".trashed", ".trash", "thumbdata", ".cache",
        ],
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
                UNIQUE(source_path, content_hash)
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_synced_hash
            ON synced_files(content_hash, file_size)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_synced_source
            ON synced_files(source_path)
        """)
        self.conn.commit()

    def is_synced(self, source_path: str, content_hash: str, file_size: int) -> bool:
        """Check if a file with this hash+size has already been synced from this path."""
        row = self.conn.execute(
            "SELECT 1 FROM synced_files WHERE source_path=? AND content_hash=? AND file_size=?",
            (source_path, content_hash, file_size),
        ).fetchone()
        return row is not None

    def mark_synced(
        self, source_path: str, target_path: str,
        content_hash: str, file_size: int, file_mtime: float,
    ) -> None:
        """Record a successful sync."""
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """INSERT OR REPLACE INTO synced_files
               (source_path, target_path, content_hash, file_size, file_mtime, copied_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (source_path, target_path, content_hash, file_size, file_mtime, now),
        )
        self.conn.commit()

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
        cutoff = (datetime.utcnow() - timedelta(days=max_age_days)).isoformat()
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
    """
    now = time.time()
    include_set = {e.lower() for e in include_extensions} if include_extensions else None
    exclude_set = {e.lower() for e in exclude_extensions}

    for src_dir in source_dirs:
        root = Path(src_dir).expanduser().resolve()
        if not root.exists():
            logger.debug("Source directory not found: %s", root)
            continue
        if not root.is_dir():
            logger.debug("Source path is not a directory: %s", root)
            continue

        for dirpath_str, dirnames, filenames in os.walk(root, followlinks=False):
            # Filter directory names in-place to skip excluded dirs
            dirnames[:] = [
                d for d in dirnames
                if not any(
                    pat.lower() in d.lower()
                    for pat in exclude_patterns
                )
            ]

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


# ── Proton Drive finder ──────────────────────────────────────────────────────

def find_proton_drive() -> list[Path]:
    """Search common locations for Proton Drive folder. Returns list of found paths."""
    import glob as glob_mod

    found = []
    for candidate in PROTON_CANDIDATES:
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


# ── Main daemon ──────────────────────────────────────────────────────────────

class PhoneDaemon:
    """Monitors phone directories and syncs new files to Proton Drive."""

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

        self.min_age = self.config.get("min_age_minutes", 10) * 60
        self.scan_interval = self.config.get("scan_interval_seconds", 60)
        self.max_size_mb = self.config.get("max_file_size_mb", 500)

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        logger.info("Received signal %s — shutting down gracefully…", signum)
        self._running = False

    def _write_pid(self) -> None:
        pid_path = Path(_expand_path(self.config.get("pid_path", DEFAULT_PID)))
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(os.getpid()))

    def _remove_pid(self) -> None:
        pid_path = Path(_expand_path(self.config.get("pid_path", DEFAULT_PID)))
        pid_path.unlink(missing_ok=True)

    def copy_file(self, source: Path, dry_run: bool = False) -> Optional[Path]:
        """Copy a file to the target directory, preserving relative structure.

        The target path mirrors the source structure under target_dir.
        For example, DCIM/Camera/IMG_001.jpg → <target>/DCIM/Camera/IMG_001.jpg

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

        target = self.target_dir / rel_path

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
                # Different content — rename with a suffix
                stem = target.stem
                suffix = target.suffix
                counter = 1
                while target.exists():
                    target = target.parent / f"{stem}_{counter}{suffix}"
                    counter += 1

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

    def scan_and_sync(self, dry_run: bool = False) -> dict:
        """Run one scan-and-sync cycle. Returns stats dict."""
        stats = {"scanned": 0, "copied": 0, "skipped": 0, "errors": 0, "bytes": 0}

        include_ext = self.config.get("include_extensions", []) or []
        exclude_ext = self.config.get("exclude_extensions", []) or []
        exclude_pat = self.config.get("exclude_patterns", []) or []

        for filepath, mtime in scan_sources(
            self.source_dirs, include_ext, exclude_ext,
            exclude_pat, self.max_size_mb, self.min_age,
        ):
            stats["scanned"] += 1

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

            # Copy it
            target_path = self.copy_file(filepath, dry_run=dry_run)
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

        return stats

    def run_once(self, dry_run: bool = False) -> dict:
        """Run a single scan cycle. Returns stats."""
        mode = "DRY-RUN" if dry_run else "PRODUCTION"
        logger.info("=== Phone Daemon scan (%s) ===", mode)
        logger.info("Source dirs: %s", self.source_dirs)
        logger.info("Target dir:  %s", self.target_dir)
        logger.info("Min age:     %d minutes", self.min_age // 60)
        logger.info("DB entries:  %d synced files", self.db.stats()["total_files"])

        stats = self.scan_and_sync(dry_run=dry_run)

        logger.info(
            "Scan complete: scanned=%d copied=%d skipped=%d errors=%d bytes=%s",
            stats["scanned"], stats["copied"], stats["skipped"],
            stats["errors"], human_bytes(stats["bytes"]),
        )

        # Prune stale entries periodically (deletes staged copies too)
        pruned = self.db.prune_missing(self.target_dir, max_age_days=30)
        if pruned:
            logger.info("Pruned %d stale DB entries (files no longer exist)", pruned)

        return stats

    def run_daemon(self) -> None:
        """Run continuously with a polling interval."""
        self._write_pid()
        logger.info("Phone Daemon started (PID %d)", os.getpid())
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

                # Sleep in small increments so we can respond to signals
                if self._running:
                    for _ in range(self.scan_interval):
                        if not self._running:
                            break
                        time.sleep(1)

        except Exception as e:
            logger.exception("Daemon error: %s", e)
        finally:
            self._remove_pid()
            self.db.close()
            logger.info("Phone Daemon stopped. Total copied: %d files, %s",
                        self._total_copied, human_bytes(self._total_bytes))


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Phone Daemon - monitor and sync phone files to Proton Drive",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python3 phone_daemon.py --scan-once --dry-run   # test without copying
  python3 phone_daemon.py --scan-once             # one-shot real run
  python3 phone_daemon.py --daemon                # start background daemon
  python3 phone_daemon.py --find-proton           # locate Proton Drive folder
  python3 phone_daemon.py --config my_config.yaml # use custom config
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
        "--daemon", action="store_true",
        help="Start as background daemon (forks to background)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scan but do not actually copy files",
    )
    parser.add_argument(
        "--find-proton", action="store_true",
        help="Search for Proton Drive folder and exit",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Show sync database statistics and exit",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Check if daemon is running and exit",
    )
    parser.add_argument(
        "--stop", action="store_true",
        help="Stop a running daemon",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose logging",
    )
    args = parser.parse_args()
    args.config = resolve_config_path(args.config)

    # ── Special commands (no config needed) ──

    if args.find_proton:
        found = find_proton_drive()
        if found:
            print(f"Found {len(found)} Proton Drive location(s):")
            for p in found:
                print(f"  {p}")
        else:
            print("No Proton Drive folder found at common locations.")
            print("Checked:")
            for c in PROTON_CANDIDATES:
                print(f"  {c}")
            print("\nTip: Manually set target_directory in phone_daemon_config.yaml")
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
                print(f"Daemon is running (PID {pid})")
            except (OSError, ValueError):
                print("Daemon is NOT running (stale PID file)")
                pid_path.unlink(missing_ok=True)
        else:
            print("Daemon is NOT running")
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
                    print(f"Daemon stopped (PID {pid})")
            except (OSError, ValueError):
                print("Stale PID file — removing")
            pid_path.unlink(missing_ok=True)
        else:
            print("No PID file found — daemon is not running")
        return

    # ── Normal operation ──

    setup_logging(
        _expand_path(load_config(args.config).get("log_path", DEFAULT_LOG)),
        verbose=args.verbose,
    )

    daemon = PhoneDaemon(args.config)

    if args.scan_once:
        daemon.run_once(dry_run=args.dry_run)
    elif args.daemon:
        # Fork to background
        pid = os.fork()
        if pid > 0:
            print(f"Daemon started (PID {pid})")
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
        daemon.run_daemon()
    else:
        # Foreground daemon (default)
        daemon.run_daemon()


if __name__ == "__main__":
    main()

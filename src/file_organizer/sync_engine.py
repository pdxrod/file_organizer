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
        # Item 11 fix: cloud mounts (Proton Drive placeholders) hang when
        # rsync uses the mmap-heavy delta algorithm. Whole-file transfer
        # streams the LOCAL source into a fresh file on the cloud side
        # without reading placeholder content — so it is the default.
        if "rsync_whole_file" in config.raw_config:
            self._whole_file: bool = bool(config.raw_config.get("rsync_whole_file"))
        else:
            # Legacy key: rsync_disable_mmap used to add --no-whole-file
            # (delta), which triggered 'mmap: Operation timed out' on
            # Proton placeholders. Reinterpret it as whole-file.
            self._whole_file = bool(config.raw_config.get("rsync_disable_mmap", True))
        # Per-I/O stall guard (rsync --timeout): a stalled placeholder read
        # fails in minutes instead of hanging the whole sync hour. 0 = off.
        try:
            io = config.raw_config.get("rsync_io_timeout_seconds", 300)
            self._io_timeout: int = int(io) if io is not None else 300
        except (TypeError, ValueError):
            self._io_timeout = 300
        self._timeout: int = (config.raw_config.get("sync_timeout_minutes", 60) or 60) * 60
        self._chunk_subfolders: int = config.raw_config.get("sync_chunk_subfolders", 30)
        self._exclude_patterns: list[str] = config.raw_config.get("exclude_patterns", []) or []
        # Direct subfolder NAMES never synced (item: organizers must not
        # mirror their own checkouts between machines). Applied by the
        # chunker and as --exclude /name/ in every rsync run.
        self._exclude_folder_names: list[str] = \
            config.raw_config.get("sync_exclude_folder_names", []) or []
        # Cross-device sync coordination (see _shared_lock_dir).
        self._lock_enabled: bool = bool(
            config.raw_config.get("sync_lock_enabled", True)
        )
        self._lock_stale_seconds: int = int(
            config.raw_config.get("sync_lock_stale_minutes", 120) or 0
        ) * 60
        # Number of files/dirs rsync actually transferred in the most recent
        # run_all_sync_pairs() call. Used for idle backoff (item 14).
        self.last_change_count: int = 0

    # ── cross-device sync lock ────────────────────────────────────────────

    SYNC_LOCK_NAME = ".file_organizer.sync.lock"
    # Path fragments that mark a folder as "shared with other devices":
    # removable volumes and cloud-sync app folders. The lock goes there so
    # every machine looking at the pair sees the same marker.
    SHARED_MARKERS = (
        "/Volumes/", "/media/", "/mnt/",
        "CloudStorage", "GoogleDrive", "Google Drive",
        "Dropbox", "OneDrive", "iCloud", "Mobile Documents",
    )

    def _shared_lock_dir(self, folder_a: Path, folder_b: Path) -> "Optional[Path]":
        """The pair folder both machines can see, or None for local-only pairs.

        The lock file must live where every device syncing the pair will
        find it: a removable volume or a cloud-sync folder. Local-only
        pairs (both folders on private disks) can't clash across devices,
        so they get no lock. Only EXISTING folders are returned — a lock
        must never create directory trees (an unmounted /Volumes/X path
        must not materialise on the root disk).
        """
        for folder in (folder_a, folder_b):
            if not folder.is_dir():
                continue
            text = str(folder)
            if any(m in text for m in self.SHARED_MARKERS):
                return folder
        home = str(Path.home())
        for folder in (folder_a, folder_b):
            if folder.is_dir() and not str(folder).startswith(home):
                return folder
        return None

    def _lock_payload(self) -> str:
        import json
        import socket
        return json.dumps({
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "started": int(time.time()),
        })

    def _acquire_lock(self, lock_dir: Path) -> bool:
        """Try to take the pair's lock. Returns True when we hold it.

        Atomic O_CREAT|O_EXCL: the first machine to create the marker wins;
        everyone else skips the pair this cycle and tries again later
        ("whoever started first finishes first"). A lock older than
        sync_lock_stale_minutes — a crashed run — is removed and taken
        over.
        """
        lock_path = lock_dir / self.SYNC_LOCK_NAME
        for _ in range(2):
            try:
                fd = os.open(str(lock_path),
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as f:
                    f.write(self._lock_payload())
                logger.info("Sync lock taken: %s", lock_path)
                return True
            except FileExistsError:
                holder = ""
                try:
                    holder = lock_path.read_text(errors="replace").strip() or ""
                except OSError:
                    pass
                try:
                    age = time.time() - lock_path.stat().st_mtime
                except OSError:
                    age = 0
                if self._lock_stale_seconds > 0 and \
                        age > self._lock_stale_seconds:
                    logger.warning(
                        "Removing stale sync lock (%s, %.0f min old, holder: %s)",
                        lock_path, age / 60, holder or "unknown",
                    )
                    try:
                        lock_path.unlink()
                    except OSError:
                        return False
                    continue  # retry the atomic create once
                logger.warning(
                    "Another device is syncing this pair (lock %s, holder: %s) "
                    "— skipping it this cycle; a later cycle will pick it up.",
                    lock_path, holder or "unknown",
                )
                return False
            except OSError as e:
                logger.warning("Could not take sync lock %s: %s", lock_path, e)
                return False
        return False

    def _release_lock(self, lock_dir: Path) -> None:
        """Drop the lock when the pair is done (best effort)."""
        try:
            (lock_dir / self.SYNC_LOCK_NAME).unlink()
        except OSError:
            pass

    def _build_rsync_args(self, source: Path, target: Path, delete: bool = False,
                          exclude_subdirs=None) -> list[str]:
        """Build rsync argument list."""
        args = ["rsync", "-av", "--progress"]

        # One line per transferred/updated item — used to detect whether a
        # sync actually did anything (idle backoff, item 14).
        args.append("--itemize-changes")

        # Item 11 fix: whole-file transfer by default so cloud placeholder
        # files are never mmap'ed/read on the receiving side. Delta mode is
        # opt-in via rsync_whole_file: false.
        args.append("--whole-file" if self._whole_file else "--no-whole-file")

        # Stall guard: fail fast when a cloud read hangs (item 11).
        if self._io_timeout > 0:
            args.append(f"--timeout={self._io_timeout}")

        # Checksum mode
        if self._checksum_mode == "checksum":
            args.append("--checksum")
        elif self._size_only:
            args.append("--size-only")

        # Chunking: skip immediate subfolders handled by their own rsync runs.
        for name in (exclude_subdirs or []):
            args.extend(["--exclude", f"/{name}/"])

        # sync_exclude_folder_names: never sync these subfolders at all
        # (e.g. the file_organizer checkouts inside a synced tree).
        for name in self._exclude_folder_names:
            args.extend(["--exclude", f"/{name}/"])

        # Exclude patterns from config. Version-control folders (.git etc.)
        # are part of the built-in exclude_patterns safety defaults — they
        # are always merged in by Config.load(), so no separate hard-coded
        # list is needed here.
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

    def _run_rsync(self, source: Path, target: Path, delete: bool = False,
                    exclude_subdirs=None) -> bool:
        """Run rsync from source to target. Returns True on success."""
        args = self._build_rsync_args(source, target, delete, exclude_subdirs)

        # Ensure target directory exists
        target.mkdir(parents=True, exist_ok=True)

        logger.info("Syncing: %s → %s", source, target)
        logger.debug("rsync args: %s", " ".join(args))

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                timeout=self._timeout,
            )
            if result.returncode == 0:
                changed = self._count_itemized_changes(result.stdout)
                self.last_change_count += changed
                logger.debug("Sync complete: %s → %s (%d changes)",
                             source, target, changed)
                return True
            else:
                stderr_text = result.stderr.decode('utf-8', errors='replace') if result.stderr else ''
                logger.error("rsync failed (%d): %s", result.returncode, stderr_text[:500])
                return False
        except subprocess.TimeoutExpired:
            logger.error("rsync timed out after %ds: %s → %s", self._timeout, source, target)
            return False
        except KeyboardInterrupt:
            raise
        except FileNotFoundError:
            logger.error("rsync not found — please install rsync")
            return False

    def _sync_subdirs(self, source: Path) -> list:
        """Direct subfolder names eligible for sync, excluding folders the
        user asked never to sync (sync_exclude_folder_names — e.g. the
        file_organizer checkouts themselves, so the organizers never
        mirror their own source code between machines).
        """
        try:
            subdirs = sorted(
                p.name for p in source.iterdir() if p.is_dir()
            )
        except OSError:
            return []
        return [n for n in subdirs if n not in self._exclude_folder_names]

    def _run_rsync_many(self, source: Path, target: Path,
                        delete: bool = False) -> bool:
        """Item 11 fix: sync a folder, splitting it into per-subfolder rsync
        runs when it has more than sync_chunk_subfolders direct subfolders.

        Huge trees (e.g. MAIN_DRIVE/dev with hundreds of thousands of files)
        used to run as ONE rsync that exceeded the subprocess timeout — one
        stuck cloud placeholder killed the whole pair. Chunking bounds each
        rsync's work and its timeout, and one failed chunk no longer
        prevents the rest from syncing.
        """
        subdirs = []
        if self._chunk_subfolders and source.is_dir():
            subdirs = self._sync_subdirs(source)

        if len(subdirs) <= self._chunk_subfolders:
            return self._run_rsync(source, target, delete=delete)

        logger.info("Chunked sync: %d subfolders of %s", len(subdirs), source)
        all_ok = True
        for name in subdirs:
            ok = self._run_rsync(
                source / name, target / name, delete=delete
            )
            if not ok:
                all_ok = False
        # Loose files at the top level (the subfolders are excluded here so
        # their contents are not transferred twice).
        ok = self._run_rsync(
            source, target, delete=delete, exclude_subdirs=subdirs
        )
        return all_ok and ok

    def _count_itemized_changes(self, stdout: bytes) -> int:
        """Count rsync --itemize-changes lines in captured output.

        Itemize lines start with < > c h . or * (e.g. ">f+++++++++ file").
        Summary lines ("sent …", "total size …") start with letters and are
        ignored.
        """
        text = stdout.decode("utf-8", errors="replace") if stdout else ""
        count = 0
        for line in text.splitlines():
            if line and line[0] in "<>ch.*":
                count += 1
        return count

    def sync_bidirectional(self, folder_a: Path, folder_b: Path) -> dict:
        """Bidirectional sync: copy new/changed files in both directions."""
        result = {"a_to_b": False, "b_to_a": False}

        # Sync A → B
        result["a_to_b"] = self._run_rsync_many(folder_a, folder_b)

        # Sync B → A
        result["b_to_a"] = self._run_rsync_many(folder_b, folder_a)

        return result

    def sync_one_way(self, source: Path, target: Path, delete: bool = False) -> bool:
        """One-way sync: copy new/changed files from source to target only."""
        return self._run_rsync_many(source, target, delete=delete)

    def run_all_sync_pairs(self) -> dict:
        """Run all configured sync pairs. Returns summary dict."""
        self.last_change_count = 0
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

            lock_dir = self._shared_lock_dir(folder_a, folder_b) \
                if self._lock_enabled else None
            lock_held = False
            if lock_dir is not None:
                lock_held = self._acquire_lock(lock_dir)
                if not lock_held:
                    summary["errors"].append(
                        "Sync pair skipped — lock held by another device: "
                        f"{folder_a} ↔ {folder_b}"
                    )
                    continue
            try:
                logger.info("Bidirectional sync: %s ↔ %s", folder_a, folder_b)
                res = self.sync_bidirectional(folder_a, folder_b)
                summary["bidirectional"].append({
                    "a": str(folder_a),
                    "b": str(folder_b),
                    "result": res,
                })
            finally:
                if lock_held:
                    self._release_lock(lock_dir)

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

            try:
                target.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError) as e:
                logger.warning("Skipping - cannot create target %s: %s", target, e)
                continue

            lock_dir = self._shared_lock_dir(source, target) \
                if self._lock_enabled else None
            lock_held = False
            if lock_dir is not None:
                lock_held = self._acquire_lock(lock_dir)
                if not lock_held:
                    summary["errors"].append(
                        "One-way sync skipped — lock held by another device: "
                        f"{source} → {target}"
                    )
                    continue
            try:
                logger.info("One-way sync: %s → %s", source, target)
                ok = self.sync_one_way(source, target)
                summary["one_way"].append({
                    "source": str(source),
                    "target": str(target),
                    "ok": ok,
                })
                if not ok:
                    summary["errors"].append(
                        f"One-way sync failed: {source} → {target}"
                    )
            finally:
                if lock_held:
                    self._release_lock(lock_dir)

        return summary

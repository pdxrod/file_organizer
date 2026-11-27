""" Folder sync engine: bidirectional + one-way sync using rsync. """

import os
import subprocess
import logging
import time
import fnmatch
import re
import stat
import json
from pathlib import Path
from typing import Optional

from file_organizer import credential_guard

logger = logging.getLogger(__name__)

# Cache of "is this rsync binary GNU rsync?" probes (see _probe_gnu).
_GNU_PROBE_CACHE: dict[str, bool] = {}


class _ExcludeMatcher:
    """rsync-style exclusion matching for the pattern shapes file_organizer
    uses. Mirrors GNU rsync's filter rules closely enough for the quick
    check to be safe:

      - "name"            matches that component name at any depth
      - "*.pyc"           component glob at any depth
      - "/name/"          the top-level DIRECTORY name only (anchored)
      - "**/name"         same as the bare name (any depth)
      - "a/b"             matches when the path's last two components
                          are "a/b" (unanchored suffix match)

    Anything else (anchored file patterns, dir-only trailing-slash
    patterns, exotic ** shapes) marks the matcher invalid and the quick
    check declines to decide, so rsync runs as usual.
    """

    def __init__(self, patterns: list[str]) -> None:
        self.basename: list[str] = []       # matched against each component
        self.suffix: list[list[str]] = []   # component lists matched at end
        self.root_dirs: set[str] = set()    # "/name/" anchored top-level dirs
        self.valid = True
        for pat in patterns or []:
            pat = str(pat)
            if not pat:
                continue
            if pat.startswith("/"):
                if pat.endswith("/") and "/" not in pat[1:-1]:
                    self.root_dirs.add(pat[1:-1])
                else:
                    self.valid = False
                    return
            elif pat.endswith("/"):
                self.valid = False
                return
            elif "**" in pat:
                if pat.startswith("**/") and "/" not in pat[3:] \
                        and "**" not in pat[3:]:
                    self.basename.append(pat[3:])
                else:
                    self.valid = False
                    return
            elif "/" in pat:
                self.suffix.append(pat.split("/"))
            else:
                self.basename.append(pat)
        # Precompile (fnmatchcase semantics: case-sensitive globs).
        def _translated(pat: str) -> str:
            # fnmatch.translate() always ends with \Z; strip it so
            # components can be joined ("*" must not cross a "/").
            t = fnmatch.translate(pat)
            return t[:-2] if t.endswith("\\Z") else t

        self._basename_re = [
            re.compile(fnmatch.translate(p)) for p in self.basename
        ]
        self._suffix_re = [
            (comps, re.compile("/".join(_translated(c) for c in comps) + "\\Z"))
            for comps in self.suffix
        ]

    def matches(self, relpath: str, is_dir: bool) -> bool:
        """True when relpath (posix, relative to the pair root) matches."""
        parts = relpath.split("/")
        if is_dir and parts[0] in self.root_dirs:
            return True
        for name in parts:
            for rx in self._basename_re:
                if rx.match(name):
                    return True
        for comps, rx in self._suffix_re:
            if len(parts) >= len(comps):
                tail = "/".join(parts[-len(comps):])
                if rx.match(tail):
                    return True
        return False


def _probe_gnu(binary: str) -> bool:
    """Does this rsync binary print GNU rsync's version banner?"""
    if binary in _GNU_PROBE_CACHE:
        return _GNU_PROBE_CACHE[binary]
    try:
        out = subprocess.run(
            [binary, "--version"], capture_output=True, timeout=5,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        _GNU_PROBE_CACHE[binary] = False
        return False
    is_gnu = "rsync  version" in out and "openrsync" not in out
    _GNU_PROBE_CACHE[binary] = is_gnu
    return is_gnu


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
        # Quick check: before each rsync direction, compare the two trees
        # with a fast Python walk (no rsync scan) and skip the direction
        # when the target already has everything from the source. Never
        # changes what rsync does — it only skips work.
        self._quick_check: bool = bool(
            config.raw_config.get("sync_quick_check", True)
        )
        # Which rsync binary to run (see _resolve_rsync_binary). macOS's
        # /usr/bin/rsync is openrsync; GNU rsync from Homebrew is preferred
        # when present.
        self._rsync_bin: str = self._resolve_rsync_binary()
        self._rsync_is_gnu: bool = _probe_gnu(self._rsync_bin)
        # Failed cloud syncs are remembered for this many seconds before
        # being tried again (0 = never remember, always retry).
        self._failure_cooldown: int = int(
            config.raw_config.get("sync_failure_cooldown_minutes", 360) or 0
        ) * 60
        raw_state = config.raw_config.get("sync_failure_state_path") or ""
        self._failure_state_path: Path = (
            Path(os.path.expanduser(raw_state)) if raw_state
            else Path.home() / ".file_organizer_sync_failures.json"
        )
        self._failures: dict = self._load_failures()
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

    # Cloud-app folder markers: a sync failure involving one of these is
    # remembered (see sync_failure_cooldown_minutes) so a stuck cloud file
    # is not retried every cycle. Removable volumes (/Volumes/) are NOT
    # listed — they keep the old retry-every-cycle behaviour.
    CLOUD_MARKERS = (
        "CloudStorage", "GoogleDrive", "Google Drive",
        "Dropbox", "OneDrive", "iCloud", "Mobile Documents",
    )

    def _is_cloud_path(self, path: Path) -> bool:
        text = str(path)
        return any(m in text for m in self.CLOUD_MARKERS)

    def _credential_excludes(self, source: Path) -> list:
        """'/rel/path' rsync patterns for credential-looking files under
        source. Content-sniffed on LOCAL sides only: reading a cloud
        mount's placeholders would materialise them (item 11). Filenames
        that look like credentials are already excluded by the built-in
        exclude_patterns, so only innocent-named files are sniffed here."""
        if self._is_cloud_path(source):
            return []
        excludes = []
        try:
            for dirpath, dirnames, filenames in os.walk(source):
                dirnames[:] = [
                    d for d in dirnames
                    if not credential_guard.suspicious_name(d)
                ]
                for fname in filenames:
                    if credential_guard.suspicious_name(fname):
                        continue  # covered by --exclude patterns
                    p = Path(dirpath) / fname
                    if credential_guard.sniff_file_for_secret(p):
                        excludes.append(
                            "/" + p.relative_to(source).as_posix()
                        )
        except OSError:
            pass
        return excludes

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

    def _resolve_rsync_binary(self) -> str:
        """Pick the rsync binary: config override, else GNU rsync from
        Homebrew when installed, else plain "rsync".

        openrsync (macOS /usr/bin/rsync) silently ignores the long-form
        --whole-file spelling and mmap-reads every file's content during
        its delta scan, which force-downloads each cloud placeholder —
        measured at 10+ minutes for a 9k-file Proton folder that GNU
        rsync / -W walks in ~1s.
        """
        override = (self.config.raw_config.get("rsync_binary") or "").strip()
        if override:
            logger.info("Using rsync binary from config: %s", override)
            return override
        for candidate in (
            "/opt/homebrew/bin/rsync",
            "/usr/local/bin/rsync",
            "/usr/local/opt/rsync/bin/rsync",
        ):
            if not (os.path.isfile(candidate)
                    and os.access(candidate, os.X_OK)):
                continue
            try:
                out = subprocess.run(
                    [candidate, "--version"],
                    capture_output=True, timeout=5,
                ).stdout.decode("utf-8", "replace")
            except (OSError, subprocess.TimeoutExpired):
                continue
            if "rsync  version" in out and "openrsync" not in out:
                logger.info("Using GNU rsync: %s", candidate)
                return candidate
        return "rsync"

    def _build_rsync_args(self, source: Path, target: Path, delete: bool = False,
                          exclude_subdirs=None) -> list[str]:
        """Build rsync argument list."""
        args = [self._rsync_bin, "-av", "--progress"]

        # One line per transferred/updated item — used to detect whether a
        # sync actually did anything (idle backoff, item 14).
        args.append("--itemize-changes")

        # Whole-file transfer by default so cloud placeholder files are
        # never mmap'ed/read. Use the SHORT -W spelling on purpose:
        # openrsync silently ignores the long --whole-file form and
        # mmap-reads every file's content during its delta scan, which
        # force-downloads each Proton placeholder (hours per pair). -W is
        # honoured by BOTH openrsync and GNU rsync.
        if self._whole_file:
            args.append("-W")
        elif self._rsync_is_gnu:
            # --no-whole-file is GNU-only. openrsync defaults to delta,
            # which is exactly what whole_file: false asks for.
            args.append("--no-whole-file")

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

        # Credential files found by CONTENT: a key inside an innocent-
        # named file (notes.txt with 'sk-…') is never copied either.
        for rel in self._credential_excludes(source):
            args.extend(["--exclude", rel])

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

    def _load_failures(self) -> dict:
        """Read the failed-sync memory (best effort — a missing or broken
        state file just means nothing is remembered)."""
        try:
            data = json.loads(self._failure_state_path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_failures(self) -> None:
        """Write the failed-sync memory, pruning entries that are older
        than four cooldowns."""
        now = time.time()
        keep = {
            k: v for k, v in self._failures.items()
            if isinstance(v, dict)
            and now - v.get("failed_at", 0) < self._failure_cooldown * 4
        }
        self._failures = keep
        try:
            self._failure_state_path.write_text(
                json.dumps(keep, indent=2, sort_keys=True)
            )
        except OSError as e:
            logger.warning(
                "Could not save sync failure state %s: %s",
                self._failure_state_path, e,
            )

    def _failure_cooldown_until(self, key: str) -> float:
        """Timestamp until which this source→target unit is remembered
        as failed (0.0 = not remembered)."""
        if self._failure_cooldown <= 0:
            return 0.0
        rec = self._failures.get(key)
        if not isinstance(rec, dict):
            return 0.0
        try:
            return float(rec.get("failed_at", 0)) + self._failure_cooldown
        except (TypeError, ValueError):
            return 0.0

    def _remember_failure(self, key: str, source: Path,
                          target: Path) -> None:
        """Remember a failed cloud sync so it is not retried every cycle.
        Local pairs are never remembered (a transient local error should
        keep retrying)."""
        if self._failure_cooldown <= 0:
            return
        if not (self._is_cloud_path(source) or self._is_cloud_path(target)):
            return
        self._failures[key] = {
            "failed_at": int(time.time()),
            "source": str(source),
            "target": str(target),
        }
        logger.info(
            "Remembering failed sync %s → %s — will not retry for "
            "%d minutes",
            source, target, self._failure_cooldown // 60,
        )
        self._save_failures()

    def _forget_failure(self, key: str) -> None:
        if key in self._failures:
            del self._failures[key]
            self._save_failures()

    def _run_rsync(self, source: Path, target: Path, delete: bool = False,
                    exclude_subdirs=None) -> bool:
        """Run rsync from source to target. Returns True on success.

        A failed CLOUD sync is remembered (sync_failure_cooldown_minutes)
        and skipped on later runs until the cooldown expires, instead of
        retrying the same stuck placeholder every cycle.
        """
        args = self._build_rsync_args(source, target, delete, exclude_subdirs)

        # Ensure target directory exists
        target.mkdir(parents=True, exist_ok=True)

        key = f"{source}|{target}"
        until = self._failure_cooldown_until(key)
        now = time.time()
        if until > now:
            logger.info(
                "Skipping %s → %s — rsync failed recently (retrying in "
                "%.0f min)",
                source, target, (until - now) / 60,
            )
            return True

        logger.info("Syncing: %s → %s", source, target)
        logger.debug("rsync args: %s", " ".join(args))

        ok = False
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
                ok = True
            else:
                stderr_text = result.stderr.decode('utf-8', errors='replace') if result.stderr else ''
                logger.error("rsync failed (%d): %s", result.returncode, stderr_text[:500])
        except subprocess.TimeoutExpired:
            logger.error("rsync timed out after %ds: %s → %s", self._timeout, source, target)
        except KeyboardInterrupt:
            raise
        except FileNotFoundError:
            logger.error("rsync not found — please install rsync")

        if ok:
            self._forget_failure(key)
        else:
            self._remember_failure(key, source, target)
        return ok

    def _sync_subdirs(self, source: Path) -> list:
        """Direct subfolder names eligible for sync, excluding folders the
        user asked never to sync (sync_exclude_folder_names — e.g. the
        file_organizer checkouts themselves, so the organizers never
        mirror their own source code between machines) AND subfolders
        whose names match exclude_patterns (.git, __pycache__,
        node_modules, .venv, …).

        The pattern filter matters in chunked mode: each subfolder gets
        its own rsync run ROOTED at that subfolder, where a bare-name
        exclude like ".git" no longer matches anything (the .git
        component is the transfer root itself, not an item inside it) —
        without this filter those junk trees were fully walked and
        transferred. Invalid (unsupported) patterns leave the list
        unfiltered, exactly like the quick check declines to decide.
        """
        try:
            subdirs = sorted(
                p.name for p in source.iterdir() if p.is_dir()
            )
        except OSError:
            return []
        matcher = _ExcludeMatcher(
            list(self._exclude_patterns)
            + [f"/{n}/" for n in self._exclude_folder_names]
        )
        kept: list[str] = []
        skipped: list[str] = []
        for name in subdirs:
            if name in self._exclude_folder_names:
                continue
            if matcher.valid and matcher.matches(name, is_dir=True):
                skipped.append(name)
                continue
            kept.append(name)
        if skipped:
            logger.info(
                "Chunker skipping excluded subfolder(s) of %s: %s",
                source, ", ".join(skipped),
            )
        return kept

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

    def _walk_index(self, root: Path) -> "Optional[dict]":
        """Index one side of a pair for the quick check.

        Returns {relpath: entry} where relpath is posix-relative:
          file    ("f", mode, size, mtime_sec)
          symlink ("L", target, mtime_sec)
          dir     ("d",)   — presence only, dir mtimes are NOT compared
                            (cloud mounts don't preserve them)
        Exclusions are applied exactly like the rsync --exclude list
        (patterns that can't be matched exactly make the matcher invalid).
        Special files (fifos etc.) are ignored — rsync skips them too
        without --specials.

        Returns None when the index can't be trusted (invalid patterns,
        I/O errors) — the caller then runs rsync as usual.
        """
        matcher = _ExcludeMatcher(
            list(self._exclude_patterns)
            + [f"/{n}/" for n in self._exclude_folder_names]
        )
        if not matcher.valid:
            return None
        if not os.path.isdir(str(root)):
            return None
        index: dict = {}
        try:
            for dirpath, dirnames, filenames in os.walk(
                str(root), followlinks=False
            ):
                dirpath_p = Path(dirpath)
                kept: list[str] = []
                for name in dirnames:
                    rel = os.path.relpath(
                        os.path.join(dirpath, name), str(root)
                    )
                    if matcher.matches(rel, is_dir=True):
                        continue
                    p = dirpath_p / name
                    try:
                        st = os.lstat(str(p))
                    except OSError:
                        return None
                    if stat.S_ISLNK(st.st_mode):
                        try:
                            target = os.readlink(str(p))
                        except OSError:
                            return None
                        index[rel] = ("L", target, int(st.st_mtime))
                    else:
                        kept.append(name)
                        index[rel] = ("d",)
                dirnames[:] = kept
                for name in filenames:
                    p = dirpath_p / name
                    rel = os.path.relpath(str(p), str(root))
                    try:
                        st = os.lstat(str(p))
                    except OSError:
                        return None
                    if stat.S_ISLNK(st.st_mode):
                        try:
                            target = os.readlink(str(p))
                        except OSError:
                            return None
                        index[rel] = ("L", target, int(st.st_mtime))
                        continue
                    if not (stat.S_ISREG(st.st_mode)
                            or stat.S_ISDIR(st.st_mode)):
                        continue  # special files: rsync skips them too
                    if matcher.matches(rel, is_dir=False):
                        continue
                    index[rel] = (
                        "f", stat.S_IMODE(st.st_mode),
                        st.st_size, int(st.st_mtime),
                    )
        except OSError:
            return None
        return index

    @staticmethod
    def _needs_sync(source_index: dict, target_index: dict) -> bool:
        """True when the target is missing a source entry or has a
        different one — i.e. the source side has something newer/missing
        that a sync would copy."""
        for rel, entry in source_index.items():
            if target_index.get(rel) != entry:
                return True
        return False

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

            skip_a_to_b = skip_b_to_a = False
            if self._quick_check:
                idx_a = self._walk_index(folder_a)
                idx_b = self._walk_index(folder_b)
                if idx_a is not None and idx_b is not None:
                    skip_a_to_b = not self._needs_sync(idx_a, idx_b)
                    skip_b_to_a = not self._needs_sync(idx_b, idx_a)

            if skip_a_to_b and skip_b_to_a:
                logger.info(
                    "Quick check: %s and %s are in sync — skipped",
                    folder_a, folder_b,
                )
                summary["bidirectional"].append({
                    "a": str(folder_a),
                    "b": str(folder_b),
                    "result": {"a_to_b": True, "b_to_a": True},
                    "skipped": True,
                })
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
                res = {"a_to_b": True, "b_to_a": True}
                if skip_a_to_b:
                    logger.info(
                        "Quick check: nothing newer in %s — skipped "
                        "%s → %s", folder_a, folder_a, folder_b,
                    )
                else:
                    res["a_to_b"] = self._run_rsync_many(folder_a, folder_b)
                if skip_b_to_a:
                    logger.info(
                        "Quick check: nothing newer in %s — skipped "
                        "%s → %s", folder_b, folder_b, folder_a,
                    )
                else:
                    res["b_to_a"] = self._run_rsync_many(folder_b, folder_a)
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

            skip = False
            if self._quick_check:
                idx_src = self._walk_index(source)
                idx_tgt = self._walk_index(target)
                if idx_src is not None and idx_tgt is not None:
                    skip = not self._needs_sync(idx_src, idx_tgt)
            if skip:
                logger.info(
                    "Quick check: nothing newer in %s — skipped %s → %s",
                    source, source, target,
                )
                summary["one_way"].append({
                    "source": str(source),
                    "target": str(target),
                    "ok": True,
                    "skipped": True,
                })
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

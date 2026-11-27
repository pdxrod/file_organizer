"""Run-level cross-device lock — ONE organizer at a time across machines.

The pair lock in sync_engine.py protects one sync pair while its rsyncs
run. This lock protects the WHOLE run: two computers organizing + syncing
the same cloud drive at once would both be reading and writing the same
files through their cloud apps. The marker (.file_organizer.run.lock)
sits at the root of the SHARED cloud drive (or run_lock_dir), so every
machine sees the same file through the cloud. The second machine to
start finds the lock, sees its heartbeat is fresh, and backs out of its
whole cycle — it retries on its next scheduled run. A crashed machine's
heartbeat stops and its lock is taken over after run_lock_stale_minutes.
"""

import json
import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

RUN_LOCK_NAME = ".file_organizer.run.lock"
# How often the holder refreshes the lock's mtime. A live run is never
# mistaken for a crashed one while this keeps beating.
HEARTBEAT_SECONDS = 30.0

# Drive-path markers used to auto-pick the lock folder: cloud-app folders
# first (those are what two machines truly share), then removable volumes
# (an external disk can only be on one machine at a time, but a second
# machine might still try while it is plugged in elsewhere).
CLOUD_MARKERS = (
    "CloudStorage", "GoogleDrive", "Google Drive",
    "Dropbox", "OneDrive", "iCloud", "Mobile Documents",
)
VOLUME_MARKERS = ("/Volumes/", "/media/", "/mnt/")


class RunLock:
    """Cross-device mutual exclusion for a whole organize run.

    acquire() returns True when this machine may proceed (no other
    machine holds a live lock), False when another machine's run is in
    progress — the caller must back out of the cycle entirely. release()
    drops the lock; it is a no-op when acquire() failed, so a caller may
    always use try/finally without deleting another machine's lock.
    """

    def __init__(self, config):
        self._config = config
        self._enabled = bool(
            config.raw_config.get("run_lock_enabled", True)
        )
        self._stale_seconds = int(
            config.raw_config.get("run_lock_stale_minutes", 15) or 0
        ) * 60
        self._dir: Optional[Path] = self._resolve_dir()
        self._path: Optional[Path] = (
            self._dir / RUN_LOCK_NAME if self._dir else None
        )
        self._held = False
        self._heartbeat_stop: Optional[threading.Event] = None
        self._heartbeat_thread: Optional[threading.Thread] = None

    # ── where the lock lives ─────────────────────────────────────────

    @property
    def active(self) -> bool:
        """Is there a shared folder this machine can lock? Local-only
        setups have no cross-device collision to prevent."""
        return self._enabled and self._path is not None

    def _resolve_dir(self) -> Optional[Path]:
        raw = self._config.raw_config
        explicit = (raw.get("run_lock_dir") or "").strip()
        if explicit:
            p = Path(os.path.expanduser(explicit))
            if p.is_dir():
                return p
            logger.warning(
                "run_lock_dir '%s' does not exist — run lock disabled.",
                explicit,
            )
            return None
        drives = getattr(self._config, "drives", None) or {}
        candidates = [str(v) for v in drives.values() if v]
        for markers in (CLOUD_MARKERS, VOLUME_MARKERS):
            for value in candidates:
                p = Path(os.path.expanduser(value))
                if not p.is_dir():
                    continue
                if any(m in str(p) for m in markers):
                    return p
        return None

    # ── acquire / release ────────────────────────────────────────────

    def _payload(self) -> str:
        return json.dumps({
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "started": int(time.time()),
        })

    def _read_holder(self) -> str:
        try:
            data = json.loads(self._path.read_text(errors="replace"))
            return str(data.get("host") or "unknown")
        except (OSError, ValueError):
            return "unknown"

    def acquire(self) -> bool:
        """True when this machine may run; False = another machine is
        mid-run and this cycle must back out."""
        if not self.active:
            return True
        for _ in range(2):
            try:
                fd = os.open(str(self._path),
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as f:
                    f.write(self._payload())
                logger.info(
                    "Run lock taken: %s (one organizer at a time "
                    "across machines)", self._path,
                )
                self._held = True
                self._start_heartbeat()
                return True
            except FileExistsError:
                holder = self._read_holder()
                try:
                    age = time.time() - self._path.stat().st_mtime
                except OSError:
                    age = 0.0
                if self._stale_seconds > 0 and age > self._stale_seconds:
                    logger.warning(
                        "Removing stale run lock (%s, %.0f min old, "
                        "holder: %s) — taking over.",
                        self._path, age / 60, holder,
                    )
                    try:
                        self._path.unlink()
                    except OSError:
                        return False
                    continue  # retry the atomic create once
                logger.warning(
                    "Another organizer is running on another computer "
                    "(lock %s, holder: %s, refreshed %.0f s ago). "
                    "Backing out of this cycle; the next scheduled run "
                    "will retry.",
                    self._path, holder, age,
                )
                return False
            except OSError as e:
                logger.warning("Could not take run lock %s: %s", self._path, e)
                return False
        return False

    def _start_heartbeat(self) -> None:
        """Refresh the lock's mtime so a long run is never mistaken for
        a crashed one; the daemon thread dies with the process."""
        stop = threading.Event()

        def beat() -> None:
            while not stop.wait(HEARTBEAT_SECONDS):
                try:
                    os.utime(str(self._path), None)
                except OSError:
                    break  # lock file gone (e.g. taken over) — stop

        self._heartbeat_stop = stop
        self._heartbeat_thread = threading.Thread(
            target=beat, name="file_organizer-run-lock", daemon=True,
        )
        self._heartbeat_thread.start()

    def release(self) -> None:
        """Drop the lock (no-op when we never held it — a caller that
        backed out must not delete the other machine's lock)."""
        if not self._held:
            return
        if self._heartbeat_stop is not None:
            self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=2.0)
        self._heartbeat_stop = None
        self._heartbeat_thread = None
        self._held = False
        try:
            self._path.unlink()
        except OSError:
            pass
        logger.info("Run lock released: %s", self._path)

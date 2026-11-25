"""single_instance.py — only ONE organizer daemon may run at a time.

The daemon takes an exclusive advisory lock on the pid file (flock on
macOS/Linux) and writes its PID there. Any later start — './organize
start', the GUI's Start button, or a raw 'python -m file_organizer
--REAL' — sees the live holder and refuses. Stopping goes through the
same file, so Stop always knows who to kill. A dead holder's file is
taken over automatically (flock dies with its owner). Stdlib only.
"""

import os
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows — pid-file check only (best effort)
    fcntl = None


def pid_alive(pid: int) -> bool:
    """True when a process with this PID exists."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (OSError, PermissionError):
        # Exists, but not ours — treat as alive (we could not manage it
        # anyway).
        return True


def read_holder(pid_file) -> int:
    """The live PID recorded in the pid file, or 0 (missing / stale)."""
    try:
        pid = int(Path(pid_file).read_text().strip() or "0")
    except (OSError, ValueError):
        return 0
    return pid if pid_alive(pid) else 0


def acquire(pid_file):
    """Take the single-instance lock and record our PID.

    Returns the open lock handle on success — keep it referenced for the
    whole process lifetime — or None when another LIVE instance holds
    the lock.
    """
    path = Path(pid_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+")
    except OSError:
        return None
    try:
        if fcntl is not None:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                return None
        else:
            # No flock: pid-file check only (best effort on Windows).
            if read_holder(path):
                handle.close()
                return None
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        return handle
    except Exception:
        try:
            handle.close()
        except OSError:
            pass
        return None


def release(handle, pid_file) -> None:
    """Drop the lock and remove the pid file when it records our PID."""
    path = Path(pid_file)
    if handle is None:
        return
    try:
        # Remove while still holding the lock, so nobody mistakes the
        # file for a live holder; a new starter creates a fresh file.
        try:
            recorded = int(path.read_text().strip() or "0")
        except (OSError, ValueError):
            recorded = 0
        if recorded == os.getpid():
            path.unlink(missing_ok=True)
    finally:
        try:
            handle.close()
        except OSError:
            pass

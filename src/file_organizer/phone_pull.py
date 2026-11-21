#!/usr/bin/env python3
"""Phone pull in Python (pull mode) — used by 'organize auto' and the
end-to-end test instead of src/phone_pull.sh.

The bash script kept deadlocking on macOS bash 3.2 (process substitution,
command-substitution pipe-holds, watchdog subshells). Plain Python
subprocesses with hard timeouts have none of those failure modes.

Behaviour mirrors the old script's pull:
  - finds ready devices with `adb devices` (restarts a wedged server once)
  - checks the configured staging folders exist on the phone
  - streams them over `adb exec-out tar` into a temp folder
  - applies the filename rules: held app-generated names stay on the
    phone, leading dates normalise, '(1)' counters move before the
    extension
  - maps each staging subfolder to a computer folder (staging_map, then
    a same-named folder under MAIN_DRIVE, then misc)
  - never overwrites: identical content is skipped, a different file
    lands as name_1.ext etc.
  - skips files younger than phone_pull.min_age_minutes
  - tracks last-seen mtimes in a state file so unchanged files are not
    re-copied

Usage:  python3 -m file_organizer.phone_pull [--quiet] [--config PATH]
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path

from file_organizer.config import map_staging_path, resolve_staging_map
from file_organizer.filename_rules import (
    is_app_generated,
    normalise_leading_date,
)
from file_organizer.rename_family import normalize_copy_counter

ADB_TIMEOUT = 20        # quick adb commands
STREAM_TIMEOUT = 600    # the tar stream
DEFAULT_SOURCE_DIRS = [
    "file_organizer_staging",
]


def _resolve(path: str, drives: dict) -> str:
    """'MAIN_DRIVE/misc' -> '/Users/x/misc'; plain paths pass through."""
    path = os.path.expanduser(path)
    for drive, base in (drives or {}).items():
        base = os.path.expanduser(str(base))
        if path == drive:
            return base
        if path.startswith(drive + "/"):
            return base + path[len(drive):]
    return path


class Puller:
    def __init__(self, cfg: dict, quiet: bool = False):
        pp = (cfg or {}).get("phone_pull", {}) or {}
        drives = (cfg or {}).get("drives", {}) or {}
        self.drives = drives
        self.quiet = quiet
        self.adb = _resolve(pp.get("adb_path") or "adb", drives)
        self.android_base = pp.get("android_base") or "/storage/emulated/0"
        self.source_dirs = pp.get("phone_source_dirs") or DEFAULT_SOURCE_DIRS
        local_stage = _resolve(pp.get("local_stage") or "~/misc", drives)
        self.local_stage = Path(local_stage)
        self.min_age_seconds = int(pp.get("min_age_minutes", 5) or 0) * 60
        self.hold = bool(pp.get("hold_app_generated_names", True))
        self.normalise = bool(pp.get("normalise_leading_dates", True))
        self.staging_map = resolve_staging_map(
            pp.get("staging_map") or {}, drives
        )
        main_drive = _resolve(drives.get("MAIN_DRIVE", "") or "", drives)
        self.misc_dir = (str(Path(main_drive) / "misc") if main_drive
                         else local_stage)
        state_file = pp.get("state_file") or ""
        if not state_file:
            state_file = str(Path(local_stage) / ".phone_pull_state")
        self.state_path = Path(os.path.expanduser(state_file))
        self.state = self._load_state()

    # ── logging ────────────────────────────────────────────────────────
    def _info(self, msg: str) -> None:
        if not self.quiet:
            print(f"[phone_pull] {msg}")

    def _warn(self, msg: str) -> None:
        print(f"[phone_pull] WARN  {msg}", file=sys.stderr)

    # ── state ──────────────────────────────────────────────────────────
    def _load_state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self.state))
        except OSError as exc:
            self._warn(f"could not save pull state: {exc}")

    # ── adb ────────────────────────────────────────────────────────────
    def _adb(self, *args: str, timeout: int = ADB_TIMEOUT):
        """Run an adb command. Returns (rc, stdout_text). rc 124 = the
        command was killed by the timeout (the phone did not answer)."""
        try:
            proc = subprocess.run(
                [self.adb, *args],
                capture_output=True, text=True, timeout=timeout,
            )
            return proc.returncode, proc.stdout or ""
        except subprocess.TimeoutExpired:
            return 124, ""
        except FileNotFoundError:
            return 127, ""

    def _devices(self):
        rc, out = self._adb("devices")
        if not out.strip():
            self._warn("adb did not respond — restarting the adb server")
            self._adb("kill-server", timeout=10)
            rc, out = self._adb("devices")
        if not out.strip():
            self._warn("adb still not responding — no phone can be used")
            return []
        devices = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, state = parts[0], parts[1]
            if state == "device":
                devices.append(serial)
            elif state == "unauthorized":
                self._warn(
                    f"phone {serial} is 'unauthorized' — USB debugging is "
                    "OFF, or the 'Allow USB debugging?' prompt on the "
                    "phone was not accepted."
                )
            else:
                self._warn(
                    f"phone {serial} is '{state}' — try reconnecting the "
                    "USB cable."
                )
        return devices

    # ── the pull ───────────────────────────────────────────────────────
    def pull_all(self) -> bool:
        devices = self._devices()
        if not devices:
            self._warn("No usable devices connected.")
            return False
        ok = True
        for serial in devices:
            if not self.pull_device(serial):
                ok = False
        return ok

    def pull_device(self, serial: str) -> bool:
        rc, model = self._adb("-s", serial, "shell",
                              "getprop ro.product.model")
        if rc == 124:
            self._warn("The phone is connected but is not answering — "
                       "unplug and replug it, then try again.")
            return False
        model = model.strip().strip("\r\n") or serial
        self._info(f"Pulling from device: {serial} ({model})")

        rel_dirs = []
        for src in self.source_dirs:
            phone_dir = f"{self.android_base}/{src}"
            rc, out = self._adb(
                "-s", serial, "shell",
                f"[ -d '{phone_dir}' ] && echo yes || echo no"
            )
            if rc == 124:
                self._warn("The phone stopped answering part-way through "
                           "— unplug and replug it, then try again.")
                return False
            if out.strip().strip("\r\n") == "yes":
                rel_dirs.append(src)
        if not rel_dirs:
            self._warn(
                f"No source folders found on device {serial} — nothing "
                "to pull."
            )
            return True

        copied = skipped = held = 0
        try:
            with tempfile.TemporaryDirectory(prefix=".tmp_pull_") as tmp:
                tar_args = " ".join(f"'{d}'" for d in rel_dirs)
                cmd = f"cd {self.android_base} && tar cf - {tar_args} 2>/dev/null"
                proc = subprocess.Popen(
                    [self.adb, "-s", serial, "exec-out", cmd],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                )
                assert proc.stdout is not None
                counters = {"copied": 0, "skipped": 0, "held": 0}

                def _stream():
                    # Members are processed INSIDE the stream loop: a
                    # 'r|' tarfile cannot extract after EOF.
                    try:
                        with tarfile.open(fileobj=proc.stdout, mode="r|") \
                                as tar:
                            for member in tar:
                                if not member.isfile():
                                    continue
                                name = member.name
                                if name.startswith("/") or ".." in \
                                        name.split("/"):
                                    continue
                                outcome = self._place(
                                    serial, member, tar, tmp, name
                                )
                                counters[outcome] = \
                                    counters.get(outcome, 0) + 1
                    except (tarfile.TarError, OSError):
                        pass

                thread = threading.Thread(target=_stream, daemon=True)
                thread.start()
                thread.join(STREAM_TIMEOUT)
                if thread.is_alive():
                    proc.kill()
                    self._warn("The phone stopped answering during the "
                               "file stream — unplug and replug it, "
                               "then try again.")
                    return False
                try:
                    proc.stdout.close()
                except OSError:
                    pass
                proc.wait(timeout=10)
                copied = counters["copied"]
                skipped = counters["skipped"]
                held = counters["held"]
        except (OSError, tarfile.TarError) as exc:
            self._warn(f"pull failed for {serial}: {exc}")
            return False
        self._save_state()
        self._info(f"Pull complete for {model}: copied={copied} "
                   f"skipped={skipped} held={held}")
        return True

    def _place(self, serial, member, tar, tmp, name) -> str:
        """Apply the filename rules and copy one file. Never overwrites."""
        fname = Path(name).name
        rel_dir = str(Path(name).parent)
        if rel_dir == ".":
            rel_dir = ""
        if self.hold and is_app_generated(fname):
            return "held"
        dest_fname = fname
        if self.normalise:
            dest_fname = normalise_leading_date(dest_fname)
        dest_fname = normalize_copy_counter(dest_fname)

        age = time.time() - (member.mtime or 0)
        if self.min_age_seconds and age < self.min_age_seconds:
            return "skipped"

        key = f"{serial}::{self.android_base}/{name}"
        if self.state.get(key) == member.mtime:
            return "skipped"

        dest_root, remainder = map_staging_path(
            rel_dir, self.drives, self.staging_map, self.misc_dir
        )
        dest_dir = Path(dest_root)
        if remainder:
            dest_dir = dest_dir / remainder
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / dest_fname

        # Extract to a temp file first (never write over an existing file).
        tar.extract(member, tmp)
        extracted = Path(tmp) / name
        try:
            if not dest.exists():
                os.replace(str(extracted), str(dest))
                self.state[key] = member.mtime
                self._info(f"  {name} -> {dest}")
                return "copied"
            if self._same_content(extracted, dest):
                extracted.unlink()
                self.state[key] = member.mtime
                return "skipped"
            # Same name, different content: keep both, incoming as name_N.
            stem, ext = os.path.splitext(dest_fname)
            n = 1
            while True:
                cand = dest_dir / f"{stem}_{n}{ext}"
                if not cand.exists():
                    break
                n += 1
            os.replace(str(extracted), str(cand))
            self.state[key] = member.mtime
            self._info(f"  {name} -> {cand} (kept the existing file)")
            return "copied"
        finally:
            if extracted.exists():
                extracted.unlink(missing_ok=True)

    @staticmethod
    def _same_content(a: Path, b: Path) -> bool:
        if a.stat().st_size != b.stat().st_size:
            return False

        def digest(p: Path) -> str:
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            return h.hexdigest()

        return digest(a) == digest(b)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    cfg = {}
    if args.config:
        import yaml
        try:
            cfg = yaml.safe_load(Path(args.config).read_text()) or {}
        except (OSError, ValueError) as exc:
            print(f"cannot read config {args.config}: {exc}", file=sys.stderr)
            return 1
    puller = Puller(cfg, quiet=args.quiet)
    return 0 if puller.pull_all() else 1


if __name__ == "__main__":
    sys.exit(main())

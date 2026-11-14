#!/usr/bin/env python3
"""Sweep the 'major folders' so a file_organizer test run starts with small,
easy-to-inspect folders (see the README "End-to-end test plan").

Moves EVERYTHING currently in these folders to /tmp/major-folders-backup/:

    ~/Pictures  ~/Downloads  ~/Documents  ~/Music  ~/misc

Run restore-major-folders.py to put the real contents back afterwards.
This script refuses to run when a backup already exists and is not empty,
so you can never overwrite a previous backup by accident.

Usage:  python3 src/test/backup-major-folders.py
"""

import shutil
import sys
from pathlib import Path

MAJOR_FOLDERS = ["Pictures", "Downloads", "Documents", "Music", "misc"]
BACKUP_ROOT = Path("/tmp/major-folders-backup")
HOME = Path.home()


def main() -> None:
    # Safety: never clobber an existing non-empty backup.
    conflicts = []
    for name in MAJOR_FOLDERS:
        dst = BACKUP_ROOT / name
        if dst.exists() and any(dst.iterdir()):
            conflicts.append(name)
    if conflicts:
        print("ERROR: a previous backup already has files for: "
              + ", ".join(conflicts))
        print("Run restore-major-folders.py first, or remove "
              f"{BACKUP_ROOT} if you really want to discard it.")
        sys.exit(1)

    for name in MAJOR_FOLDERS:
        src = HOME / name
        dst = BACKUP_ROOT / name
        src.mkdir(parents=True, exist_ok=True)
        dst.mkdir(parents=True, exist_ok=True)
        moved = 0
        for entry in src.iterdir():
            shutil.move(str(entry), str(dst / entry.name))
            moved += 1
        empty = not any(src.iterdir())
        print(f"{name:10} moved {moved} item(s) -> {dst}  "
              f"({'now empty' if empty else 'STILL HAS CONTENT — check!'})")

    print()
    print("Major folders are empty and ready for test files.")
    print("Put the real contents back later with:")
    print("  python3 src/test/restore-major-folders.py")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Put the real contents of the major folders back after a test run.

Moves everything from /tmp/major-folders-backup/<name> back into
~/<name> for: Pictures, Downloads, Documents, Music, misc.

Anything already present in the target folder (e.g. test files you made)
is left alone and reported as skipped.

Usage:  python3 src/test/restore-major-folders.py
"""

import shutil
from pathlib import Path

MAJOR_FOLDERS = ["Pictures", "Downloads", "Documents", "Music", "misc"]
BACKUP_ROOT = Path("/tmp/major-folders-backup")
HOME = Path.home()


def main() -> None:
    if not BACKUP_ROOT.exists():
        print(f"Nothing to restore — {BACKUP_ROOT} does not exist.")
        return

    for name in MAJOR_FOLDERS:
        src_dir = BACKUP_ROOT / name
        dst_dir = HOME / name
        if not src_dir.exists() or not any(src_dir.iterdir()):
            print(f"{name:10} nothing to restore")
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        restored = 0
        skipped = 0
        for entry in src_dir.iterdir():
            target = dst_dir / entry.name
            if target.exists():
                print(f"  SKIP  {name}/{entry.name} — already in ~/{name}")
                skipped += 1
                continue
            shutil.move(str(entry), str(target))
            restored += 1
        if not any(src_dir.iterdir()):
            src_dir.rmdir()
        print(f"{name:10} restored {restored} item(s)"
              + (f", skipped {skipped}" if skipped else ""))

    if BACKUP_ROOT.exists() and not any(BACKUP_ROOT.iterdir()):
        BACKUP_ROOT.rmdir()

    print()
    print("Done. Your real files are back in the major folders.")
    print("(Test files created during the plan stay where they are —")
    print("delete them by hand if you don't want them.)")


if __name__ == "__main__":
    main()

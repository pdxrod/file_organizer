#!/usr/bin/env python3
"""Put the swept files back after the end-to-end test plan.

Reads the manifest written by backup-major-folders.py and moves every
backed-up entry back into its original folder. Anything already present in
the target (e.g. test files created during the plan, or files the sync
brought back from the one-way source) is left alone and reported as
skipped. The manifest is removed once everything is restored.

Cross-platform: works wherever the backup script ran (the manifest stores
absolute original paths).

Usage:  python3 src/test/restore-major-folders.py
"""

import json
import shutil
import sys
from pathlib import Path

import testplan_common as tc


def main() -> None:
    if not tc.MANIFEST_PATH.exists():
        print(f"Nothing to restore — {tc.MANIFEST_PATH} does not exist.")
        return

    try:
        manifest = json.loads(tc.MANIFEST_PATH.read_text())
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot read manifest {tc.MANIFEST_PATH}: {exc}")
        sys.exit(1)

    folders = manifest.get("folders", [])
    if not folders:
        print("Manifest is empty — nothing to restore.")
        tc.MANIFEST_PATH.unlink(missing_ok=True)
        return

    for entry in folders:
        original = Path(entry["original"])
        backup = Path(entry["backup"])
        if not backup.is_dir():
            print(f"SKIP  backup folder missing: {backup}")
            continue
        original.mkdir(parents=True, exist_ok=True)
        restored = 0
        skipped = 0
        for item in backup.iterdir():
            target = original / item.name
            if target.exists():
                print(f"  SKIP  {original.name}/{item.name} — already in "
                      f"{original}")
                skipped += 1
                continue
            shutil.move(str(item), str(target))
            restored += 1
        if not any(backup.iterdir()):
            backup.rmdir()
        print(f"{original}: restored {restored} item(s)"
              + (f", skipped {skipped}" if skipped else ""))

    tc.MANIFEST_PATH.unlink(missing_ok=True)
    leftovers = [p for p in (tc.BACKUP_ROOT / "folders").iterdir()] \
        if (tc.BACKUP_ROOT / "folders").is_dir() else []
    if leftovers:
        print("WARNING: some backup folders still have content: "
              + ", ".join(str(p) for p in leftovers))
    for sub in (tc.BACKUP_ROOT / "folders", tc.BACKUP_ROOT):
        try:
            if sub.is_dir() and not any(sub.iterdir()):
                sub.rmdir()
        except OSError:
            pass

    print()
    print("Done. Your files are back in their original folders.")
    print("(Files created during the plan stay where they are — delete")
    print("them by hand if you don't want them.)")


if __name__ == "__main__":
    main()

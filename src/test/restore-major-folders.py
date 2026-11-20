#!/usr/bin/env python3
"""Put the swept files back after the end-to-end test plan.

Reads the manifest written by backup-major-folders.py and puts every
backed-up item back into its original folder. The restore NEVER
overwrites anything:

  - the target has no such item  → the item moves straight back
  - both copies have the same bytes → the backup copy is dropped
    (duplicate; contents are compared, never just size/mtime)
  - same name but different file → the backup copy is moved back beside
    the original as "<name>.file_organizer-backup" (numbered if that is
    taken), so nothing is lost and nothing is clobbered — review those
    and delete them when you are happy

The manifest is removed once the backup is fully drained, so the next
sweep starts cleanly. Conflicts no longer leave the plan stuck.

Cross-platform: works wherever the backup script ran (the manifest stores
absolute original paths).

Usage:  python3 src/test/restore-major-folders.py
"""

import hashlib
import json
import shutil
import sys
from pathlib import Path

import testplan_common as tc

CONFLICT_SUFFIX = ".file_organizer-backup"


def _sha256(path: Path) -> str:
    """SHA-256 of a file's contents, streamed."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_content(a: Path, b: Path) -> bool:
    """True when two regular files hold identical bytes. Content is
    compared, never just size/mtime — deleting a backup copy must never
    lose data because two different files happen to share a timestamp."""
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except OSError:
        return False
    return _sha256(a) == _sha256(b)


def _free_path(target: Path) -> Path:
    """target's name plus CONFLICT_SUFFIX, numbered until unused."""
    candidate = target.with_name(target.name + CONFLICT_SUFFIX)
    n = 1
    while candidate.exists():
        candidate = target.with_name(f"{target.name}{CONFLICT_SUFFIX}{n}")
        n += 1
    return candidate


def _merge_back(backup_dir: Path, target_dir: Path, stats: dict) -> None:
    """Move everything in backup_dir into target_dir without overwriting."""
    for item in sorted(backup_dir.iterdir(), key=lambda p: p.name):
        target = target_dir / item.name
        if not target.exists():
            shutil.move(str(item), str(target))
            stats["restored"] += 1
        elif item.is_dir() and target.is_dir():
            _merge_back(item, target, stats)
            if not any(item.iterdir()):
                item.rmdir()
        elif item.is_file() and target.is_file() \
                and _same_content(item, target):
            item.unlink()
            stats["identical"] += 1
        else:
            # Same name but different content (or one side changed from
            # file to folder): keep BOTH, the backup copy renamed so the
            # user can compare.
            shutil.move(str(item), str(_free_path(target)))
            stats["conflicts"] += 1


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

    totals = {"restored": 0, "identical": 0, "conflicts": 0}
    for entry in folders:
        original = Path(entry["original"])
        backup = Path(entry["backup"])
        if not backup.is_dir():
            print(f"SKIP  backup folder missing: {backup}")
            continue
        original.mkdir(parents=True, exist_ok=True)
        stats = {"restored": 0, "identical": 0, "conflicts": 0}
        _merge_back(backup, original, stats)
        for key in totals:
            totals[key] += stats[key]
        if backup.is_dir() and not any(backup.iterdir()):
            backup.rmdir()
        parts = [f"put back {stats['restored']}"]
        if stats["identical"]:
            parts.append(f"dropped {stats['identical']} duplicate(s)")
        if stats["conflicts"]:
            parts.append(f"kept {stats['conflicts']} conflict(s) as "
                         f"{CONFLICT_SUFFIX}")
        print(f"{original}: " + ", ".join(parts))

    tc.MANIFEST_PATH.unlink(missing_ok=True)
    for sub in (tc.BACKUP_ROOT / "folders", tc.BACKUP_ROOT):
        try:
            if sub.is_dir() and not any(sub.iterdir()):
                sub.rmdir()
        except OSError:
            pass

    print()
    print("Done. Your files are back in their original folders.")
    if totals["conflicts"]:
        print()
        print(f"{totals['conflicts']} item(s) had the same name but "
              f"different content — they were kept beside the originals "
              f"with the suffix '{CONFLICT_SUFFIX}'.")
        print("Look them over and delete the ones you don't want.")
    else:
        print("(Files created during the plan stay where they are — "
              "delete them by hand if you don't want them.)")


if __name__ == "__main__":
    main()

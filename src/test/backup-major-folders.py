#!/usr/bin/env python3
"""Sweep ONE sync-pair (both sides) and ONE one-way-pair (its local target
side) into a temp backup, then drop the plan's test files into the swept
sync-pair folders.

Which pairs? Chosen from YOUR config.yaml:

  - one sync_pairs entry where both folders exist (default: the smallest,
    so the sweep is fast and safe — override with --sync-pair N)
  - one one_way_pairs entry whose source exists and whose target is a
    LOCAL folder (default: smallest source — override with --one-way-pair N)

The one-way pair's SOURCE is never touched: its files are what repopulate
the emptied target when organize runs. Everything is recorded in a
manifest, and restore-major-folders.py puts the swept files back.

Cross-platform (macOS / Windows / Linux): the backup root lives under the
system temp directory, never under /tmp literally.

Usage:
  python3 src/test/backup-major-folders.py            # sweep + create test files
  python3 src/test/backup-major-folders.py --list     # just show the candidates
  python3 src/test/backup-major-folders.py --sync-pair 2 --one-way-pair 1
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import testplan_common as tc


def _refuse_repo_sweep(folder: Path) -> bool:
    """Never sweep a folder that contains this repo (e.g. someone paired
    their whole home folder)."""
    repo = tc.repo_root()
    return tc.is_under(repo, folder)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(tc.default_config_path()))
    parser.add_argument("--list", action="store_true",
                        help="print the candidate pairs and exit")
    parser.add_argument("--sync-pair", type=int, default=None, metavar="N",
                        help="1-based sync_pairs entry to use")
    parser.add_argument("--one-way-pair", type=int, default=None, metavar="N",
                        help="1-based one_way_pairs entry to use")
    args = parser.parse_args()

    try:
        cfg = tc.parse_config(Path(args.config))
    except ValueError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    sync_index = args.sync_pair - 1 if args.sync_pair else None
    ow_index = args.one_way_pair - 1 if args.one_way_pair else None

    if args.list:
        print(f"Config: {args.config}")
        print(tc.format_candidates(cfg))
        return

    chosen_sync, chosen_ow, messages = tc.select_pairs(
        cfg, sync_index, ow_index
    )
    for message in messages:
        print(f"NOTE: {message}")
    if chosen_sync is None:
        print("ERROR: no sync_pair to test — nothing swept.")
        sys.exit(1)

    if tc.MANIFEST_PATH.exists():
        print(f"ERROR: a previous backup still exists ({tc.BACKUP_ROOT}).")
        print("Run restore-major-folders.py first, or remove that folder")
        print("if you are sure you want to discard it.")
        sys.exit(1)

    # Folders to sweep: both sides of the sync pair + the one-way target.
    # Identical paths are swept once.
    to_sweep = []
    for side, folder in enumerate(chosen_sync["folders"]):
        to_sweep.append(("sync_pair", side, folder))
    if chosen_ow is not None:
        to_sweep.append(("one_way_target", 0, chosen_ow["folders"][1]))

    seen = set()
    unique = []
    for role, side, folder in to_sweep:
        if tc.real(folder) in seen:
            continue
        seen.add(tc.real(folder))
        unique.append((role, side, folder))

    for _role, _side, folder in unique:
        if _refuse_repo_sweep(folder):
            print(f"ERROR: refusing to sweep {folder} — it contains the "
                  "file_organizer repo. Choose a different pair with "
                  "--sync-pair / --one-way-pair.")
            sys.exit(1)

    tc.BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    manifest = {"folders": []}
    for i, (role, side, folder) in enumerate(unique):
        folder.mkdir(parents=True, exist_ok=True)
        dest = tc.BACKUP_ROOT / "folders" / str(i)
        dest.mkdir(parents=True, exist_ok=True)
        moved = 0
        for entry in folder.iterdir():
            shutil.move(str(entry), str(dest / entry.name))
            moved += 1
        manifest["folders"].append({
            "role": role,
            "side": side,
            "original": str(folder),
            "backup": str(dest),
            "moved": moved,
        })
        role_name = {"sync_pair": "sync pair side", "one_way_target":
                     "one-way target"}[role]
        print(f"{role_name:16} {folder}  moved {moved} item(s)")

    # The plan's test files, created right after the sweep.
    side_a, side_b = chosen_sync["folders"]
    (side_a / tc.SIDE_A_FILE).write_text(
        "file_organizer test file - side A\n" * 100
    )
    (side_b / tc.SIDE_B_FILE).write_text(
        "file_organizer test file - side B\n" * 100
    )
    project = side_a / tc.PROJECT
    (project / ".git").mkdir(parents=True, exist_ok=True)
    (project / "a.py").write_text('print("alpha")\n')
    (project / "b.py").write_text('print("beta")\n')
    (project / ".git" / "config").write_text("local git data\n")

    manifest["sync_pair"] = [str(f) for f in chosen_sync["folders"]]
    manifest["one_way_pair"] = (
        [str(f) for f in chosen_ow["folders"]] if chosen_ow else None
    )
    tc.MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))

    print()
    print("Swept folders are empty. Test files created:")
    print(f"  {side_a / tc.SIDE_A_FILE}")
    print(f"  {side_b / tc.SIDE_B_FILE}")
    print(f"  {project}  (a.py, b.py, .git — .git must NOT sync)")
    if chosen_ow is not None:
        src, dst = chosen_ow["folders"]
        print()
        print(f"One-way pair under test: {src}  ->  {dst}")
        print(f"The target {dst} was emptied; ./organize auto should")
        print("refill it from the source.")
    print()
    print("Run ./organize auto when the plan says so. Afterwards:")
    print("  python3 src/test/verify-test-results.py")
    print("  python3 src/test/restore-major-folders.py   (puts files back)")


if __name__ == "__main__":
    main()

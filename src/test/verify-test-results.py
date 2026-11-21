#!/usr/bin/env python3
"""Verify the end-to-end test run (the small-folder plan).

Checks everything a script can see from the computer:

  - the sync pair synchronised BOTH ways (each side's test file reached
    the other side, and the test project arrived without .git)
  - the one-way pair copied its source files into its target
  - the phone's ready files were pulled into the computer's folders,
    and the held (app-generated) files were NOT pulled
  - the router moved the ~/Downloads test file out of ~/Downloads
  - the second folder (cloud or external) really was read and written

The pairs and paths all come from the temporary test config the plan
built (drives, sync_pairs, one_way_pairs, phone_pull, router) — the real
config.yaml is never used here.

Usage:  python3 src/test/verify-test-results.py --config PATH
"""

import argparse
import os
import sys
from pathlib import Path

import testplan_common as tc

HOME = Path.home()

PHONE_PRESENT = [
    "20261111_celtic.png",          # DCIM/Camera - ready to copy
    "20261111_phone_notes.txt",     # Documents - ready to copy
    "20261111_phone_download.txt",  # Download - ready to copy
]
PHONE_ABSENT = [
    "Screenshot_20261114_666_X.jpg",  # renamed away on the phone
    "Screenshot_20261114_777_X.jpg",  # held - kept on the phone
    "IMG_20261114_153021.jpg",        # held - kept on the phone
]
DOWNLOADS_FILE = "20261111_downloads_test.txt"

SKIP_DIRS = {".git", ".tmp_pull", "__pycache__", ".venv", "node_modules"}

_results = []
_failures = 0


def report(kind: str, message: str) -> None:
    global _failures
    _results.append((kind, message))
    if kind == "FAIL":
        _failures += 1


def find(names, roots, depth: int = 8) -> dict:
    """Walk roots once; return {name: [paths]} for matching file names."""
    found = {name: [] for name in names}
    wanted = set(names)
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if os.path.relpath(dirpath, root).count(os.sep) >= depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fname in filenames:
                if fname in wanted:
                    found[fname].append(str(Path(dirpath) / fname))
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(tc.default_config_path()))
    args = parser.parse_args()

    try:
        cfg = tc.parse_config(Path(args.config))
    except ValueError as exc:
        report("FAIL", str(exc))
        for kind, msg in _results:
            print(f"{kind:4} {msg}")
        sys.exit(1)

    main_drive = Path(cfg["drives"].get("MAIN_DRIVE", str(HOME)))
    local_stage = tc.resolve(
        cfg["phone_pull"].get("local_stage", "MAIN_DRIVE/misc"), cfg
    )

    report("INFO", f"MAIN_DRIVE    = {main_drive}")
    report("INFO", f"phone pull stage = {local_stage}")
    report("INFO", "")

    # 1. Sync pair: both-ways synchronisation.
    sync_pairs = cfg.get("sync_pairs", [])
    if not sync_pairs:
        report("FAIL", "no sync pair in the test settings — both-ways "
               "sync not checked")
    else:
        side_a, side_b = (
            tc.resolve(sync_pairs[0][0], cfg), tc.resolve(sync_pairs[0][1], cfg)
        )
        for fname in (tc.SIDE_A_FILE, tc.SIDE_B_FILE):
            for label, side in (("side A", side_a), ("side B", side_b)):
                path = side / fname
                if path.is_file():
                    report("PASS", f"sync pair {label} has {fname}")
                else:
                    report(
                        "FAIL",
                        f"sync pair {label} missing {fname}: {path} "
                        "(both-ways sync did not copy it over)",
                    )
        project = side_a / tc.PROJECT
        mirror = side_b / tc.PROJECT
        for fname in tc.PROJECT_FILES:
            if not (project / fname).is_file():
                report("FAIL", f"project file missing: {project / fname}")
            if (mirror / fname).is_file():
                report("PASS", f"project synced across the pair: "
                       f"{mirror / fname}")
            else:
                report("FAIL", f"project did not sync: {mirror / fname}")
        if (mirror / ".git").exists():
            report("FAIL", f".git was synced to the other side: "
                   f"{mirror / '.git'}")
        else:
            report("PASS", ".git NOT synced (as expected)")

    # 2. One-way pair: every source file must be in the target.
    one_way = cfg.get("one_way_pairs", [])
    if not one_way:
        report("INFO", "no one-way pair in the test settings — one-way "
               "check skipped")
    else:
        src, dst = tc.resolve(one_way[0][0], cfg), tc.resolve(one_way[0][1], cfg)
        sample = []
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fname in filenames:
                sample.append(Path(dirpath) / fname)
        if not sample:
            report("INFO", f"one-way source {src} has no files")
        else:
            missing = 0
            for sp in sample:
                rel = sp.relative_to(src)
                tp = dst / rel
                if tp.is_file() and tp.stat().st_size == sp.stat().st_size:
                    continue
                missing += 1
                report("FAIL", f"one-way target missing or wrong size: {rel}")
            if missing:
                report("FAIL", f"one-way sync did not copy everything to "
                       f"{dst} ({missing}/{len(sample)} files wrong)")
            else:
                report("PASS", f"one-way sync copied {len(sample)}/"
                       f"{len(sample)} files into {dst}")

    # 3. Phone files: ready ones present, held ones absent.
    roots = [local_stage] + [
        main_drive / name
        for name in ("misc", "Documents", "Pictures", "Music", "Downloads")
    ]
    found = find(PHONE_PRESENT + PHONE_ABSENT, roots)
    for name in PHONE_PRESENT:
        if found[name]:
            report("PASS", f"phone file pulled: {name} -> {found[name][0]}")
        else:
            report(
                "FAIL",
                f"phone file NOT on the computer: {name} "
                "(did the test run on the phone too? was the phone "
                "plugged in with USB debugging on?)",
            )
    for name in PHONE_ABSENT:
        if found[name]:
            report("FAIL", f"held file unexpectedly on the computer: "
                   f"{name} -> {found[name][0]}")
        else:
            report("PASS", f"held file NOT pulled (as expected): {name}")

    # The renamed screenshot: the user picks the new name (the rename
    # prompt prefills a date), so accept ANY pulled .jpg that is not one
    # of the held names.
    staged_jpgs = []
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if os.path.relpath(dirpath, root).count(os.sep) >= 8:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fname in filenames:
                if fname.lower().endswith(".jpg") \
                        and fname not in PHONE_ABSENT \
                        and "Screenshots" in Path(dirpath).parts:
                    staged_jpgs.append(str(Path(dirpath) / fname))
    if staged_jpgs:
        report("PASS", f"renamed phone file pulled: {staged_jpgs[0]}")
    else:
        report(
            "FAIL",
            "no renamed phone file on the computer — the renamed "
            "screenshot was not pulled (did the phone finish its side?)",
        )

    # 4. Router should have moved the Downloads test file out.
    router = cfg["router"]
    router_on = str(router.get("enabled", "")).lower() in (
        "true", "yes", "on", "1",
    )
    dl_dir_real = tc.real(main_drive / "Downloads")
    watch_real = [
        tc.real(tc.resolve(w, cfg)) for w in router.get("watch_folders", [])
    ]
    dl = main_drive / "Downloads" / DOWNLOADS_FILE
    if not router_on or dl_dir_real not in watch_real:
        report(
            "INFO",
            "router is not enabled or does not watch ~/Downloads in this "
            "config — skipping the Downloads move check",
        )
    elif not dl.exists():
        targets = [
            tc.resolve(v, cfg)
            for v in router.get("target_folders", {}).values()
            if v
        ] or [main_drive]
        moved = find([DOWNLOADS_FILE], targets, depth=4)
        if moved[DOWNLOADS_FILE]:
            report("PASS", "router moved the Downloads file out -> "
                   + moved[DOWNLOADS_FILE][0])
        else:
            report(
                "FAIL",
                f"{DOWNLOADS_FILE} left ~/Downloads but was not found in "
                "the router's target folders",
            )
    else:
        report(
            "FAIL",
            f"{DOWNLOADS_FILE} is still in ~/Downloads — the router did "
            "not move it",
        )

    # 5. Remote read/write: the plan wrote small test files to the second
    # side of the pair — when a cloud or external folder was used, that
    # side lives outside MAIN_DRIVE.
    if sync_pairs:
        second_side = tc.resolve(sync_pairs[0][1], cfg)
        if second_side.is_dir():
            where = ("outside MAIN_DRIVE"
                     if not tc.is_under(second_side, main_drive)
                     else "local (no cloud or external folder in the config)")
            report("PASS", f"second folder exists and was written: "
                   f"{second_side} ({where})")
        else:
            report("FAIL", f"second folder missing after the run: "
                   f"{second_side}")

    # 6. External drive: informational (a starter path is expected to be
    # missing).
    external_raw = cfg["drives"].get("EXTERNAL_DRIVE", "")
    if external_raw:
        external = Path(external_raw)
        if external.is_dir():
            report("PASS", f"external drive found: {external}")
        else:
            report("INFO", f"external drive not found (expected for a "
                   f"starter path): {external}")

    print()
    for kind, msg in _results:
        if msg == "":
            continue
        print(f"{kind:4} {msg}")

    print()
    print("Manual checks (only you can do these):")
    print("  - On the PHONE: ls file_organizer_staging/DCIM/Screenshots")
    print("    should show your renamed file and NONE of the held ones")
    print("    (no 777_X, no 666_X).")
    print("  - Open the second folder (cloud or external) and eyeball")
    print("    the file_organizer_test folders there (they are removed")
    print("    when the test finishes).")

    if _failures:
        print(f"\n{_failures} check(s) FAILED.")
        sys.exit(1)
    print("\nAll scripted checks passed.")


if __name__ == "__main__":
    main()

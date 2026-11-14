#!/usr/bin/env python3
"""Verify the end-to-end test run (README: "End-to-end test plan").

Checks everything a script can see from the computer:

  - the phone's ready files were pulled into the computer's folders
  - the held (app-generated) files were NOT pulled
  - the chosen sync pair synchronised BOTH ways (each side's test file
    reached the other side, and the test project arrived without .git)
  - the chosen one-way pair refilled its emptied target from its source
  - the router moved the ~/Downloads test file out of ~/Downloads
  - organized/ soft links are intact and point at your real files

Nothing here is tied to one person's setup: the pairs and paths all come
from config.yaml (drives, sync_pairs, one_way_pairs, phone_pull, router,
output_base). Run it AFTER ./organize auto, BEFORE the restore script.

Usage:  python3 src/test/verify-test-results.py [--config PATH]
            [--sync-pair N] [--one-way-pair N]
"""

import argparse
import os
import sys
from pathlib import Path

import testplan_common as tc

HOME = Path.home()
REPO = tc.repo_root()

PHONE_PRESENT = [
    "20261111_celtic.png",          # DCIM/Camera - ready to copy
    "20261111_phone_notes.txt",     # Documents - ready to copy
    "20261111_phone_download.txt",  # Download - ready to copy
    "2026114_foo_baz.jpg",          # renamed from Screenshot_20261114_666_X.jpg
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
    parser.add_argument("--sync-pair", type=int, default=None, metavar="N",
                        help="1-based sync_pairs entry (as used by backup)")
    parser.add_argument("--one-way-pair", type=int, default=None, metavar="N",
                        help="1-based one_way_pairs entry (as used by backup)")
    args = parser.parse_args()

    try:
        cfg = tc.parse_config(Path(args.config))
    except ValueError as exc:
        report("FAIL", str(exc))
        for kind, msg in _results:
            print(f"{kind:4} {msg}")
        sys.exit(1)

    main_drive = Path(cfg["drives"].get("MAIN_DRIVE", str(HOME)))
    proton_raw = cfg["drives"].get("PROTON_DRIVE", "")
    proton_drive = Path(proton_raw) if proton_raw else None
    local_stage = tc.resolve(
        cfg["phone_pull"].get("local_stage", "MAIN_DRIVE/misc"), cfg
    )
    out_raw = cfg["top"].get("output_base", "organized") or "organized"
    organized = (
        REPO / "organized"
        if out_raw in ("organized", "~/organized")
        else Path(os.path.expanduser(out_raw))
    )

    sync_index = args.sync_pair - 1 if args.sync_pair else None
    ow_index = args.one_way_pair - 1 if args.one_way_pair else None
    manifest = tc.read_manifest()
    if manifest is not None:
        # The manifest is the contract: verify exactly the folders the
        # backup script swept (their sizes changed since then, so the
        # default 'smallest pair' pick could otherwise differ).
        sp = manifest.get("sync_pair")
        ow = manifest.get("one_way_pair")
        chosen_sync = (
            {"folders": [Path(sp[0]), Path(sp[1])]}
            if isinstance(sp, list) and len(sp) == 2 else None
        )
        chosen_ow = (
            {"folders": [Path(ow[0]), Path(ow[1])]}
            if isinstance(ow, list) and len(ow) == 2 else None
        )
        report("INFO", "pairs taken from the backup manifest")
    else:
        chosen_sync, chosen_ow, messages = tc.select_pairs(
            cfg, sync_index, ow_index
        )
        for message in messages:
            report("INFO", message)

    report("INFO", f"MAIN_DRIVE    = {main_drive}")
    report("INFO", f"PROTON_DRIVE  = {proton_drive}")
    report("INFO", f"phone pull stage = {local_stage}")
    report("INFO", f"organized     = {organized}")
    report("INFO", "")

    # 0. Safety nudge: another both-ways pair touching a swept folder will
    # also sync both ways during the test.
    if chosen_sync:
        swept = {tc.real(f) for f in chosen_sync["folders"]}
        if chosen_ow:
            swept.add(tc.real(chosen_ow["folders"][1]))
        chosen_real = {tc.real(f) for f in chosen_sync["folders"]}
        for pair in cfg.get("sync_pairs", []):
            if not isinstance(pair, list) or len(pair) != 2:
                continue
            a, b = tc.resolve(pair[0], cfg), tc.resolve(pair[1], cfg)
            key = {tc.real(a), tc.real(b)}
            if key == chosen_real:
                continue
            if key & swept:
                report(
                    "INFO",
                    f"WARNING: another both-ways pair ({pair[0]} <-> "
                    f"{pair[1]}) touches a swept folder and syncs both "
                    "ways during the test",
                )

    # 1. Phone files: ready ones present, held ones absent.
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
                "(did ./organize auto run on the phone AND the computer? "
                "is the phone's min_age_minutes 0? is the file older than "
                "the computer's phone_pull.min_age_minutes?)",
            )
    for name in PHONE_ABSENT:
        if found[name]:
            report(
                "FAIL",
                f"held file unexpectedly on the computer: {name} -> "
                f"{found[name][0]}",
            )
        else:
            report("PASS", f"held file NOT pulled (as expected): {name}")

    # 2. Sync pair: both-ways synchronisation.
    if chosen_sync:
        side_a, side_b = chosen_sync["folders"]
        for fname in (tc.SIDE_A_FILE, tc.SIDE_B_FILE):
            for label, side in (("side A", side_a), ("side B", side_b)):
                path = side / fname
                if path.is_file():
                    report("PASS", f"sync pair {label} has {fname}: {path}")
                else:
                    report(
                        "FAIL",
                        f"sync pair {label} missing {fname}: {path} "
                        "(both-ways sync did not copy it over)",
                    )
        project = side_a / tc.PROJECT
        mirror = side_b / tc.PROJECT
        for fname in tc.PROJECT_FILES:
            if (project / fname).is_file():
                report("PASS", f"project file exists: {project / fname}")
            else:
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
            report("PASS", f".git NOT synced (as expected): "
                    f"{mirror / '.git'} absent")
        if (project / ".git").is_dir():
            report("PASS", f"source .git exists ({project / '.git'}) — so "
                    "the exclude list is what kept it out")
    else:
        report("FAIL", "no sync pair selected — both-ways sync not checked")

    # 3. One-way pair: the emptied target must have been refilled from the
    # source by ./organize auto.
    if chosen_ow:
        src, dst = chosen_ow["folders"]
        sample = []
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fname in filenames:
                sample.append(Path(dirpath) / fname)
                if len(sample) >= 5:
                    break
            if len(sample) >= 5:
                break
        if not sample:
            report("INFO", f"one-way source {src} has no files — nothing "
                    "to refill from")
        else:
            missing = 0
            for sp in sample:
                rel = sp.relative_to(src)
                tp = dst / rel
                if tp.is_file() and tp.stat().st_size == sp.stat().st_size:
                    continue
                missing += 1
                report("FAIL", f"one-way target missing or wrong size: "
                        f"{rel} (expected in {dst})")
            if missing:
                report(
                    "FAIL",
                    f"one-way sync did not refill {dst} from {src} "
                    f"({missing}/{len(sample)} sampled files wrong)",
                )
            else:
                report(
                    "PASS",
                    f"one-way sync refilled {dst} from {src} "
                    f"({len(sample)}/{len(sample)} sampled files present)",
                )
    else:
        report("INFO", "no one-way pair selected — one-way check skipped")

    # 4. Router should have moved the Downloads test file out (only when
    # this config's router actually watches Downloads — otherwise skip).
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
            "config — skipping the Downloads move check"
            + ("" if dl.exists() else f" (note: {DOWNLOADS_FILE} is not in "
               "~/Downloads)"),
        )
    elif not dl.exists():
        targets = [
            tc.resolve(v, cfg)
            for v in router.get("target_folders", {}).values()
            if v
        ] or [main_drive]
        moved = find([DOWNLOADS_FILE], targets, depth=4)
        if moved[DOWNLOADS_FILE]:
            report(
                "PASS",
                "router moved the Downloads file out (as expected) -> "
                + moved[DOWNLOADS_FILE][0],
            )
        else:
            report(
                "FAIL",
                f"{DOWNLOADS_FILE} left ~/Downloads but was not found in "
                "the router's target folders (search depth 4) — look by "
                "hand if your router buckets deeper",
            )
    else:
        report(
            "FAIL",
            f"{DOWNLOADS_FILE} is still in "
            "~/Downloads — the router did not move it (router disabled? "
            "Downloads not a watch folder?)",
        )

    # 5. Proton Drive mirrors of the phone files — informational only:
    # whether they also appear under PROTON_DRIVE depends on the pairs in
    # your config, which vary from machine to machine.
    if proton_drive:
        proton_roots = [
            proton_drive / name for name in ("misc", "Documents", "Pictures")
        ]
        pm = find(PHONE_PRESENT, proton_roots)
        for name in PHONE_PRESENT:
            if pm[name]:
                report("INFO", f"phone file mirrored on Proton Drive: "
                        f"{name} -> {pm[name][0]}")
            else:
                report("INFO", f"phone file NOT mirrored under "
                        "PROTON_DRIVE/misc|Documents|Pictures: "
                        f"{name} (depends on your sync pairs)")

    # 6. organized/ soft links.
    if organized.is_dir():
        total = broken = into_main = 0
        examples = []
        samples = []
        main_real = tc.real(main_drive)
        for dirpath, dirnames, filenames in os.walk(organized):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fname in filenames:
                link = Path(dirpath) / fname
                if not link.is_symlink():
                    continue
                total += 1
                if not os.path.exists(link):
                    broken += 1
                    if len(examples) < 10:
                        examples.append(str(link))
                    continue
                target = tc.real(link)
                if target.startswith(main_real + os.sep):
                    into_main += 1
                if len(samples) < 5:
                    samples.append(target)
        report("PASS" if total else "FAIL",
               f"organized/ contains {total} soft link(s)")
        if broken:
            report("FAIL", f"{broken} broken soft link(s), e.g. "
                    f"{examples[0]}")
        else:
            report("PASS", "no broken soft links")
        if into_main:
            report(
                "PASS",
                f"organized/ links point at your real files under "
                f"MAIN_DRIVE: {into_main}",
            )
            report("INFO", "sample link targets: " + " ; ".join(samples))
        else:
            report(
                "FAIL",
                "no organized/ links point under MAIN_DRIVE — check your "
                "source_folders / organize_folders",
            )
    else:
        report("FAIL", f"organized folder missing: {organized}")

    print()
    for kind, msg in _results:
        if msg == "":
            continue
        print(f"{kind:4} {msg}")

    print()
    print("Manual checks (only you can do these):")
    print("  - On the PHONE: ls file_organizer_staging/DCIM/Screenshots")
    print("    should show ONLY 2026114_foo_baz.jpg (no 777_X, no 666_X).")
    if chosen_sync:
        side_a, side_b = chosen_sync["folders"]
        print(f"  - Both sides of the sync pair now have each other's test")
        print(f"    files: {side_a} and {side_b}")
        print(f"    ({tc.SIDE_A_FILE}, {tc.SIDE_B_FILE}, and")
        print(f"    {tc.PROJECT} with a.py + b.py and NO .git on the other")
        print("    side).")
    if chosen_ow:
        src, dst = chosen_ow["folders"]
        print(f"  - The one-way target {dst} should now contain copies of")
        print(f"    files from {src}.")
    print("  - Open organized/ and follow a few links to your real files.")
    print("  - Open Proton Drive and eyeball the pair folders above.")

    if _failures:
        print(f"\n{_failures} check(s) FAILED.")
        sys.exit(1)
    print("\nAll scripted checks passed.")


if __name__ == "__main__":
    main()

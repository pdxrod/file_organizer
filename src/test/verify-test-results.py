#!/usr/bin/env python3
"""Verify the end-to-end test run (README: "End-to-end test plan").

Checks everything a script can see from the computer:

  - the phone's ready files were pulled into the computer's folders
  - the held (app-generated) files were NOT pulled
  - the ~/dev test project reached Proton Drive, with .git excluded
  - the router moved the ~/Downloads test file out of ~/Downloads
  - the major-folder test files are still in place
  - organized/ soft links are intact and reach into ~/pol and ~/dev

Prints PASS/FAIL/INFO per check, then the manual checks only you can do
(Proton Drive app, phone staging folder). Exits non-zero when a hard
check fails.

Usage:  python3 src/test/verify-test-results.py [--config PATH]
                                                [--dev-folder NAME]
"""

import argparse
import os
import sys
from pathlib import Path

HOME = Path.home()
REPO = Path(__file__).resolve().parent.parent.parent  # the file_organizer folder

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
MAJOR_FILES = {
    "Pictures": "20261111_pictures_test.txt",
    "Documents": "20261111_documents_test.txt",
    "Music": "20261111_music_test.txt",
    "misc": "20261111_misc_test.txt",
}
DOWNLOADS_FILE = "20261111_downloads_test.txt"
DEV_FOLDER = "sync_test_project"
DEV_FILES = ["a.py", "b.py"]

SKIP_DIRS = {".git", ".tmp_pull", "__pycache__", ".venv", "node_modules"}

_results = []
_failures = 0


def report(kind: str, message: str) -> None:
    global _failures
    _results.append((kind, message))
    if kind == "FAIL":
        _failures += 1


def parse_config(path: Path) -> dict:
    """Minimal de-dented-YAML reader: drives: and phone_pull: maps plus
    top-level scalar keys. Enough to resolve the paths this script checks."""
    cfg = {"drives": {}, "phone_pull": {}, "top": {}}
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        report("FAIL", f"cannot read config {path}: {exc}")
        return cfg
    section = None
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        key, _, val = stripped.partition(":")
        val = val.strip().strip("'\"").strip()
        if indent == 0:
            cfg["top"][key] = val
            section = key
        elif section in ("drives", "phone_pull") and indent == 2:
            cfg[section][key] = val
    return cfg


def resolve(ref: str, cfg: dict) -> Path:
    """'MAIN_DRIVE/misc' -> '/Users/x/misc'; plain paths pass through."""
    ref = os.path.expanduser(ref)
    path = Path(ref)
    if path.is_absolute():
        return path
    head, _, rest = ref.partition("/")
    if head in cfg["drives"]:
        base = Path(cfg["drives"][head])
        return base / rest if rest else base
    return path


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
    parser.add_argument("--config", default=str(REPO / "config.yaml"))
    parser.add_argument("--dev-folder", default=DEV_FOLDER)
    args = parser.parse_args()

    cfg = parse_config(Path(args.config))
    main_drive = Path(cfg["drives"].get("MAIN_DRIVE", str(HOME)))
    proton_raw = cfg["drives"].get("PROTON_DRIVE", "")
    proton_drive = Path(proton_raw) if proton_raw else None
    local_stage = resolve(
        cfg["phone_pull"].get("local_stage", "MAIN_DRIVE/misc"), cfg
    )
    out_raw = cfg["top"].get("output_base", "organized") or "organized"
    organized = (
        REPO / "organized"
        if out_raw in ("organized", "~/organized")
        else Path(os.path.expanduser(out_raw))
    )

    report("INFO", f"MAIN_DRIVE    = {main_drive}")
    report("INFO", f"PROTON_DRIVE  = {proton_drive}")
    report("INFO", f"phone pull stage = {local_stage}")
    report("INFO", f"organized     = {organized}")
    report("INFO", "")

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

    # 2. Major-folder test files are still in place.
    for folder, fname in MAJOR_FILES.items():
        path = main_drive / folder / fname
        if path.is_file():
            report("PASS", f"major-folder file in place: {path}")
        else:
            report(
                "FAIL",
                f"major-folder file missing: {path} "
                "(did backup-major-folders.py run first and the file get "
                "created afterwards?)",
            )

    # 3. Router should have moved the Downloads test file out.
    dl = main_drive / "Downloads" / DOWNLOADS_FILE
    if not dl.exists():
        moved = find(
            [DOWNLOADS_FILE],
            [main_drive / "pol", main_drive / "dev"],
            depth=4,
        )
        if moved[DOWNLOADS_FILE]:
            report(
                "PASS",
                "router moved the Downloads file out (as expected) -> "
                + moved[DOWNLOADS_FILE][0],
            )
        else:
            report(
                "FAIL",
                f"{DOWNLOADS_FILE} left ~/Downloads but was not found under "
                "~/pol or ~/dev (search depth 4) — look by hand if your "
                "router buckets deeper",
            )
    else:
        report(
            "FAIL",
            f"{DOWNLOADS_FILE} is still in "
            "~/Downloads — the router did not move it (router disabled? "
            "Downloads not a watch folder?)",
        )

    # 4. dev test project: local files, Proton mirror, .git excluded.
    dev_proj = main_drive / "dev" / args.dev_folder
    for fname in DEV_FILES:
        path = dev_proj / fname
        if path.is_file():
            report("PASS", f"dev project file exists: {path}")
        else:
            report("FAIL", f"dev project file missing: {path}")
    if proton_drive:
        p_dev = proton_drive / "dev" / args.dev_folder
        for fname in DEV_FILES:
            path = p_dev / fname
            if path.is_file():
                report("PASS", f"Proton mirror: {path}")
            else:
                report(
                    "FAIL",
                    f"Proton mirror missing: {path} (is MAIN_DRIVE/dev <-> "
                    "PROTON_DRIVE/dev a sync pair in your config?)",
                )
        if (p_dev / ".git").exists():
            report("FAIL", f".git was copied to Proton Drive: {p_dev / '.git'}")
        else:
            report(
                "PASS",
                f".git NOT copied to Proton Drive (as expected): "
                f"{p_dev / '.git'} absent",
            )
        if (dev_proj / ".git").is_dir():
            report(
                "PASS",
                f"source .git exists ({dev_proj / '.git'}) — so the "
                "exclude list is what kept it out",
            )
    else:
        report("INFO", "PROTON_DRIVE not set in config — Proton checks skipped")

    # 5. Proton mirrors of the phone files and mirrored major folders.
    if proton_drive:
        proton_roots = [
            proton_drive / name for name in ("misc", "Documents", "Pictures")
        ]
        mirror_names = (
            PHONE_PRESENT
            + [MAJOR_FILES["misc"], MAJOR_FILES["Documents"],
               MAJOR_FILES["Pictures"]]
        )
        pm = find(mirror_names, proton_roots)
        for name in mirror_names:
            if pm[name]:
                report("PASS", f"Proton Drive mirror: {name} -> {pm[name][0]}")
            else:
                report(
                    "FAIL",
                    f"not mirrored to Proton Drive: {name} "
                    "(misc/Documents/Pictures sync pairs missing, or the "
                    "sync did not run)",
                )
        report(
            "INFO",
            "Music is NOT expected in Proton Drive — its one-way pair "
            "copies FROM Proton Drive.",
        )

    # 6. organized/ soft links.
    if organized.is_dir():
        total = broken = pol = dev = 0
        examples = []
        # realpath both sides: config paths may go through symlinks
        # (e.g. /tmp -> /private/tmp on macOS) while link targets do not.
        pol_base = os.path.realpath(main_drive / "pol")
        dev_base = os.path.realpath(main_drive / "dev")
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
                target = os.path.realpath(link)
                if target.startswith(pol_base):
                    pol += 1
                elif target.startswith(dev_base):
                    dev += 1
        report("PASS" if total else "FAIL",
               f"organized/ contains {total} soft link(s)")
        if broken:
            report("FAIL", f"{broken} broken soft link(s), e.g. {examples[0]}")
        else:
            report("PASS", "no broken soft links")
        report("PASS" if pol else "FAIL",
               f"organized/ links into ~/pol: {pol}")
        report("PASS" if dev else "FAIL",
               f"organized/ links into ~/dev: {dev}")
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
    print("  - Open Proton Drive: Documents/, Pictures/, dev/sync_test_project/")
    print("    (a.py + b.py, NO .git), misc/DCIM/Camera/20261111_celtic.png.")
    print("  - Open organized/ and follow a few links into ~/pol and ~/dev.")

    if _failures:
        print(f"\n{_failures} check(s) FAILED.")
        sys.exit(1)
    print("\nAll scripted checks passed.")


if __name__ == "__main__":
    main()

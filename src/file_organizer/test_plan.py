#!/usr/bin/env python3
"""'organize test' — the guided end-to-end test runner.

Two halves, one on each device:

  ./organize test          on the COMPUTER — stops the daemon, sweeps one
                           sync pair (both sides) and one one-way pair
                           (local target) into a temp backup, drops the
                           test files, empties organized/, hands over to
                           the phone, runs ./organize auto, verifies the
                           results, and puts your files back.

  ./organize test          on the PHONE — creates the phone test files,
                           walks the user through the renames, runs the
                           production scan, and sends the user back to
                           the computer.

Every step is announced first and waits for Enter, so the user always
knows what is happening and what to do next.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent  # file_organizer/
SRC = Path(__file__).resolve().parent.parent          # src/
# testplan_common lives in src/test (plain scripts, not a package)
if str(SRC / "test") not in sys.path:
    sys.path.insert(0, str(SRC / "test"))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import testplan_common as tc  # noqa: E402

DOWNLOAD_TEST_FILE = "20261111_downloads_test.txt"


def _pause(message: str) -> None:
    """Print the message and wait for Enter or x (EOF keeps going)."""
    print()
    print(message)
    print()
    try:
        answer = input("Press Enter to continue, or x to cancel... ")
    except EOFError:
        print("(no interactive input available — continuing)")
        return
    except KeyboardInterrupt:
        print("\nStopped.")
        raise SystemExit(130)
    if answer.strip().lower() == "x":
        print("Stopped.")
        raise SystemExit(0)


def _run(cmd, check: bool = False) -> int:
    """Run a subprocess with live output; return its exit code."""
    print()
    print("> " + " ".join(str(c) for c in cmd))
    print()
    code = subprocess.call([str(c) for c in cmd])
    if check and code != 0:
        print(f"\nThat command failed (exit {code}).")
    return code


def _run_python(script_rel: str, *args: str, check: bool = False) -> int:
    return _run([sys.executable, str(REPO / script_rel), *args], check=check)


def _run_organize(*args: str, check: bool = False) -> int:
    return _run([sys.executable, str(REPO / "organize"), *args], check=check)


def _router_watches_downloads(cfg) -> bool:
    router = cfg["router"]
    on = str(router.get("enabled", "")).lower() in (
        "true", "yes", "on", "1",
    )
    main = Path(cfg["drives"].get("MAIN_DRIVE", str(Path.home())))
    watched = [
        tc.real(tc.resolve(w, cfg)) for w in router.get("watch_folders", [])
    ]
    return on and tc.real(main / "Downloads") in watched


# ── computer half ──────────────────────────────────────────────────────────

def run_computer_test() -> int:
    print()
    print("=" * 64)
    print("  organize test — guided end-to-end test (computer side)")
    print("=" * 64)
    print()
    print("This walks the whole pipeline: phone scan -> staging folder ->")
    print("USB pull -> organize (soft links) -> sync pairs -> cloud.")
    print()
    print("Along the way it will:")
    print("  - stop the daemon")
    print("  - sweep ONE sync pair (both sides) and ONE one-way pair")
    print("    (local target) into a temp backup, then drop test files")
    print("  - empty organized/ (soft links only — real files untouched)")
    print("  - hand over to the phone, then run ./organize auto here")
    print("  - check the results and put your files back")
    print()
    _pause(
        "This test moves real files to a temp backup first — nothing is\n"
        "deleted, and nothing on Proton Drive is emptied. Type x at any\n"
        "pause to cancel."
    )

    config_path = tc.default_config_path()
    if not config_path.exists():
        print(f"\nERROR: {config_path} not found.")
        print("Run './organize config' once, then './organize test' again.")
        return 1
    try:
        cfg = tc.parse_config(config_path)
    except ValueError as exc:
        print(f"\nERROR: {exc}")
        return 1

    out_raw = cfg["top"].get("output_base", "organized") or "organized"
    organized = (
        REPO / "organized"
        if out_raw in ("organized", "~/organized")
        else Path(os.path.expanduser(out_raw))
    )

    # 1. Stop the daemon so nothing runs against the swept folders.
    _run_organize("stop")

    # 2. Undo any previous run, then sweep + create the test files.
    _pause("Ready to sweep the test folders?")
    _run_python("src/test/restore-major-folders.py")
    if _run_python("src/test/backup-major-folders.py", check=True) != 0:
        print("\nThe sweep failed — see the message above. Typical fixes:")
        print("  - a previous backup still exists: run")
        print("    'python3 src/test/restore-major-folders.py' first")
        print("  - list the candidate pairs:")
        print("    'python3 src/test/backup-major-folders.py --list'")
        return 1

    # 3. Router test file (only when this config's router watches
    # ~/Downloads).
    if _router_watches_downloads(cfg):
        main = Path(cfg["drives"].get("MAIN_DRIVE", str(Path.home())))
        path = main / "Downloads" / DOWNLOAD_TEST_FILE
        path.write_text("file_organizer router test\n")
        print(f"\nCreated router test file: {path}")
    else:
        print("\nRouter does not watch ~/Downloads in this config — "
              "skipping that part.")

    # 4. Clean organized/.
    if organized.is_dir():
        shutil.rmtree(organized)
    print(f"Emptied {organized} (soft links only — real files untouched).")

    # 5. Hand over to the phone.
    _pause(
        "PHONE TIME.\n"
        "\n"
        "  1. Plug the phone into USB and make sure USB debugging is ON\n"
        "     (Settings -> Developer options -> USB debugging).\n"
        "  2. On the phone in Termux:\n"
        "       cd <the file_organizer folder>\n"
        "       git pull\n"
        "       ./organize test\n"
        "     The phone creates the test files, walks you through the\n"
        "     renames, runs its production scan, and then tells you to\n"
        "     come back here.\n"
        "\n"
        "  Timing note: this computer only pulls staged files older than\n"
        "  phone_pull.min_age_minutes (5). If the next step pulls 0 files,\n"
        "  wait 5 minutes (or set that value to 0 in config.yaml) and run\n"
        "  './organize auto' again.\n"
        "\n"
        "  (The phone can also just be on the same WiFi with wireless\n"
        "  ADB — './organize list' shows what this computer can see.)"
    )

    # 6. Production run on the computer: pull first, then organize, router
    # and sync.
    if _run_organize("auto", check=True) != 0:
        print("'organize auto' reported a problem — the verifier below")
        print("will still show what did and did not happen.")

    # 7. Verify.
    code = _run_python("src/test/verify-test-results.py")
    if code == 0:
        print("\nALL SCRIPTED CHECKS PASSED.")
    else:
        print("\n" + "-" * 64)
        print("Some checks FAILED — see the list above. Common causes:")
        print("  - the phone steps were skipped or the rename did not happen")
        print("  - files younger than phone_pull.min_age_minutes (wait 5")
        print("    minutes and re-run './organize auto')")
        print("  - a sync pair or router setting differs from the plan")

    # 8. Put the swept files back.
    _pause("Now put your files back? (runs restore-major-folders.py)")
    _run_python("src/test/restore-major-folders.py")

    print()
    print("Done. Your config.yaml was never touched. Files the test")
    print("copied around stay where they are — delete them by hand if")
    print("you don't want them.")
    return code


# ── phone half ─────────────────────────────────────────────────────────────

def run_phone_test() -> int:
    from file_organizer import phone_sync

    print()
    print("=" * 64)
    print("  organize test — guided end-to-end test (phone side)")
    print("=" * 64)
    print()
    print("This half creates test files on the phone, walks you through")
    print("the renames, and runs the production scan that stages files")
    print("for the computer to pull.")

    cfg_path = REPO / "phone_sync_config.yaml"
    if not cfg_path.exists():
        print(f"\nERROR: {cfg_path} not found.")
        print("Run './organize make-config' on this phone first (it is")
        print("created from the template), then './organize test' again.")
        return 1

    sync = phone_sync.PhoneSync(str(cfg_path))
    if sync.min_age != 0:
        minutes = sync.min_age // 60
        print(f"\nYour grace period is {minutes} minute(s) — fresh files")
        print("will NOT be copied until they are that old, so the test")
        print("would fail.")
        print("Fix it first: './organize config' -> 'Grace period (minutes)'")
        print("-> 0 -> save. Then run './organize test' again.")
        return 1

    _pause("Ready to create the test files on this phone?")
    if _run_python("src/test/create-files-on-phone.py", check=True) != 0:
        print("\nCould not create the test files — see above. (Termux")
        print("storage permission missing? Run 'termux-setup-storage'.)")
        return 1

    _pause(
        "Now the rename walk. You will be prompted for each held file:\n"
        "  - Screenshot_20261114_666_X.jpg  -> type the new name:\n"
        "       2026114_foo_baz.jpg\n"
        "  - Screenshot_20261114_777_X.jpg  -> press Enter alone (keep)\n"
        "  - IMG_20261114_153021.jpg        -> press Enter alone (keep)\n"
        "  - 'x' stops the walk at any time.\n"
        "\n"
        "Keeping two of them held is the point: it proves held files\n"
        "are not copied, while the renamed one now copies."
    )
    phone_sync.rename_held_interactive(sync)

    _pause(
        "Now the production scan — the ready files are copied into\n"
        "file_organizer_staging/ (the held ones stay on the phone)."
    )
    stats = sync.run_once(dry_run=False)
    print()
    print(f"Scan results: copied {stats.get('copied', 0)}, "
          f"held {stats.get('held', 0)}, "
          f"skipped {stats.get('skipped', 0)}, "
          f"errors {stats.get('errors', 0)}")

    print()
    print("Phone side done. The staged files are ready for the computer")
    print("to pull. Keep the phone plugged in (USB debugging ON) and go")
    print("back to the computer — press Enter THERE to continue.")
    return 0

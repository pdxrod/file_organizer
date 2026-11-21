#!/usr/bin/env python3
"""'organize test' — the guided end-to-end test runner.

Two halves, one on each device:

  ./organize test          on the COMPUTER — stops the daemon, creates a
                           few SMALL test files in throwaway folders on
                           this computer and on Proton Drive, hands over
                           to the phone, runs one full cycle against
                           those folders, checks the results, and clears
                           the test files away. Your own files are never
                           moved or deleted, and the big folders are
                           never touched.

  ./organize test          on the PHONE — creates the phone test files,
                           walks the user through the renames, copies the
                           ready files into the staging folder, and sends
                           the user back to the computer.

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

# Throwaway folders the computer half uses — created under MAIN_DRIVE and
# PROTON_DRIVE for the test, deleted again at the end. They must be small
# so a full test run takes minutes, not hours.
TEST_FOLDER = "file_organizer_test"


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


def _run(cmd, check: bool = False, quiet: bool = False) -> int:
    """Run a subprocess with live output; return its exit code."""
    if not quiet:
        print()
        print("> " + " ".join(str(c) for c in cmd))
        print()
    code = subprocess.call([str(c) for c in cmd])
    if check and code != 0:
        print(f"\nThat command failed (exit {code}).")
    return code


def _run_python(script_rel: str, *args: str, check: bool = False,
                quiet: bool = False) -> int:
    return _run([sys.executable, str(REPO / script_rel), *args],
                check=check, quiet=quiet)


def _run_organize(*args: str, check: bool = False,
                  quiet: bool = False) -> int:
    return _run([sys.executable, str(REPO / "organize"), *args],
                check=check, quiet=quiet)


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


def _small(path: Path, name: str) -> None:
    """Write a tiny file (over the router's 1 KB minimum, so the router
    really moves the Downloads test file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"file_organizer test file: {name}\n" * 50)


# ── computer half ──────────────────────────────────────────────────────────

def run_computer_test() -> int:
    print()
    print("=" * 64)
    print("  organize test — guided end-to-end test (computer side)")
    print("=" * 64)
    print()
    print("This test checks, with a few small files, that this computer")
    print("can:")
    print("  - sync files between it and your Proton Drive folder")
    print("  - pull the phone's staged files")
    print("  - route a file out of ~/Downloads")
    print()
    print("Your own files are never moved or deleted, and none of your")
    print("big folders are touched.")
    _pause("Press Enter to run it, or x to cancel.")

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

    main = Path(cfg["drives"].get("MAIN_DRIVE", str(Path.home())))
    proton_raw = cfg["drives"].get("PROTON_DRIVE", "")
    proton = Path(proton_raw) if proton_raw else None
    if proton and proton.is_dir():
        print(f"\nProton Drive folder found: {proton}")
    elif proton:
        print(f"\nProton Drive folder NOT found: {proton}")
        print("The cloud parts of the test cannot run. Plug it in or")
        print("check the drive path in the config, then try again.")
        return 1
    external_raw = cfg["drives"].get("EXTERNAL_DRIVE", "")
    if external_raw:
        external = Path(external_raw)
        if external.is_dir():
            print(f"External drive found: {external}")
        else:
            print(f"External drive not found (expected for a starter")
            print(f"path): {external}")

    # 1. Stop the daemon so it does not run alongside the test.
    _run_organize("stop", quiet=True)

    # 2. Create the small test folders and files.
    sync_a = main / TEST_FOLDER / "sync"
    sync_b = proton / TEST_FOLDER / "sync"
    ow_src = proton / TEST_FOLDER / "oneway"
    ow_dst = main / TEST_FOLDER / "oneway"
    for folder in (main / TEST_FOLDER, proton / TEST_FOLDER):
        shutil.rmtree(folder, ignore_errors=True)
    _small(sync_a / tc.SIDE_A_FILE, "side A")
    _small(sync_b / tc.SIDE_B_FILE, "side B")
    for fname in tc.PROJECT_FILES:
        _small(sync_a / tc.PROJECT / fname, fname)
    (sync_a / tc.PROJECT / ".git").mkdir(parents=True)
    (sync_a / tc.PROJECT / ".git" / "config").write_text("x")
    for name in ("oneway_1.txt", "oneway_2.txt"):
        _small(ow_src / name, name)
    print(f"\nCreated a few small test files in {main / TEST_FOLDER}")
    print(f"and {proton / TEST_FOLDER}.")

    # 3. A temporary config that swaps the real pairs for the small test
    # folders. The real config.yaml is never touched.
    test_cfg = REPO / ".organize_test_config.yaml"
    try:
        from file_organizer.config import Config
        import yaml
        real_cfg = Config(str(config_path))
        raw = dict(real_cfg.raw_config)
        raw["sync_pairs"] = [{"folders": [str(sync_a), str(sync_b)]}]
        raw["one_way_pairs"] = [{"folders": [str(ow_src), str(ow_dst)]}]
        raw["phone_pull"]["min_age_minutes"] = 0  # fresh files copy at once
        test_cfg.write_text(yaml.safe_dump(raw, sort_keys=False))
    except Exception as exc:
        print(f"\nERROR: could not build the test settings: {exc}")
        return 1

    # 4. Router test file (only when this config's router watches
    # ~/Downloads).
    dl_path = main / "Downloads" / DOWNLOAD_TEST_FILE
    if _router_watches_downloads(cfg):
        _small(dl_path, "router")
        print(f"\nCreated a test file in ~/Downloads — the router should")
        print("move it out.")
    else:
        print("\nRouter does not watch ~/Downloads in this config —")
        print("skipping that part.")

    # 5. Hand over to the phone.
    _pause(
        "PHONE TIME.\n"
        "\n"
        "  On the phone, open Termux, cd to the\n"
        "  file_organizer folder, and run './organize test'.\n"
        "  Follow its prompts, then come back to this\n"
        "  computer and press Enter here."
    )

    # 6. One full cycle against the small test folders (subcommand first —
    # the organizer script reads the command before any flags).
    if _run_organize("auto", "--config", str(test_cfg), check=True,
                     quiet=True) != 0:
        print("The run reported a problem — the checks below will show")
        print("what did and did not happen.")

    # 7. Verify.
    code = _run_python("src/test/verify-test-results.py",
                       "--config", str(test_cfg), quiet=True)
    if code == 0:
        print("\nALL SCRIPTED CHECKS PASSED.")
    else:
        print("\n" + "-" * 64)
        print("Some checks FAILED — see the list above. Common causes:")
        print("  - the phone steps were skipped or the rename did not happen")
        print("  - the phone was not plugged in for the pull")

    # 8. Clear the test files away.
    for folder in (main / TEST_FOLDER, proton / TEST_FOLDER):
        shutil.rmtree(folder, ignore_errors=True)
    dl_path.unlink(missing_ok=True)
    test_cfg.unlink(missing_ok=True)

    print()
    print("Done. Your config.yaml was never touched.")
    print()
    print("In everyday use:")
    print("  - './organize auto' runs one full cycle, like the run this")
    print("    test just did")
    print("  - './organize start' runs it in the background on a schedule")
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
    print("the renames, and copies the ready files into the staging")
    print("folder for the computer to collect.")

    cfg_path = REPO / "phone_sync_config.yaml"
    if not cfg_path.exists():
        print(f"\nERROR: {cfg_path} not found.")
        print("Run './organize make-config' on this phone first (it is")
        print("created from the template), then './organize test' again.")
        return 1

    sync = phone_sync.PhoneSync(str(cfg_path))

    _pause("Ready to create the test files on this phone?")
    if _run_python("src/test/create-files-on-phone.py", check=True) != 0:
        print("\nCould not create the test files — see above. (Termux")
        print("storage permission missing? Run 'termux-setup-storage'.)")
        return 1

    _pause(
        "Next: the renames. For each held file:\n"
        "\n"
        "  Screenshot_20261114_666_X.jpg\n"
        "     clear the pre-filled date, then type the\n"
        "     new name: 2026114_foo_baz.jpg\n"
        "  Screenshot_20261114_777_X.jpg\n"
        "     press Enter alone to keep it\n"
        "  IMG_20261114_153021.jpg\n"
        "     press Enter alone to keep it\n"
        "  x at any prompt stops the walk\n"
        "\n"
        "Screenshot_20261114_777_X.jpg and\n"
        "IMG_20261114_153021.jpg are not copied —\n"
        "they stay on the phone. You renamed\n"
        "Screenshot_20261114_666_X.jpg to\n"
        "2026114_foo_baz.jpg, so it will be copied.\n"
        "\n"
        "Files that look renamed are copied; files an\n"
        "app created and left as they are stay on the\n"
        "phone."
    )
    phone_sync.rename_held_interactive(sync)

    _pause(
        "Now the files you renamed are copied into\n"
        "file_organizer_staging/ on the phone, ready\n"
        "for the computer to collect. App-created\n"
        "files stay on the phone."
    )
    stats = sync.run_once(dry_run=False)
    print()
    print(f"Scan results: copied {stats.get('copied', 0)}, "
          f"held {stats.get('held', 0)}, "
          f"skipped {stats.get('skipped', 0)}, "
          f"errors {stats.get('errors', 0)}")
    _pause(
        "Phone side done. The staged files are ready for the\n"
        "computer to collect.\n"
        "\n"
        "Keep the phone plugged in (USB debugging ON) and go\n"
        "back to the computer — press Enter THERE to continue."
    )
    return 0

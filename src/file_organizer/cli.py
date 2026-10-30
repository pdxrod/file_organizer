#!/usr/bin/env python3
"""CLI entry point for file_organizer."""

import os, sys, argparse, logging, signal, subprocess, time
from pathlib import Path

from file_organizer.config import Config, ConfigError, report_first_run, resolve_log_path, find_project_root, ensure_output_dir
from file_organizer.scanner import FileScanner
from file_organizer.analyzer import ContentAnalyzer
from file_organizer.organizer import Organizer
from file_organizer.sync_engine import SyncEngine
from file_organizer.softlink_handler import SoftlinkHandler
from file_organizer.auto_git import AutoGit
from file_organizer.dedup import DedupEngine
from file_organizer.organizer import _TYPE_MAP

logger = logging.getLogger("file_organizer")

def setup_logging(verbose: bool = False, config_path: str = "config.yaml"):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(resolve_log_path(config_path)),
        ],
    )

def auto_pull_enabled(config: Config) -> bool:
    """Should a production auto cycle attempt the ADB phone pull?

    True unless phone_pull.auto_pull is explicitly false in config.yaml.
    The pull itself is best-effort — phone_pull.sh exits quietly when no
    device is connected.
    """
    pp = config.raw_config.get("phone_pull") or {}
    return bool(pp.get("auto_pull", True))


def run_auto_phone_pull(config: Config) -> bool:
    """Best-effort ADB phone pull for production auto cycles.

    Runs src/phone_pull.sh in quiet pull mode. Returns True when a pull
    actually ran, False when it was skipped or nothing was connected.
    Never raises — a broken adb setup must not break the organize cycle.
    """
    if not auto_pull_enabled(config):
        return False
    script = Path(__file__).resolve().parent.parent / "phone_pull.sh"
    if not script.exists():
        logger.warning("Auto phone pull: script not found (%s)", script)
        return False
    timeout = int(config.raw_config.get("sync_timeout_minutes", 60) or 60) * 60
    logger.info("Auto phone pull: checking for a connected phone...")
    try:
        result = subprocess.run(
            ["bash", str(script), "--quiet"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Auto phone pull: timed out after %ds", timeout)
        return False
    except OSError as e:
        logger.warning("Auto phone pull: could not run phone_pull.sh: %s", e)
        return False

    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if result.returncode == 0:
        if out:
            for line in out.splitlines():
                logger.info("Auto phone pull: %s", line)
        return True
    # No phone attached (or adb missing) is a normal state, not an error —
    # but a phone that CANNOT be used (USB debugging off / prompt not
    # accepted) must stand out so the user knows what to do.
    if err:
        for line in err.splitlines():
            lowered = line.lower()
            if "unauthorized" in lowered or "usb debugging" in lowered:
                logger.warning("Auto phone pull: %s", line)
            else:
                logger.info("Auto phone pull: %s", line)
    return False


def compute_idle_sleep(base: int, factor: int, max_multiplier: int, streak: int) -> int:
    """Sleep duration for a daemon cycle after `streak` idle cycles.

    Each idle cycle multiplies the base interval by `factor`, capped at
    base * max_multiplier. Any action resets the streak to 0 (item 14).
    Kept in sync with phone_sync.compute_idle_sleep.
    """
    if streak <= 0 or factor <= 1:
        return base
    multiplier = min(factor ** min(streak, 12), max_multiplier)
    return base * multiplier


def run_full_cycle(config: Config, dry_run: bool = False):
    """Run a full organization + sync + dedup cycle.

    Returns (actions, sync_thread):
      actions      — dict counting real changes (links, sync transfers,
                     dedup removals, git inits) for idle detection.
      sync_thread  — the background sync thread, or None if no sync ran.
    """
    actions = {
        "links_created": 0,
        "orphans_removed": 0,
        "sync_changes": 0,
        "dedup_removed": 0,
        "git_initialized": 0,
        "collapse_changes": 0,
    }
    sync_thread = None
    scanner = FileScanner(config)
    analyzer = ContentAnalyzer(config)
    organizer = Organizer(config)
    sync_engine = SyncEngine(config)
    softlink_handler = SoftlinkHandler(config)
    auto_git = AutoGit(config)
    dedup = DedupEngine(config)

    if dry_run:
        logger.info("=== DRY RUN MODE — no real files will be modified ===")
    else:
        logger.info("=== PRODUCTION MODE — changes will be applied ===")

    # 1. Scan — build allowed extensions from organize_file_types config
    allowed_types: list[str] = config.raw_config.get("organize_file_types", []) or []
    if allowed_types:
        allowed_extensions: set[str] = {
            ext for ext, ftype in _TYPE_MAP.items() if ftype in allowed_types
        }
        logger.info("Filtering to file types: %s (%d extensions)",
                     allowed_types, len(allowed_extensions))
    else:
        allowed_extensions = None  # include all

    logger.info("Scanning source folders...")
    scanned = list(scanner.scan_all_sources(allowed_extensions=allowed_extensions))
    logger.info("Scanned %d files.", len(scanned))

    # 2. Analyze
    if config.raw_config.get("enable_content_analysis", False):
        logger.info("Analyzing file contents...")
        file_keywords = []
        for sf in scanned:
            kws = analyzer.analyze_file(sf.path)
            if kws:
                file_keywords.append((sf.path, kws))
        freq = analyzer.build_category_frequencies(file_keywords)
        categories = analyzer.filter_meaningful_categories(freq, total_files=len(scanned))
        logger.info("Found %d meaningful categories.", len(categories))
    else:
        categories = []

    # 3. Organize
    logger.info("Building organized link tree...")
    # Build mapping of file -> assigned categories
    file_cat_map: dict = {}
    if categories:
        for fp, kws in file_keywords:
            matched = [cat for cat in categories if cat in kws]
            if matched:
                file_cat_map[fp] = matched
    # Convert scanned files to entries for organizer
    entries = [(sf.path, sf.mtime, file_cat_map.get(sf.path, [])) for sf in scanned]
    stats = organizer.organize(entries, dry_run=dry_run)
    logger.info('Links created: %s', stats)
    actions["links_created"] += stats.get("links_created", 0)
    valid_files = {sf.path for sf in scanned}
    if not dry_run:
        actions["orphans_removed"] += organizer.clean_orphaned_links(valid_files)

    # 4. Auto phone pull — if a phone is connected, pull its
    # file_organizer_staging files onto this computer BEFORE the folder
    # sync starts, so the pulled files ride along to the cloud drive in
    # the same cycle. Best-effort: no device = one quiet log line.
    if not dry_run:
        pulled = run_auto_phone_pull(config)
        if pulled:
            logger.info("Auto phone pull: pulled phone files into staging")

    # 5. Sync — run in background thread so slow ProtonDrive/external
    # drives don't block the fast scan + organize work
    if config.raw_config.get("enable_folder_sync", False) and not dry_run:
        logger.info("Starting background folder sync...")
        import threading
        def background_sync():
            try:
                summary = sync_engine.run_all_sync_pairs()
                actions["sync_changes"] = sync_engine.last_change_count
                logger.info("Background sync complete: %s", summary)
            except Exception as e:
                logger.error("Background sync failed: %s", e)
        sync_thread = threading.Thread(target=background_sync, daemon=True)
        sync_thread.start()

    # 6. Softlink handling (exclude patterns for rsync)
    exclude_for_rsync = softlink_handler.get_rsync_exclude_patterns()
    if exclude_for_rsync:
        logger.info("Excluding from sync: %s", exclude_for_rsync[:10])

    # 7. Auto-git (only in production mode — this makes real changes)
    if auto_git.enabled and not dry_run:
        logger.info("Auto-git scan...")
        git_summary = auto_git.scan_and_init()
        logger.info("Auto-git: %s", git_summary)
        actions["git_initialized"] += git_summary.get("initialized", 0)

    # 8. Dedup
    if config.raw_config.get("enable_duplicate_detection", False):
        logger.info("Running deduplication...")
        dedup_summary = dedup.run(dry_run=dry_run)
        logger.info("Dedup: %s", dedup_summary)
        actions["dedup_removed"] += dedup_summary.get("removed", 0)

    # 8. (N)-rename-family collapse (item 10). Deleting siblings is
    # destructive, so it runs only when the config flag is on AND we are
    # not in dry-run. Use `--collapse-renames` for a dry-run preview.
    if config.raw_config.get("collapse_rename_families", False):
        logger.info("Collapsing (N)-rename families...")
        collapse_stats = _run_collapse(config, dry_run=dry_run)
        logger.info("Collapse: %s", collapse_stats)
        actions["collapse_changes"] += (
            collapse_stats.get("renamed", 0) + collapse_stats.get("deleted", 0)
        )

    # 9. Download-copy dedupe: delete foo_1 / foo (1) / foo-1 / foo (1) (1)
    # copies when a plain-named file with IDENTICAL content exists. Runs
    # in every auto cycle (previews in dry-run); only identical bytes are
    # ever deleted, so nothing real is lost.
    if config.raw_config.get("dedupe_download_copies", True):
        logger.info("Deduping download copies...")
        dedupe_stats = _run_download_copy_dedupe(config, dry_run=dry_run)
        logger.info("Download-copy dedupe: %s", dedupe_stats)
        actions["dedup_removed"] += dedupe_stats.get("deleted", 0)

    logger.info("=== Cycle complete ===")
    return actions, sync_thread


def _collapse_folders(config: Config):
    """Folders to scan for (N)-rename families: organize_folders, falling
    back to source_folders."""
    return (
        config.raw_config.get("organize_folders")
        or config.raw_config.get("source_folders")
        or []
    )


def _run_collapse(config: Config, dry_run: bool = True) -> dict:
    """Plan (and optionally apply) the duplicate collapse across the
    configured folders: (N)-rename families plus identical-content '_N'
    duplicates. Returns stats {"renamed", "deleted"}."""
    from file_organizer.rename_family import collapse_in_tree, apply_collapse
    actions = []
    for folder in _collapse_folders(config):
        root = Path(folder)
        if not root.exists():
            logger.warning("Collapse: skipping missing folder %s", root)
            continue
        # keep_differing: siblings whose content differs from the winner
        # are NOT deleted — numbered web-archive assets are different
        # files, not duplicates.
        actions.extend(collapse_in_tree(root, keep_differing=True))
    return apply_collapse(actions, dry_run=dry_run)


def _run_download_copy_dedupe(config: Config, dry_run: bool = True) -> dict:
    """Delete redundant download copies (foo_1, foo (1), foo-1,
    foo (1) (1)) across the configured folders when a plain-named file
    with identical content exists. Returns stats {"renamed", "deleted"}."""
    from file_organizer.rename_family import (
        download_copies_in_tree, apply_collapse,
    )
    actions = []
    for folder in _dedupe_folders(config):
        root = Path(folder)
        if not root.exists():
            logger.warning(
                "Download-copy dedupe: skipping missing folder %s", root
            )
            continue
        actions.extend(download_copies_in_tree(root))
    return apply_collapse(actions, dry_run=dry_run)


def _dedupe_folders(config: Config):
    """Folders to scan for redundant download copies: organize_folders AND
    source_folders — copies pile up in both (e.g. MAIN_DRIVE/misc and
    MAIN_DRIVE/pol are sources, not organize targets)."""
    folders = []
    seen = set()
    for f in (config.raw_config.get("organize_folders") or []) + \
            (config.raw_config.get("source_folders") or []):
        if f not in seen:
            seen.add(f)
            folders.append(f)
    return folders

def normalize_flag_token(token, option_strings):
    """One CLI flag token → its canonical spelling.

    Case-insensitive and dash-optional: real, -real, Real, REAL, --real
    and -REAL all resolve to --REAL. Tokens that match no known option
    pass through untouched (they are values, e.g. a file name after
    --config).
    """
    name = token.lstrip("-").lower()
    for canon in option_strings:
        if canon.lstrip("-").lower() == name:
            return canon
    return token


def normalize_argv(argv, option_strings):
    """Canonicalize every flag token in argv (values untouched)."""
    return [normalize_flag_token(a, option_strings) for a in argv]


def main(argv=None):
    parser = argparse.ArgumentParser(description="File Organizer v2")
    parser.add_argument("-R", "--REAL", action="store_true", help="Production mode (default: test/dry-run)")
    parser.add_argument("--scan-once", action="store_true", help="Run one cycle then exit")
    parser.add_argument("--sync-only", action="store_true", help="Only sync folders")
    parser.add_argument("--dedupe-only", action="store_true", help="Only run deduplication")
    parser.add_argument("--git-preview", action="store_true", help="Preview which folders would get git init (no changes)")
    parser.add_argument("--git-init", action="store_true", help="Run auto-git: init, .gitignore, add, commit")
    parser.add_argument("--cleanup", action="store_true", help="Remove broken and stale symlinks from the organized folder")
    parser.add_argument("--collapse-renames", action="store_true",
                        help="Collapse 'foo (1).png' families and delete "
                             "identical '_N' duplicates — shows a plan, "
                             "applies only with --REAL")
    parser.add_argument("--create-test", action="store_true", help="Create test environment")
    parser.add_argument("--config", type=str, default="config.yaml", help="Config file path")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    # Flags are case-insensitive and dashes are optional (real/-real/
    # -REAL/--REAL all work); normalize before argparse sees them.
    option_strings = [
        opt for action in parser._actions for opt in action.option_strings
    ]
    if argv is not None:
        argv = normalize_argv(list(argv), option_strings)
    args = parser.parse_args(argv)

    # Running `python -m file_organizer` from src/ should still find the
    # live config.yaml at the project root (next to organize).
    if args.config == "config.yaml" and not os.path.exists("config.yaml"):
        root_cfg = find_project_root() / "config.yaml"
        if root_cfg.exists():
            args.config = str(root_cfg)

    setup_logging(args.verbose, args.config)
    real_mode = args.REAL

    # Load config
    config_path = Path(args.config)
    if not config_path.exists():
        logger.info("No config.yaml found. Creating starter config...")
        Config.create_starter(config_path)
        logger.info("Created %s — please edit it and run again.", config_path)
        # First-run UX (item 9): tell the user whether the template paths
        # will work and exactly how to finish editing.
        report_first_run(str(config_path))
        # Item 15: create the organized folder up front so the user can see
        # where the soft links will live.
        default_out = find_project_root() / "organized"
        default_out.mkdir(parents=True, exist_ok=True)
        logger.info("Organized folder (where soft links will go): %s", default_out)
        return

    try:
        config = Config(config_path)
    except ConfigError as e:
        print(
            f"\nERROR: {e}\n"
            "\nOpen your config file in TextEdit (Mac), Notepad (Windows) "
            "or nano (Termux), fix the problem above, then run again.\n",
            file=sys.stderr,
        )
        sys.exit(1)
    logger.info("Loaded config from %s", config_path)

    # Item 15: the organized folder (replacement for ~/organized) lives
    # inside the file_organizer folder by default. Create it up front so
    # it is always visible, even in dry-run mode.
    out_dir = ensure_output_dir(config)
    logger.info("Organized folder: %s", out_dir)

    if args.create_test:
        from file_organizer.test_env import create_test_environment
        create_test_environment(config)
        return

    if args.collapse_renames:
        # Item 10: plan first, apply only with --REAL.
        from file_organizer.rename_family import (
            collapse_in_tree, format_actions, apply_collapse,
        )
        actions = []
        for folder in _collapse_folders(config):
            root = Path(folder)
            if not root.exists():
                logger.warning("Collapse: skipping missing folder %s", root)
                continue
            actions.extend(collapse_in_tree(root))
        print("=== Rename-family collapse plan ===")
        print(format_actions(actions))
        if real_mode:
            stats = apply_collapse(actions, dry_run=False)
            print(f"Applied: {stats}")
        else:
            print("No changes made. Run with --REAL to apply.")
        return

    if args.sync_only and real_mode:
        sync_engine = SyncEngine(config)
        sync_engine.run_all_sync_pairs()
        return

    if args.dedupe_only and real_mode:
        dedup = DedupEngine(config)
        dedup.run(dry_run=False)
        return

    if args.cleanup and real_mode:
        organizer = Organizer(config)
        output_base = organizer.output_base
        removed = 0
        for link_path in output_base.rglob("*"):
            if not link_path.is_symlink():
                continue
            try:
                target = link_path.resolve()
            except OSError:
                # Broken symlink
                link_path.unlink(missing_ok=True)
                logger.info("Removed broken symlink: %s", link_path)
                removed += 1
                continue
            if not target.exists():
                link_path.unlink(missing_ok=True)
                logger.info("Removed stale symlink (target gone): %s -> %s", link_path, target)
                removed += 1
        # Remove empty directories
        for dirpath in sorted(output_base.rglob("*"), key=lambda p: -len(str(p))):
            if dirpath.is_dir() and not any(dirpath.iterdir()):
                try:
                    dirpath.rmdir()
                    logger.info("Removed empty directory: %s", dirpath)
                except OSError:
                    pass
        logger.info("Cleanup complete: removed %d symlinks.", removed)
        return

    if args.git_preview:
        import json
        auto_git = AutoGit(config)
        result = auto_git.preview()
        if not result["enabled"]:
            print(result["message"])
        else:
            print(f"Auto-git is enabled. Scanning: {', '.join(result['auto_git_folders'])}")
            print(f"Folders that would get 'git init': {result['candidate_count']}")
            print()
            for c in result["candidates"]:
                print(f"  {c['path']}")
                print(f"    Reason: {c['reason']}")
            if result["candidate_count"] == 0:
                print("  (none — all folders already have .git or don't qualify)")
            print()
            print("No changes were made. Run with --git-init to apply.")
        return

    if args.git_init:
        auto_git = AutoGit(config)
        if not auto_git.enabled:
            print("Auto-git is disabled in config. Nothing to do.")
        else:
            print(f"Running auto-git on: {', '.join(auto_git._auto_git_folders)}")
            summary = auto_git.scan_and_init()
            print(f"Scanned: {summary['scanned']}, Initialized: {summary['initialized']}, Skipped: {summary['skipped']}")
            print("Check %s for details." % resolve_log_path(args.config))
        return

    # Daemon mode
    if real_mode and not args.scan_once:
        logger.info("Entering daemon mode (Ctrl+C to stop)...")
        interval = config.raw_config.get("scan_interval", 3600)
        backoff_factor = int(config.raw_config.get("idle_backoff_factor", 2) or 2)
        backoff_max = int(
            config.raw_config.get("idle_backoff_max_multiplier", 8) or 8
        )
        idle_streak = 0
        try:
            while True:
                # Run the cycle, then wait for the background sync so two
                # rsyncs never overlap (cloud drives dislike that) and the
                # action counts are complete.
                actions, sync_thread = run_full_cycle(config, dry_run=False)
                if sync_thread is not None:
                    sync_thread.join(timeout=max(interval, 60))
                total = sum(actions.values())
                if total == 0:
                    idle_streak += 1
                else:
                    idle_streak = 0
                sleep_for = compute_idle_sleep(
                    interval, backoff_factor, backoff_max, idle_streak
                )
                logger.info(
                    "Cycle actions: %s. Sleeping %ds (idle streak %d).",
                    actions, sleep_for, idle_streak,
                )
                time.sleep(sleep_for)
        except KeyboardInterrupt:
            logger.info("Daemon stopped.")
        return

    # One-shot cycle
    try:
        _, sync_thread = run_full_cycle(config, dry_run=not real_mode)
        # The folder sync runs in a background daemon thread; a one-shot
        # run must wait for it or the process exit kills the rsyncs
        # mid-transfer (cloud drives dislike that, and the sync would
        # silently never finish).
        if sync_thread is not None:
            sync_thread.join()
    except KeyboardInterrupt:
        logger.info("Interrupted by user. Exiting.")
        return

if __name__ == "__main__":
    main()

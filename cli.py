#!/usr/bin/env python3
"""CLI entry point for file_organizer."""

import os, sys, argparse, logging, signal, time
from pathlib import Path

from file_organizer.config import Config
from file_organizer.scanner import FileScanner
from file_organizer.analyzer import ContentAnalyzer
from file_organizer.organizer import Organizer
from file_organizer.sync_engine import SyncEngine
from file_organizer.softlink_handler import SoftlinkHandler
from file_organizer.auto_git import AutoGit
from file_organizer.dedup import DedupEngine
from file_organizer.organizer import _TYPE_MAP

logger = logging.getLogger("file_organizer")

def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(os.path.expanduser("~/.file_organizer.log")),
        ],
    )

def run_full_cycle(config: Config, dry_run: bool = False):
    """Run a full organization + sync + dedup cycle."""
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
    valid_files = {sf.path for sf in scanned}
    if not dry_run:
        organizer.clean_orphaned_links(valid_files)

    # 4. Sync — run in background thread so slow ProtonDrive/external
    # drives don't block the fast scan + organize work
    if config.raw_config.get("enable_folder_sync", False) and not dry_run:
        logger.info("Starting background folder sync...")
        import threading
        def background_sync():
            try:
                summary = sync_engine.run_all_sync_pairs()
                logger.info("Background sync complete: %s", summary)
            except Exception as e:
                logger.error("Background sync failed: %s", e)
        t = threading.Thread(target=background_sync, daemon=True)
        t.start()

    # 5. Softlink handling (exclude patterns for rsync)
    exclude_for_rsync = softlink_handler.get_rsync_exclude_patterns()
    if exclude_for_rsync:
        logger.info("Excluding from sync: %s", exclude_for_rsync[:10])

    # 6. Auto-git (only in production mode — this makes real changes)
    if auto_git.enabled and not dry_run:
        logger.info("Auto-git scan...")
        git_summary = auto_git.scan_and_init()
        logger.info("Auto-git: %s", git_summary)

    # 7. Dedup
    if config.raw_config.get("enable_duplicate_detection", False):
        logger.info("Running deduplication...")
        dedup_summary = dedup.run(dry_run=dry_run)
        logger.info("Dedup: %s", dedup_summary)

    logger.info("=== Cycle complete ===")

def main():
    parser = argparse.ArgumentParser(description="File Organizer v2")
    parser.add_argument("-R", "--REAL", action="store_true", help="Production mode (default: test/dry-run)")
    parser.add_argument("--scan-once", action="store_true", help="Run one cycle then exit")
    parser.add_argument("--sync-only", action="store_true", help="Only sync folders")
    parser.add_argument("--dedupe-only", action="store_true", help="Only run deduplication")
    parser.add_argument("--git-preview", action="store_true", help="Preview which folders would get git init (no changes)")
    parser.add_argument("--git-init", action="store_true", help="Run auto-git: init, .gitignore, add, commit")
    parser.add_argument("--cleanup", action="store_true", help="Remove broken and stale symlinks from ~/organized")
    parser.add_argument("--create-test", action="store_true", help="Create test environment")
    parser.add_argument("--config", type=str, default="config.yaml", help="Config file path")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    setup_logging(args.verbose)
    real_mode = args.REAL

    # Load config
    config_path = Path(args.config)
    if not config_path.exists():
        logger.info("No config.yaml found. Creating starter config...")
        Config.create_starter(config_path)
        logger.info("Created %s — please edit it and run again.", config_path)
        return

    config = Config(config_path)
    logger.info("Loaded config from %s", config_path)

    if args.create_test:
        from file_organizer.test_env import create_test_environment
        create_test_environment(config)
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
            print("Check ~/.file_organizer.log for details.")
        return

    # Full cycle
    try:
        run_full_cycle(config, dry_run=not real_mode)
    except KeyboardInterrupt:
        logger.info("Interrupted by user. Exiting.")
        return

    # Daemon mode
    if real_mode and not args.scan_once:
        logger.info("Entering daemon mode (Ctrl+C to stop)...")
        interval = config.raw_config.get("scan_interval", 3600)
        try:
            while True:
                time.sleep(interval)
                run_full_cycle(config, dry_run=False)
        except KeyboardInterrupt:
            logger.info("Daemon stopped.")

if __name__ == "__main__":
    main()

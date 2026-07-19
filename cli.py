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

    # 1. Scan
    logger.info("Scanning source folders...")
    scanned = list(scanner.scan_all_sources())
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
        categories = analyzer.filter_meaningful_categories(freq)
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
    organizer.organize(entries, dry_run=dry_run)
    organizer.clean_orphaned_links(set())

    # 4. Sync
    if config.raw_config.get("enable_folder_sync", False) and not dry_run:
        logger.info("Running folder sync...")
        summary = sync_engine.run_all_sync_pairs()
        logger.info("Sync complete: %s", summary)

    # 5. Softlink handling
    logger.info("Processing softlink folders...")
    exclude_for_rsync = softlink_handler.get_rsync_exclude_patterns()
    logger.info("Excluding from sync: %s", exclude_for_rsync[:10])

    # 6. Auto-git
    if auto_git.enabled:
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

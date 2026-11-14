#!/usr/bin/env python3
"""Shared helpers for the end-to-end test-plan scripts (backup, restore,
verify). Plain stdlib only, so the scripts run unchanged on macOS, Windows,
Linux and Termux.

The scripts derive everything from config.yaml (drives, sync_pairs,
one_way_pairs, router, phone_pull, output_base) — nothing is hard-coded to
one person's machine.
"""

import os
import tempfile
from pathlib import Path

HOME = Path.home()
BACKUP_ROOT = Path(tempfile.gettempdir()) / "major-folders-backup"
MANIFEST_PATH = BACKUP_ROOT / "manifest.json"

# Test-file names the plan relies on (created by backup-major-folders.py,
# checked by verify-test-results.py).
SIDE_A_FILE = "20261111_side_a_test.txt"
SIDE_B_FILE = "20261111_side_b_test.txt"
PROJECT = "sync_test_project"
PROJECT_FILES = ["a.py", "b.py"]


def repo_root() -> Path:
    """The file_organizer folder containing organize and config.yaml."""
    return Path(__file__).resolve().parent.parent.parent


def default_config_path() -> Path:
    return repo_root() / "config.yaml"


def real(path) -> str:
    """Cross-platform comparison key: normcased realpath (handles macOS
    /tmp -> /private/tmp and Windows case differences)."""
    return os.path.normcase(os.path.realpath(str(path)))


def is_under(path: Path, base: Path) -> bool:
    """True when `path` is `base` or inside it (cross-platform)."""
    base_real = real(base)
    path_real = real(path)
    return path_real == base_real or path_real.startswith(base_real + os.sep)


def parse_config(path: Path) -> dict:
    """Minimal de-dented-YAML reader: drives:, phone_pull:, router:,
    sync_pairs:, one_way_pairs: and top-level scalar keys.

    Raises ValueError when the file cannot be read.
    """
    cfg = {"drives": {}, "phone_pull": {}, "top": {}, "router": {}}
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read config {path}: {exc}") from exc
    section = None
    subsection = None
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        # List items without a colon: router watch_folders entries and the
        # folder paths inside sync_pairs / one_way_pairs.
        if (
            section == "router"
            and indent == 4
            and subsection == "watch_folders"
            and stripped.startswith("-")
        ):
            cfg["router"]["watch_folders"].append(
                stripped.lstrip("-").strip()
            )
            continue
        if (
            section in ("sync_pairs", "one_way_pairs")
            and subsection == "folders"
            and stripped.startswith("-")
            and ":" not in stripped
        ):
            cfg[section][-1].append(stripped.lstrip("-").strip())
            continue
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        val = val.strip().strip("'\"").strip()
        if indent == 0:
            cfg["top"][key] = val
            section = key
            subsection = None
        elif section in ("drives", "phone_pull") and indent == 2:
            cfg[section][key] = val
        elif section == "router":
            if indent == 2 and val:
                cfg["router"][key] = val
            elif indent == 2 and not val:
                subsection = key
                if key == "watch_folders":
                    cfg["router"].setdefault("watch_folders", [])
                elif key == "target_folders":
                    cfg["router"].setdefault("target_folders", {})
            elif indent == 4 and subsection == "target_folders":
                sub_key, _, sub_val = stripped.partition(":")
                if sub_val.strip():
                    cfg["router"]["target_folders"][sub_key.strip()] = \
                        sub_val.strip().strip("'\"")
        elif section in ("sync_pairs", "one_way_pairs"):
            if stripped.startswith("-") and not val:
                # "- folders:" starts a new pair
                cfg.setdefault(section, []).append([])
                subsection = "folders"
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


def count_files(folder: Path) -> int:
    """Number of files under a folder (metadata-only walk)."""
    n = 0
    for _dirpath, _dirnames, filenames in os.walk(folder):
        n += len(filenames)
    return n


def pair_candidates(cfg: dict):
    """(sync_candidates, one_way_candidates).

    Each candidate is {"index": 0-based position in config.yaml's list,
    "folders": [Path, Path], "count": file count used for the default
    pick}. sync pairs need two existing, distinct folders. one-way pairs
    need an existing source and a LOCAL target (never sweep a cloud or
    external target).
    """
    main = Path(cfg["drives"].get("MAIN_DRIVE", str(HOME)))
    sync_cands = []
    for i, pair in enumerate(cfg.get("sync_pairs", [])):
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        a, b = resolve(pair[0], cfg), resolve(pair[1], cfg)
        if a.is_dir() and b.is_dir() and real(a) != real(b):
            sync_cands.append({
                "index": i,
                "folders": [a, b],
                "count": count_files(a) + count_files(b),
            })
    ow_cands = []
    for i, pair in enumerate(cfg.get("one_way_pairs", [])):
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        src, dst = resolve(pair[0], cfg), resolve(pair[1], cfg)
        if not src.is_dir():
            continue
        if not (is_under(dst, HOME) or is_under(dst, main)):
            continue  # never sweep a cloud/external target
        if real(src) == real(dst):
            continue
        ow_cands.append({
            "index": i,
            "folders": [src, dst],
            "count": count_files(src),
        })
    return sync_cands, ow_cands


def select_pairs(cfg: dict, sync_index=None, one_way_index=None):
    """Pick the test pairs. sync_index / one_way_index are 0-based indices
    into config.yaml's sync_pairs / one_way_pairs arrays (None = smallest
    eligible pair). Returns (sync_cand or None, ow_cand or None, messages).
    """
    sync_cands, ow_cands = pair_candidates(cfg)
    messages = []
    chosen_sync = chosen_ow = None
    if sync_index is not None:
        chosen_sync = next(
            (c for c in sync_cands if c["index"] == sync_index), None
        )
        if chosen_sync is None:
            messages.append(
                f"sync_pairs[{sync_index}] is not eligible — both folders "
                "must exist and differ"
            )
    else:
        if sync_cands:
            chosen_sync = min(sync_cands, key=lambda c: c["count"])
        else:
            messages.append(
                "no eligible sync_pair found (need 2 existing folders)"
            )
    if one_way_index is not None:
        chosen_ow = next(
            (c for c in ow_cands if c["index"] == one_way_index), None
        )
        if chosen_ow is None:
            messages.append(
                f"one_way_pairs[{one_way_index}] is not eligible — the "
                "source must exist and the target must be a local folder"
            )
    else:
        if ow_cands:
            chosen_ow = min(ow_cands, key=lambda c: c["count"])
        else:
            messages.append(
                "no eligible one_way_pair with a local target — the "
                "one-way part of the plan is skipped"
            )
    return chosen_sync, chosen_ow, messages


def format_candidates(cfg: dict) -> str:
    """Human-readable pair listing (1-based numbers) for --list."""
    sync_cands, ow_cands = pair_candidates(cfg)
    lines = ["sync_pairs (both folders exist):"]
    if not sync_cands:
        lines.append("  (none eligible)")
    for c in sync_cands:
        a, b = c["folders"]
        lines.append(f"  {c['index'] + 1}. {a}  <->  {b}   ({c['count']} files)")
    lines.append("one_way_pairs (source exists, target is local):")
    if not ow_cands:
        lines.append("  (none eligible)")
    for c in ow_cands:
        src, dst = c["folders"]
        lines.append(f"  {c['index'] + 1}. {src}  ->  {dst}   ({c['count']} files)")
    return "\n".join(lines)


def read_manifest():
    """The backup manifest as a dict, or None when it does not exist or
    cannot be read. The manifest is the contract between the backup
    script and the verifier: it records which pairs were swept, so the
    verifier checks exactly those folders even though folder sizes have
    changed by then."""
    if not MANIFEST_PATH.exists():
        return None
    try:
        import json
        return json.loads(MANIFEST_PATH.read_text())
    except (OSError, ValueError):
        return None

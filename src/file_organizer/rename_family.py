"""Item 10: no `_1`/`(N)` renames.

Two rules live here:

1. **Copy-overwrite rule** — copying `foo.png` over an existing `foo.png`
   overwrites it in place when the incoming file is NEWER and a DIFFERENT
   SIZE. No `foo_1.png` renames are ever created by our copy paths.

2. **(N)-family collapse** — Mac/phone rename families like `foo (1).png`,
   `foo (2).png`, `foo (1) (1).png`, `foo (1) (2).png` or even
   `foo (1) (1)_1.png` are collapsed to the base name: the NEWEST member
   becomes `foo.png` and every other member is deleted.

This module is pure planning + applying — callers decide when it runs and
always show a dry-run plan before deleting anything.
"""

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

# Matches a name with at least one (N) group and an optional trailing _N,
# e.g. "foo (1).png", "foo (1) (2).png", "foo (1) (1)_1.png".
# The (N) groups are limited to 1-3 digits so real names with year suffixes
# like "report (2026).txt" are NOT treated as rename families.
# A plain "foo_1.png" (underscore without a (N) group) is also NOT a family
# member — item 10 says we must not rename plain _1 files.
_FAMILY_RE = re.compile(
    r"^(?P<base>.*?)\s*(?P<groups>(?:\(\d{1,3}\)\s*)+)"
    r"(?P<underscore>_\d+)?(?P<ext>\.[^.]+)?$"
)


@dataclass
class FamilyMatch:
    original: str
    base_name: str   # e.g. "foo.png"
    base_stem: str   # e.g. "foo"
    ext: str         # e.g. ".png" ("" when there is no extension)


def parse_family(name: str) -> Optional[FamilyMatch]:
    """Return the family base for names like 'foo (1).png' or None if the
    name is not a (N)-rename member."""
    if not isinstance(name, str) or not name:
        return None
    m = _FAMILY_RE.match(name)
    if not m:
        return None
    base = m.group("base").rstrip()
    if not base:
        return None
    ext = m.group("ext") or ""
    return FamilyMatch(original=name, base_name=base + ext, base_stem=base, ext=ext)


@dataclass
class CollapseAction:
    kind: str                        # "rename" or "delete"
    path: Path
    target: Optional[Path] = None    # set for "rename"


def plan_collapse(directory, min_age_seconds: int = 0) -> List[CollapseAction]:
    """Plan the collapse of (N)-rename families inside one directory.

    The directory is scanned non-recursively. For every family the NEWEST
    member (by mtime, ties broken by larger size) becomes the base name —
    replacing the base file if it exists — and all other members are
    deleted. A single member with no base file is left alone (it may be a
    real name, e.g. a year suffix). Families whose newest file is younger
    than min_age_seconds are skipped entirely (grace period).

    Returns a list of actions; nothing is touched on disk.
    """
    directory = Path(directory)
    actions: List[CollapseAction] = []
    if not directory.is_dir():
        return actions

    groups: dict = {}
    try:
        entries = list(directory.iterdir())
    except OSError:
        return actions

    for p in entries:
        if not p.is_file():
            continue
        fm = parse_family(p.name)
        if fm:
            groups.setdefault(fm.base_name, []).append(p)

    now = time.time()
    for base_name, members in groups.items():
        base = directory / base_name
        candidates = list(members)
        if base.exists() and base.is_file():
            candidates.append(base)
        if len(candidates) < 2:
            continue  # single member and no base — leave alone

        def sort_key(p):
            try:
                st = p.stat()
                return (st.st_mtime, st.st_size)
            except OSError:
                return (0, 0)

        candidates.sort(key=sort_key)
        newest = candidates[-1]

        if min_age_seconds > 0:
            try:
                age = now - newest.stat().st_mtime
            except OSError:
                age = float("inf")
            if age < min_age_seconds:
                logger.debug(
                    "Family %s skipped: newest file younger than %ds",
                    base_name, min_age_seconds,
                )
                continue

        if newest == base:
            for m in members:
                actions.append(CollapseAction("delete", m))
        else:
            actions.append(CollapseAction("rename", newest, base))
            for m in members:
                if m != newest:
                    actions.append(CollapseAction("delete", m))

    return actions


def collapse_in_tree(root, min_age_seconds: int = 0) -> List[CollapseAction]:
    """Plan collapse actions for every directory under root (recursive)."""
    root = Path(root)
    actions: List[CollapseAction] = []
    if not root.is_dir():
        return actions
    for dirpath, _dirnames, _filenames in os.walk(root):
        actions.extend(plan_collapse(dirpath, min_age_seconds=min_age_seconds))
    return actions


def format_actions(actions: List[CollapseAction]) -> str:
    """Human-readable plan for printing."""
    if not actions:
        return "Nothing to collapse."
    lines = []
    for a in actions:
        if a.kind == "rename":
            lines.append(f"  RENAME {a.path} → {a.target.name}")
        else:
            lines.append(f"  DELETE {a.path}")
    return "\n".join(lines)


def apply_collapse(actions: List[CollapseAction], dry_run: bool = False) -> dict:
    """Apply a collapse plan. Returns stats {"renamed", "deleted"}."""
    stats = {"renamed": 0, "deleted": 0}
    for a in actions:
        if dry_run:
            if a.kind == "rename":
                logger.info("[DRY-RUN] Would rename %s → %s", a.path, a.target.name)
            else:
                logger.info("[DRY-RUN] Would delete %s", a.path)
            continue
        try:
            if a.kind == "rename":
                if not a.target:
                    continue
                a.target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(a.path, a.target)
                logger.info("Renamed %s → %s", a.path, a.target.name)
                stats["renamed"] += 1
            elif a.kind == "delete":
                a.path.unlink(missing_ok=True)
                logger.info("Deleted %s", a.path)
                stats["deleted"] += 1
        except OSError as e:
            logger.error("Collapse action failed for %s: %s", a.path, e)
    return stats

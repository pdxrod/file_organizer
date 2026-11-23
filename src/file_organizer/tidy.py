"""tidy.py — the generic folder tidy used by './organize tidy'.

Applies the same rules to every configured root folder:
    1. merge sibling folders whose names differ only by spacing or
       leading numbers, at EVERY depth ('3 Lee Morgan' + '3LeeMorgan'
       -> 'LeeMorgan'; 'Blood on the Tracks' + 'BloodOnTheTracks' ->
       'BloodOnTheTracks')
    2. merge nested folders named like their parent ('pac/2pac/' ->
       'pac/')
    3. delete duplicate files that are identical in content when their
       names differ only by spacing/punctuation ('Ain't Ya Coming Home
       Babe.mp3' + 'Ain'tYaComingHomeBabe.mp3' — only the second stays)
    4. flatten excessive nesting: music folders that hold only subfolders
       with few files, and any tiny nested folder (one subfolder, <=5 files)
    5. strip leading track/order numbers from file names (only when a
       readable name of 3+ letters remains — dates like 20231014_… stay)
    6. audio files: top-level files move into their mdls artist folder;
       'Unnamed Track' / download-style names are renamed from mdls
       title / artist / track metadata
    7. 'Album1'-style folders holding audio become 'Singles'
    8. remove empty folders (and folders holding only .DS_Store)

Protected everywhere: dot-folders, Photos libraries ('Photo Booth
Library', *.photoslibrary), year folders (NNNN), and anything under a
path listed in tidy.exclude_folders. Nothing changes unless apply=True;
collisions never overwrite (name_1.ext, name_2.ext, …).
"""

import hashlib
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from file_organizer.rename_family import next_free_name

logger = logging.getLogger(__name__)

AUDIO_EXTS = {
    ".mp3", ".m4a", ".m4p", ".flac", ".wav", ".aac", ".ogg", ".wma",
    ".aiff", ".aif", ".opus",
}

_UNNAMED = re.compile(r"unnamed[-_\s]*track", re.I)
_UGLY = re.compile(r"--|-mp3-|unnamed", re.I)
_ALBUM_GENERIC = re.compile(r"^album[\d\s]*$", re.I)
_LIME = re.compile(r"^limewire-[^-]+-+", re.I)
_TRAIL_MP3 = re.compile(r"\s*-mp3-\d+$", re.I)

# App-managed folders that must never be renamed or flattened.
_PROTECTED_NAMES = {
    "photo booth library",
    "iphoto library",
    "takeout",
    "wondershare video converter ultimate",
}

# Stems that are not a name — stripping a number from them would just
# produce another meaningless name (e.g. '1.html.erb' -> 'html').
_GENERIC_STEMS = {
    "html", "htm", "erb", "css", "js", "json", "xml", "csv", "index",
    "page", "home", "main", "app", "view", "views", "mass", "data",
    "file", "files", "doc", "docs", "test", "tests", "readme",
    "sitemap", "robots", "feed", "rss", "img", "image", "images",
    "photo", "photos", "pic", "pics", "thumb", "thumbnail", "banner",
    "logo", "icon", "icons", "header", "footer", "sidebar", "asset",
    "assets", "script", "scripts", "style", "styles", "config",
    "settings", "download", "downloads", "upload", "uploads",
    "unknown", "untitled",
}


def _protected(name: str) -> bool:
    low = name.lower()
    return (
        name.startswith(".")
        or low in _PROTECTED_NAMES
        or low.endswith(".photoslibrary")
        or low.endswith(".photolibrary")
        or low.endswith("_files")
        or re.fullmatch(r"\d{4}", name) is not None
    )


def _excluded(p: Path, excludes: list) -> bool:
    return any(e == p or e in p.parents for e in excludes)


def _in_protected(p: Path, root: Path) -> bool:
    try:
        parts = p.relative_to(root).parts[:-1]
    except ValueError:
        return False
    return any(_protected(part) for part in parts)


def _strip_leading_num(s: str) -> str:
    """'04 From A Buick 6' -> 'From A Buick 6'; '02SpiritOnTheWater' ->
    'SpiritOnTheWater'; '1-04 Moanin'' -> 'Moanin''. Only 1-2 digit
    order-number prefixes are stripped — longer numbers ('911Report',
    '800-everest…') and digits before lowercase letters ('2pac- …')
    are kept."""
    while True:
        new = re.sub(r"^\d{1,2}(?=[-_.\s]|[A-Z])[-_.\s]*", "", s, count=1)
        if new == s:
            return s
        s = new


def camel(name: str) -> str:
    """'3 Lee Morgan' -> 'LeeMorgan'; '2KennyBurrell' -> 'KennyBurrell'."""
    base = re.sub(r"^\d{1,2}[-_.\s]*", "", name.strip())
    base = re.sub(r"\s+", "", base)
    return base.strip("-_. ") or name


def _clean_download_name(stem: str) -> str:
    """Decode download-style names with no usable metadata:
    'Limewire-2pac-----Thugs--Mansion-mp3-9049' -> 'Thugs Mansion'
    'Bob--Dylan-Bob--Dylan-----A--Hard--Rains...-mp3-21306'
        -> 'A Hard Rains A Gonna Fall'
    '2pac-  I Ain't Mad At Cha' -> 'I Ain't Mad At Cha'
    """
    s = _TRAIL_MP3.sub("", stem)
    if s.lower().startswith("limewire-"):
        # 'Limewire-<artist>-----<the rest>'
        m = re.match(r"^[^-]+-([^-]+)-{3,}(.*)$", s)
        if m:
            s = m.group(2)
        else:
            s = re.sub(r"^limewire-[^-]+-+", "", s)
    else:
        # 'Bob--Dylan-Bob--Dylan-----A--Hard…' -> 'A--Hard…'
        s = re.sub(r"^(\S+--\S+)-\1-{2,}", "", s, count=1)
        # '2pac-  I Ain't Mad At Cha' -> 'I Ain't Mad At Cha'
        s = re.sub(r"^[^-]+-\s+", "", s, count=1)
    s = re.sub(r"-{2,}", " ", s)
    s = re.sub(r"-", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -_")
    return s


def _mdls(path: Path) -> dict:
    try:
        out = subprocess.run(
            ["mdls", str(path)], capture_output=True, text=True, timeout=15,
        ).stdout
    except OSError:
        return {}
    res = {}
    lines = out.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^(\S+)\s*=\s*(.*)$", lines[i].strip())
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2).strip()
        if val == "(":
            items = []
            i += 1
            while i < len(lines) and lines[i].strip() != ")":
                item = lines[i].strip().strip('"').rstrip(",")
                if item:
                    items.append(item)
                i += 1
            res[key] = items
        else:
            res[key] = "" if val == "(null)" else val.strip('"')
        i += 1
    return res


def _move_file(src: Path, directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / name
    if dest.exists() and dest.is_file():
        src_hash = _sha256(src)
        if src_hash is not None and src_hash == _sha256(dest):
            # Identical content is already there: drop the incoming copy.
            try:
                src.unlink(missing_ok=True)
                logger.info("tidy: %s duplicate of %s — deleted", src, dest)
            except OSError as e:
                logger.error("tidy: unlink %s failed: %s", src, e)
            return dest
    if dest.exists():
        dest = directory / next_free_name(directory, name)
    try:
        os.replace(str(src), str(dest))
    except OSError as e:
        logger.error("tidy: %s -> %s failed: %s", src, dest, e)
        return src
    return dest


def _is_empty_dir(p: Path) -> bool:
    try:
        entries = list(p.iterdir())
    except OSError:
        return False
    return all(e.name == ".DS_Store" for e in entries)


def _sha256(path: Path):
    """Content hash (the first 64 MB is enough to spot real duplicates)."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
                if fh.tell() > (64 << 20):
                    break
        return h.hexdigest()
    except OSError:
        return None


def _file_key(name: str) -> str:
    """Case/punctuation/space-insensitive file identity:
    'Ain't Ya Coming Home Babe.mp3' == 'Ain'tYaComingHomeBabe.mp3'."""
    p = Path(name)
    return re.sub(r"[^a-z0-9]", "", p.stem.lower()) + p.suffix.lower()


def _merge_dir(src: Path, dst: Path) -> None:
    """Move everything from src into dst (recursively, collision-safe)."""
    for child in sorted(src.iterdir(), key=lambda p: p.name.lower()):
        if child.name == ".DS_Store":
            continue
        target = dst / child.name
        if child.is_dir():
            if not target.exists():
                try:
                    os.replace(str(child), str(target))
                except OSError as e:
                    logger.error("tidy: merge dir %s failed: %s", child, e)
                continue
            _merge_dir(child, target)
        else:
            _move_file(child, dst, child.name)
    try:
        shutil.rmtree(src, ignore_errors=True)
    except OSError as e:
        logger.error("tidy: could not remove merged folder %s: %s", src, e)


def _flatten(d: Path) -> None:
    files = sorted(
        (p for p in d.rglob("*") if p.is_file() and p.parent != d),
        key=lambda p: str(p),
    )
    for f in files:
        _move_file(f, d, f.name)
    for sub in sorted(
        (p for p in d.iterdir() if p.is_dir()), key=lambda p: str(p),
        reverse=True,
    ):
        shutil.rmtree(sub, ignore_errors=True)


def _has_audio(d: Path) -> bool:
    return any(
        f.suffix.lower() in AUDIO_EXTS
        for f in d.rglob("*") if f.is_file()
    )


def _merge_twins(d: Path, root: Path, excludes: list, apply: bool,
                 stats: dict) -> None:
    """Merge sibling folders whose names differ only by spacing or
    leading numbers, at any depth ('Blood on the Tracks' +
    'BloodOnTheTracks' -> 'BloodOnTheTracks')."""
    children = [
        c for c in d.iterdir()
        if c.is_dir() and not c.is_symlink()
        and not _protected(c.name) and not _excluded(c, excludes)
    ]
    groups = {}
    order = []
    for c in sorted(children, key=lambda p: p.name.lower()):
        key = camel(c.name).lower()
        groups.setdefault(key, []).append(c)
        if key not in order:
            order.append(key)
    for key in order:
        group = groups[key]
        if len(group) == 1 and camel(group[0].name).lower() \
                == group[0].name.lower():
            continue
        canonical = min(group, key=lambda c: (
            " " in c.name,
            c.name.lower() != camel(c.name).lower(),
            len(c.name),
            c.name.lower(),
        ))
        want = camel(canonical.name)
        target = d / want
        if canonical.name != want:
            if target.exists() and not target.is_dir():
                target = d / next_free_name(d, want)
            stats["renamed_dirs"].append(
                f"{canonical.relative_to(root)}/ -> {target.name}/")
            if apply:
                try:
                    canonical.rename(target)
                    canonical = target
                except OSError as e:
                    stats["errors"] += 1
                    logger.error("tidy: rename %s failed: %s",
                                 canonical, e)
                    continue
        for other in group:
            if other == canonical or not other.exists():
                continue
            stats["merged"].append(
                f"{other.relative_to(root)}/ -> {canonical.name}/")
            if apply:
                _merge_dir(other, canonical)


def _merge_children_up(d: Path, root: Path, excludes: list, apply: bool,
                       stats: dict) -> None:
    """Merge nested folders named like their parent ('pac/2pac/' ->
    'pac/'), at any depth."""
    for sub in [s for s in d.iterdir()
                if s.is_dir() and not s.is_symlink()
                and not _protected(s.name) and not _excluded(s, excludes)]:
        if camel(sub.name).lower() == camel(d.name).lower():
            stats["merged"].append(
                f"{sub.relative_to(root)}/ -> {d.name}/")
            if apply:
                _merge_dir(sub, d)


def _dedupe_files(d: Path, root: Path, excludes: list, apply: bool,
                  stats: dict) -> None:
    """Delete duplicate files in one folder: identical content and names
    that differ only by spacing / punctuation ('Ain't Ya Coming Home
    Babe.mp3' + 'Ain'tYaComingHomeBabe.mp3' — only the second stays).
    Files with different content are never deleted."""
    files = [
        f for f in d.iterdir()
        if f.is_file() and not f.is_symlink()
        and f.name != ".DS_Store" and not _excluded(f, excludes)
    ]
    groups = {}
    order = []
    for f in sorted(files, key=lambda p: p.name.lower()):
        key = _file_key(f.name)
        groups.setdefault(key, []).append(f)
        if key not in order:
            order.append(key)
    for key in order:
        group = groups[key]
        if len(group) < 2:
            continue
        survivor = min(group, key=lambda f: (
            " " in f.name,
            f.name.lower() != camel(f.stem).lower() + f.suffix.lower(),
            len(f.name),
            f.name.lower(),
        ))
        sh = _sha256(survivor)
        if sh is None:
            continue
        for other in group:
            if other == survivor or not other.exists():
                continue
            if _sha256(other) == sh:
                stats["dupes_deleted"].append(
                    f"{other.relative_to(root)} -> delete "
                    f"(same content as {survivor.name})")
                if apply:
                    try:
                        other.unlink(missing_ok=True)
                        logger.info("tidy: deleted duplicate %s "
                                    "(same as %s)", other, survivor)
                    except OSError as e:
                        stats["errors"] += 1
                        logger.error("tidy: unlink %s failed: %s",
                                     other, e)


def _tidy_root(root: Path, excludes: list, apply: bool, stats: dict) -> None:
    if _excluded(root, excludes):
        return

    # 1 — recursive twin-merge + duplicate-file cleanup, deepest first.
    all_dirs = sorted(
        [root] + [
            p for p in root.rglob("*")
            if p.is_dir() and not p.is_symlink()
        ],
        key=lambda p: len(p.relative_to(root).parts),
        reverse=True,
    )
    for d in all_dirs:
        if (not d.exists() or _protected(d.name)
                or _in_protected(d, root) or _excluded(d, excludes)):
            continue
        _merge_twins(d, root, excludes, apply, stats)
        _merge_children_up(d, root, excludes, apply, stats)
        _dedupe_files(d, root, excludes, apply, stats)

    # 2 — flatten excessive nesting.
    for d in sorted(
        (p for p in root.iterdir() if p.is_dir() and not p.is_symlink()),
        key=lambda p: p.name.lower(),
    ):
        if _protected(d.name) or _excluded(d, excludes):
            continue
        subs = [s for s in d.iterdir() if s.is_dir()]
        if not subs:
            continue
        direct = any(s.is_file() and s.name != ".DS_Store"
                     for s in d.iterdir())
        total = sum(1 for p in d.rglob("*") if p.is_file())
        if direct:
            continue
        if _has_audio(d):
            if len(subs) != 1 and total > 20:
                continue
        else:
            if len(subs) != 1 or total > 5:
                continue
        stats["flattened"].append(
            f"{d.relative_to(root)}/ (flatten {len(subs)} subfolder(s), "
            f"{total} file(s))")
        if apply:
            _flatten(d)

    # 2b — second dedupe wave: merges and flattening bring files from
    # different folders together; catch any remaining identical-content
    # pairs that landed next to each other after those moves.
    all_dirs2 = sorted(
        [root] + [
            p for p in root.rglob("*")
            if p.is_dir() and not p.is_symlink()
        ],
        key=lambda p: len(p.relative_to(root).parts),
        reverse=True,
    )
    for d in all_dirs2:
        if (not d.exists() or _protected(d.name)
                or _in_protected(d, root) or _excluded(d, excludes)):
            continue
        _dedupe_files(d, root, excludes, apply, stats)

    # 3 — per-file work: strip order numbers, mdls renames, artist moves.
    all_files = sorted(
        (p for p in root.rglob("*")
         if p.is_file() and not p.is_symlink() and p.name != ".DS_Store"),
        key=lambda p: str(p),
    )
    for f in all_files:
        if _excluded(f, excludes) or _in_protected(f, root):
            continue
        current = f
        ext = f.suffix.lower()

        # 4a — strip a leading order number when a readable name remains.
        stripped = _strip_leading_num(current.stem)
        if (stripped and stripped != current.stem
                and stripped.lower() not in _GENERIC_STEMS
                and re.search(r"[A-Za-z]{3,}", stripped)):
            new_name = stripped + current.suffix
            dest = current.with_name(new_name)
            if dest.exists() and dest.is_file():
                sh = _sha256(current)
                if sh is not None and sh == _sha256(dest):
                    stats["dupes_deleted"].append(
                        f"{current.relative_to(root)} -> delete "
                        f"(same content as {dest.name})")
                    if apply:
                        try:
                            current.unlink(missing_ok=True)
                            logger.info("tidy: deleted duplicate %s "
                                        "(same as %s)", current, dest)
                        except OSError as e:
                            stats["errors"] += 1
                            logger.error("tidy: unlink %s failed: %s",
                                         current, e)
                    continue
                dest = current.with_name(
                    next_free_name(current.parent, new_name))
            if not dest.exists():
                stats["file_renames"].append(
                    f"{current.relative_to(root)} -> {dest.name}")
                if apply:
                    try:
                        current.rename(dest)
                        current = dest
                    except OSError as e:
                        stats["errors"] += 1
                        logger.error("tidy: rename %s failed: %s",
                                     current, e)

        if ext not in AUDIO_EXTS:
            continue

        # 4b — top-level audio files (and files in stray digit-only
        # folders) move into their mdls artist folder.
        at_root = (
            current.parent == root
            or (current.parent.parent == root
                and re.fullmatch(r"\d{1,3}", current.parent.name))
        )
        if at_root:
            meta = _mdls(current)
            author = ""
            if meta.get("kMDItemAuthors"):
                author = meta["kMDItemAuthors"][0].strip()
            if author:
                artist_dir = root / camel(author)
                if (artist_dir != root
                        and artist_dir != current.parent
                        and not re.fullmatch(r"\d{1,3}", artist_dir.name)):
                    stats["file_moves"].append(
                        f"{current.relative_to(root)} -> "
                        f"{artist_dir.name}/")
                    if apply:
                        moved = _move_file(current, artist_dir, current.name)
                        if moved.parent == artist_dir:
                            current = moved

        # 4c — ugly / unnamed names: rename from metadata.
        if _UGLY.search(current.stem):
            meta = _mdls(current)
            title = meta.get("kMDItemTitle", "").strip()
            new_stem = ""
            if title and not _UNNAMED.search(title):
                t = _strip_leading_num(title).strip()
                t = re.sub(r"\s+", " ", t).replace("/", "-")
                if t and t.lower() != current.stem.lower():
                    new_stem = t[:80]
            if not new_stem and not _UNNAMED.search(current.stem):
                cleaned = _clean_download_name(current.stem)
                if (cleaned and len(cleaned) >= 4
                        and not cleaned.isdigit()
                        and cleaned.lower() != current.stem.lower()):
                    new_stem = cleaned[:80]
            if not new_stem and _UNNAMED.search(current.stem):
                artist = ""
                if current.parent != root:
                    artist = camel(current.parent.name)
                track = meta.get("kMDItemAudioTrackNumber", "").strip()
                m = re.search(r"(\d{1,3})", current.stem)
                num = track if track.isdigit() else (m.group(1) if m else "")
                if artist and num:
                    new_stem = f"{artist}-Track{int(num):02d}"
            if new_stem:
                new_name = new_stem + current.suffix
                dest = current.with_name(new_name)
                if not dest.exists():
                    stats["file_renames"].append(
                        f"{current.relative_to(root)} -> {new_name}")
                    if apply:
                        try:
                            current.rename(dest)
                        except OSError as e:
                            stats["errors"] += 1
                            logger.error("tidy: rename %s failed: %s",
                                         current, e)

    # 4 — 'Album1'-style folders holding audio become 'Singles'.
    for d in sorted(
        (p for p in root.rglob("*") if p.is_dir()), key=lambda p: str(p),
    ):
        if _protected(d.name) or _excluded(d, excludes):
            continue
        if _ALBUM_GENERIC.fullmatch(d.name) and not _is_empty_dir(d) \
                and _has_audio(d):
            target = d.with_name("Singles")
            if not target.exists():
                stats["renamed_dirs"].append(
                    f"{d.relative_to(root)}/ -> Singles/")
                if apply:
                    try:
                        d.rename(target)
                    except OSError as e:
                        stats["errors"] += 1
                        logger.error("tidy: rename %s failed: %s", d, e)

    # 5 — remove empty folders (deepest first).
    for d in sorted(
        (p for p in root.rglob("*") if p.is_dir()), key=lambda p: str(p),
        reverse=True,
    ):
        if _protected(d.name) or _excluded(d, excludes):
            continue
        if _is_empty_dir(d):
            stats["removed_dirs"].append(str(d.relative_to(root)))
            if apply:
                for junk in d.iterdir():
                    try:
                        junk.unlink()
                    except OSError:
                        pass
                try:
                    d.rmdir()
                except OSError as e:
                    logger.error("tidy: rmdir %s failed: %s", d, e)


def tidy_folders(roots: list, excludes: list = (), apply: bool = False) -> dict:
    stats = {
        "merged": [], "renamed_dirs": [], "flattened": [], "removed_dirs": [],
        "file_renames": [], "file_moves": [], "dupes_deleted": [],
        "errors": 0,
    }
    for root in roots:
        if not root.exists():
            logger.warning("tidy: root missing: %s", root)
            continue
        _tidy_root(root, list(excludes), apply, stats)
    return stats

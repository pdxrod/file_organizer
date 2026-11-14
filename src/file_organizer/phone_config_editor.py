"""`organize config` on the phone — a guided phone_sync_config.yaml editor.

Same conversation as the computer editor (Enter keeps, 'e' edits, 'x'
quits without saving, 's' saves and quits, Ctrl+C/EOF cancel cleanly),
but for the phone daemon's settings — source folders, the staging
folder, cloud sync, the free-space guard, the grace period, rename
rules, scan timing, filters and exclusions.

One important difference from the computer editor: this one must work
WITHOUT PyYAML. Termux no longer ships python-pyyaml and a pip install
would need the clang toolchain, so the phone daemon (phone_sync.py)
reads its config with a built-in flat-YAML parser. This editor writes
exactly that flat subset (`key: value` lines, block lists, comments),
so the file it produces is readable with or without PyYAML. Like the
computer editor, it never installs a broken file: the result is parsed
back with the daemon's own parser and only then moved into place.

Design rules (same as config_editor.py):
- Every prompt goes through ask()/confirm() from editor_common with an
  injectable input_fn and output_fn, so tests drive whole sessions with
  scripted answers and no terminal.
- The walk mutates the config in place, so a mid-walk 's' saves exactly
  what has been entered so far.
"""

import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from file_organizer.editor_common import (
    EditorAbort,
    EditorSaveQuit,
    _section,
    _toggle,
    ask,
    confirm,
    edit_path_list,
    path_status,
)
from file_organizer.phone_sync import (
    _extract_phone_section,
    _parse_simple_yaml,
)

# The daemon's built-in parser doesn't know `phone:` nesting — the live
# phone_sync_config.yaml is the de-dented phone: section (flat).
# These defaults mirror config_template.yaml's phone: section for keys a
# hand-edited file might be missing.
DEFAULTS: Dict[str, Any] = {
    "source_directories": [
        "/storage/emulated/0/DCIM",
        "/storage/emulated/0/Pictures",
        "/storage/emulated/0/Documents",
        "/storage/emulated/0/Download",
    ],
    "target_directory": "/storage/emulated/0/file_organizer_staging",
    "sync_cloud_drives": True,
    "cloud_sync_subfolder": "file_organizer",
    "cloud_drive_paths": [],
    "min_free_space_mb": 200,
    "min_age_minutes": 0,
    "collapse_rename_families": False,
    "hold_app_generated_names": True,
    "normalise_leading_dates": True,
    "scan_interval_seconds": 300,
    "idle_backoff_factor": 2,
    "idle_backoff_max_multiplier": 8,
    "max_file_size_mb": 500,
    "include_extensions": [],
    "exclude_extensions": [".tmp", ".temp", ".partial", ".crdownload",
                           ".part", ".download", ".crypt14", ".pyc", ".log"],
    "exclude_patterns": [".thumbnails", ".thumbdata", ".face", ".pending",
                         ".trashed", ".trash", "thumbdata", ".cache", ".tmp",
                         ".DS_Store", ".Spotlight-V100", ".TemporaryItems",
                         ".fseventsd", ".DocumentRevisions-V100"],
    "exclude_folder_names": [".git", ".hg", ".svn", ".cvs", ".venv", "venv",
                             "env", ".env", "node_modules", ".next", ".nuxt",
                             "__pycache__", ".pytest_cache", ".mypy_cache",
                             ".tox", ".cache", ".parcel-cache", "coverage",
                             ".nyc_output", "elm-stuff", ".elixir_ls",
                             ".stack-work", "_build", "deps", "ebin", "dist",
                             "build", "target", ".bundle", "vendor", "bundle",
                             "priv/static", ".gradle", ".m2", "tmp/cache"],
    "exclude_folder_patterns": ["line", "twitter", "gmail", "whatsapp",
                                "telegram"],
    "delete_after_copy": False,
    "prune_max_age_days": 30,
}


# ── phone config health check (bare './organize' auto-open rule) ───────────

def phone_config_problem(path: str) -> Optional[str]:
    """None when the phone config is usable; otherwise a plain-language
    description of what is wrong. Used to decide whether bare `./organize`
    on a phone should open the editor instead of printing usage."""
    if not os.path.exists(path):
        return f"no phone_sync_config.yaml yet at {path}"
    try:
        with open(path, "r") as f:
            content = f.read()
    except OSError as e:
        return f"cannot read {path}: {e}"
    for i, line in enumerate(content.splitlines(), start=1):
        indent = line[:len(line) - len(line.lstrip())]
        if "\t" in indent:
            return (f"{path} line {i}: TAB characters are not allowed in "
                    "YAML indentation — use spaces")
    loaded = _load_content(content)
    if not loaded:
        return f"{path} is empty — nothing configured yet"
    if not isinstance(loaded, dict):
        return f"{path} must contain YAML settings (a top-level mapping)"
    return None


def needs_phone_editor(config_path: str) -> bool:
    """True when the phone config is absent or invalid — the rule that
    makes bare `./organize` on a phone launch the editor automatically."""
    return phone_config_problem(config_path) is not None


def _load_content(content: str) -> Optional[dict]:
    """Parse config text the same way the daemon does: PyYAML when it is
    importable, otherwise the built-in flat parser. Any PyYAML failure
    (syntax error, odd structure) falls back to the flat parser too, and
    a total failure returns None."""
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml is not None:
        try:
            loaded = yaml.safe_load(content)
        except Exception:
            loaded = None
        if isinstance(loaded, dict) and loaded:
            return loaded
    try:
        return _parse_simple_yaml(content)
    except ValueError:
        return None


def _load_phone_config(path: str) -> dict:
    loaded = _load_content(Path(path).read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


# ── flat YAML writer (readable by the daemon with or without PyYAML) ───────

def _scalar_to_text(value) -> str:
    """One flat-subset scalar. Values the built-in parser would misread
    (numbers, booleans, null, empty, embedded comments, quote-ish starts)
    are double-quoted; it returns quoted strings verbatim."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    low = s.lower()
    bare = s.strip()
    needs_quotes = (
        not s
        or bare != s
        or low in ("true", "false", "yes", "no", "on", "off",
                   "null", "~", "none")
        or re.match(r"^[+-]?\d+(\.\d+)?$", s)
        or " #" in s
        or s[0] in "'\"[{" 
    )
    if not needs_quotes:
        return s
    if '"' in s:
        # The flat parser does no unescaping, so single-quote instead.
        return f"'{s}'"
    return f'"{s}"'


def _dump_flat(cfg: Dict) -> str:
    """The flat YAML subset the daemon's built-in parser reads back:
    `key: value` lines, block lists, and comments. No PyYAML needed."""
    lines = [
        "# phone_sync_config.yaml — written by './organize config'.",
        "# Run './organize config' again any time to change these",
        "# settings (Enter keeps a value, e edits, x quits without",
        "# saving, s saves and quits).",
        "",
    ]
    for key, value in cfg.items():
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
                continue
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_scalar_to_text(item)}")
        else:
            lines.append(f"{key}: {_scalar_to_text(value)}")
    return "\n".join(lines) + "\n"


def _write_phone_config(path: str, cfg: Dict, output_fn=None) -> Optional[str]:
    """Dump the config in the flat subset, parse it back with the daemon's
    own parser, and only then move it into place. Returns an error message,
    or None on success. The previous file survives any failure."""
    path = Path(path)
    tmp = path.with_name(path.name + ".new")
    text = _dump_flat(cfg)
    try:
        tmp.write_text(text, encoding="utf-8")
    except OSError as e:
        return f"could not write {tmp}: {e}"
    try:
        parsed = _parse_simple_yaml(text)
    except ValueError as e:
        tmp.unlink(missing_ok=True)
        return f"the new settings don't validate: {e}"
    if parsed != cfg:
        tmp.unlink(missing_ok=True)
        return ("the new settings don't round-trip through the phone's "
                f"config parser ({parsed!r} != expected)")
    os.replace(tmp, path)
    return None


# ── section helpers ────────────────────────────────────────────────────────

def _ask_number(cfg: Dict, key: str, title: str, explanation: str,
                input_fn, output_fn, minimum: Optional[int] = None) -> None:
    """Edit one whole-number setting. Blank keeps the current value."""
    current = cfg.get(key)
    output_fn("")
    output_fn(f"{title}")
    for line in explanation.splitlines():
        output_fn(f"  {line}")
    output_fn(f"  Currently: {current}")

    def validate(v):
        try:
            n = int(v)
        except ValueError:
            return "type a whole number (or Enter to keep the current one)"
        if minimum is not None and n < minimum:
            return f"must be at least {minimum}"
        return None

    answer = ask("  new value:", input_fn, output_fn, default=str(current),
                 prefill=str(current), validate=validate)
    cfg[key] = int(answer)


def _create_from_template(template_path: str, config_path: str) -> Optional[str]:
    """Write a live phone config from the template's phone: section
    (de-dented, so it stays inside the flat subset). Returns an error
    message, or None on success."""
    try:
        content = Path(template_path).read_text(encoding="utf-8")
    except OSError as e:
        return f"cannot read the template {template_path}: {e}"
    if "phone:" not in content:
        return f"{template_path} has no phone: section"
    header = (
        "# Generated from config_template.yaml (the phone: section) by\n"
        "# './organize config'. Run './organize config' again any time.\n"
    )
    try:
        Path(config_path).write_text(
            header + _extract_phone_section(content), encoding="utf-8"
        )
    except OSError as e:
        return f"could not write {config_path}: {e}"
    return None


def _staging_inside_source(sources: List[str], target: str) -> Optional[str]:
    """Warn when the staging folder is inside a source folder (or vice
    versa) — the daemon would re-copy its own output every scan."""
    t = os.path.expanduser(str(target)).rstrip("/")
    if not t:
        return None
    for src in sources:
        s = os.path.expanduser(str(src)).rstrip("/")
        if not s:
            continue
        if t == s or t.startswith(s + "/"):
            return f"the staging folder is inside a source folder ({src})"
        if s.startswith(t + "/"):
            return f"a source folder is inside the staging folder ({src})"
    return None


# ── the editor ─────────────────────────────────────────────────────────────

def run_phone_editor(config_path: str, template_path: Optional[str] = None,
                     input_fn=None, output_fn=None) -> int:
    """Walk the user through phone_sync_config.yaml section by section.

    Returns 0 when a new config was saved, 1 when nothing was written
    (cancelled, or the result failed validation)."""
    input_fn = input_fn if input_fn is not None else input
    output_fn = output_fn if output_fn is not None else print
    config_path = str(config_path)
    if not template_path:
        template_path = str(Path(config_path).parent / "config_template.yaml")
    if not os.path.exists(template_path):
        return _fail(output_fn, f"template not found: {template_path}")

    output_fn("")
    output_fn("organize config (phone) — change phone_sync_config.yaml")
    output_fn("section by section. I ask, you type; press Enter to keep")
    output_fn("what's there. Type x at any prompt to quit without saving,")
    output_fn("or s to save what you've changed so far and quit.")
    output_fn("")

    cfg: Dict = {}
    try:
        problem = phone_config_problem(config_path)
        if problem:
            output_fn(f"⚠  {problem}")
            output_fn("")
            if not confirm(
                "Start fresh from the template's phone: settings and set"
                " it up now?", input_fn, output_fn, default=True,
            ):
                output_fn("Nothing changed. Run './organize config' again")
                output_fn("when ready.")
                return 1
            err = _create_from_template(template_path, config_path)
            if err:
                return _fail(output_fn, err)
            cfg = _load_phone_config(config_path)
            output_fn("Created phone_sync_config.yaml from the template —")
            output_fn("anything you skip stays exactly as it is.")
        else:
            cfg = _load_phone_config(config_path)
            output_fn("Loaded your current phone_sync_config.yaml —")
            output_fn("anything you skip stays exactly as it is.")

        for key, value in DEFAULTS.items():
            cfg.setdefault(key, value)

        # ── the section walk ──
        edit_path_list(
            "Source folders",
            cfg["source_directories"],
            "Folders the daemon watches for new files. Missing folders are\n"
            "skipped silently, so it is safe to keep a generous list. The\n"
            "staging folder must NOT be inside any of these.",
            input_fn, output_fn,
            example="/storage/emulated/0/DCIM",
        )

        output_fn("")
        output_fn("Staging folder")
        output_fn("  Files are copied here, keeping their relative folder")
        output_fn("  structure — the computer pulls them from here over USB.")
        target = ask("  staging folder:", input_fn, output_fn,
                     default=cfg["target_directory"],
                     prefill=str(cfg["target_directory"]))
        if target is not None:
            cfg["target_directory"] = target

        if _toggle(
            cfg, "sync_cloud_drives", "Cloud sync on the phone",
            "Copies new files straight into the cloud drive folders found\n"
            "on this device (Proton Drive, Google Drive, …) — no computer\n"
            "needed. Off = only stage files for the computer's USB pull.",
            input_fn, output_fn,
        ):
            sub = ask("  subfolder inside each cloud drive:",
                      input_fn, output_fn,
                      default=cfg["cloud_sync_subfolder"],
                      prefill=str(cfg["cloud_sync_subfolder"]))
            if sub is not None:
                cfg["cloud_sync_subfolder"] = sub

        edit_path_list(
            "Cloud drive folders to look for",
            cfg["cloud_drive_paths"],
            "Patterns './organize find-cloud-drives' checks for your cloud\n"
            "apps. Wildcards like * are allowed — missing paths are\n"
            "ignored.",
            input_fn, output_fn, show_status=False,
        )

        _ask_number(
            cfg, "min_free_space_mb", "Free-space guard (MB)",
            "The daemon stops copying when the target has less free space\n"
            "than this — it never fills the phone up. 0 = off.",
            input_fn, output_fn, minimum=0,
        )
        _ask_number(
            cfg, "min_age_minutes", "Grace period (minutes)",
            "Files younger than this are left alone — time to delete\n"
            "unwanted photos BEFORE they get copied. 0 = copy\n"
            "immediately (the default).",
            input_fn, output_fn, minimum=0,
        )
        _toggle(
            cfg, "collapse_rename_families", "Collapse rename families",
            "Turns 'foo (1).png' download-copy families into one file,\n"
            "DELETING the losing copies. Off by default for safety.",
            input_fn, output_fn,
        )
        _toggle(
            cfg, "hold_app_generated_names", "Hold app-generated names",
            "Screenshot_…, IMG-…, PXL_… files are NOT copied until you\n"
            "rename them to something meaningful (with './organize\n"
            "rename'). Off = copy everything.",
            input_fn, output_fn,
        )
        _toggle(
            cfg, "normalise_leading_dates", "Normalise leading dates",
            "Rewrites 2026-09-23-party.jpg to 20260923-party.jpg on the\n"
            "STAGED copy — the original keeps its name.",
            input_fn, output_fn,
        )
        _ask_number(
            cfg, "scan_interval_seconds", "Scan interval (seconds)",
            "How often the daemon checks for new files. 60-300 is\n"
            "responsive; 300 saves battery.",
            input_fn, output_fn, minimum=10,
        )
        _ask_number(
            cfg, "idle_backoff_factor", "Idle backoff factor",
            "When a scan copies nothing, the next sleep is this many\n"
            "times longer (up to the max multiplier below). Saves battery.",
            input_fn, output_fn, minimum=1,
        )
        _ask_number(
            cfg, "idle_backoff_max_multiplier", "Idle backoff maximum",
            "The longest idle sleep is scan_interval_seconds times this.",
            input_fn, output_fn, minimum=1,
        )
        _ask_number(
            cfg, "max_file_size_mb", "Maximum file size (MB)",
            "Files larger than this are skipped. 0 = no limit.",
            input_fn, output_fn, minimum=0,
        )
        edit_path_list(
            "Include extensions",
            cfg["include_extensions"],
            "If non-empty, ONLY these extensions are copied (dots\n"
            "included). Empty = copy ALL types.",
            input_fn, output_fn, show_status=False,
            example=".jpg, .png",
        )
        edit_path_list(
            "Exclude extensions",
            cfg["exclude_extensions"],
            "These are NEVER copied. The built-in safety defaults\n"
            "(.tmp, .crypt14, .pyc, …) stay in force — this list only\n"
            "ever ADDS.",
            input_fn, output_fn, show_status=False,
        )
        edit_path_list(
            "Excluded names",
            cfg["exclude_patterns"],
            "Files or folders whose name contains any of these are\n"
            "skipped. Built-in safety defaults always stay in force —\n"
            "this list only ever ADDS.",
            input_fn, output_fn, show_status=False,
        )
        edit_path_list(
            "Excluded folder names (exact)",
            cfg["exclude_folder_names"],
            "Folders whose name EXACTLY matches one of these are never\n"
            "copied ('build' skips a folder called 'build' but not\n"
            "'mybuildings'). Built-in safety defaults stay in force.",
            input_fn, output_fn, show_status=False,
        )
        edit_path_list(
            "Excluded folder patterns",
            cfg["exclude_folder_patterns"],
            "Whole folders whose name CONTAINS any of these are skipped —\n"
            "but files with these words in their names still copy (so\n"
            "airline-ticket.pdf survives excluding 'gmail').",
            input_fn, output_fn, show_status=False,
        )
        _toggle(
            cfg, "delete_after_copy", "Delete after copy",
            "DANGER: deletes the ORIGINAL file after copying it. Keeps\n"
            "the phone clean but you lose the original. Strongly\n"
            "recommended: keep this OFF.",
            input_fn, output_fn,
        )
        _ask_number(
            cfg, "prune_max_age_days", "Prune stale entries after N days",
            "If a source file has been deleted for at least this many\n"
            "days, its database entry and staged copy are removed.",
            input_fn, output_fn, minimum=0,
        )

        _section(output_fn, "Review", "These are the settings you chose:")
        for key in (
            "target_directory", "sync_cloud_drives", "min_free_space_mb",
            "min_age_minutes", "collapse_rename_families",
            "hold_app_generated_names", "normalise_leading_dates",
            "scan_interval_seconds", "max_file_size_mb",
            "delete_after_copy",
        ):
            output_fn(f"  {key}: {cfg.get(key)}")
        if not confirm("Save these settings to phone_sync_config.yaml?",
                       input_fn, output_fn, default=True):
            output_fn("Nothing changed — phone_sync_config.yaml was left")
            output_fn("as it was.")
            return 1
        err = _write_phone_config(config_path, cfg, output_fn)
        if err:
            output_fn(f"✗  {err}")
            output_fn("Your previous phone_sync_config.yaml was left")
            output_fn("untouched.")
            return 1
        output_fn(f"✓  Saved {config_path}")
        output_fn("")
        output_fn("Will it work?")
        output_fn(f"  {path_status(str(cfg['target_directory']))}   "
                  f"staging: {cfg['target_directory']}")
        warn = _staging_inside_source(
            cfg["source_directories"], str(cfg["target_directory"])
        )
        if warn:
            output_fn(f"  ⚠  {warn}")
        output_fn("")
        output_fn("Next: ./organize test     (dry run — shows what would")
        output_fn("                          happen, changes nothing)")
        output_fn("      ./organize scan     (one real scan)")
        output_fn("      ./organize start    (run in the background)")
        return 0
    except EditorSaveQuit:
        # 's' anywhere: save exactly what has been entered so far.
        if not cfg:
            err = _create_from_template(template_path, config_path)
            if err:
                return _fail(output_fn, err)
            cfg = _load_phone_config(config_path)
            output_fn("(Nothing configured yet — saving the template's")
            output_fn("phone: settings as a starting point.)")
        err = _write_phone_config(config_path, cfg, output_fn)
        if err:
            output_fn(f"✗  {err}")
            output_fn("Your previous phone_sync_config.yaml was left")
            output_fn("untouched.")
            return 1
        output_fn(f"✓  Saved {config_path} (your changes so far) — exiting.")
        return 0
    except EditorAbort:
        output_fn("")
        output_fn("Cancelled — nothing was changed.")
        return 1


def _fail(output_fn, message: str) -> int:
    output_fn(f"✗  {message}")
    return 1


if __name__ == "__main__":
    sys.exit(run_phone_editor(
        str(Path(__file__).resolve().parent.parent.parent
            / "phone_sync_config.yaml"),
    ))

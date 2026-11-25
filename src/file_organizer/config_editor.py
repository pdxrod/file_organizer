"""`organize config` — a guided, section-by-section config.yaml editor.

The user runs `./organize config` (or plain `./organize` when config.yaml
is missing or invalid) and this walks them through the settings one
section at a time, in plain words: drives, folders to scan, sync pairs,
one-way backups, the organized folder, feature toggles, the router, the
phone pull, and excluded names. Anything they skip stays exactly as it
was; pressing Enter always keeps the current value.

Design rules:

- Every prompt goes through ask()/confirm() with an injectable input_fn
  and output_fn, so tests drive the whole editor with a scripted list of
  answers and no terminal.
- The editor never writes a broken file: the result is dumped with
  yaml.safe_dump (spaces, never tabs), loaded back through Config to
  validate, and only then moved into place with os.replace. The previous
  config.yaml survives any failure.
- Typing 'x' (case-insensitive) at ANY prompt quits immediately with
  nothing saved — the same 'x stops' convention as './organize rename'
  on the phone. Ctrl+C and EOF cancel cleanly too.
- Typing 's' at ANY prompt does the opposite: it saves everything
  entered so far (including edits in the middle of a section — the
  section editors mutate the config in place) and exits 0.
"""

import io
import os
import sys
import yaml
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any, Dict, List, Optional

from file_organizer.config import (
    Config,
    ConfigError,
    _check_no_tabs,
    starter_data,
)
from file_organizer.editor_common import (
    EditorAbort,
    EditorSaveQuit,
    _input_with_prefill,
    _pick_number,
    _section,
    _toggle,
    ask,
    confirm,
    edit_path_list,
    edit_pairs,
    path_status,
)


# ── config health check (used by the launcher's auto-open rule) ────────────

def config_problem(path: str) -> Optional[str]:
    """None when the config file is usable; otherwise a plain-language
    description of what is wrong. Used to decide whether bare `./organize`
    should open the editor instead of printing usage."""
    if not os.path.exists(path):
        return f"no config.yaml yet at {path}"
    try:
        with open(path, "r") as f:
            content = f.read()
    except OSError as e:
        return f"cannot read {path}: {e}"
    try:
        _check_no_tabs(content, path)
    except ConfigError as e:
        return str(e)
    try:
        loaded = yaml.safe_load(content)
    except yaml.YAMLError as e:
        detail = (str(e).splitlines() or ["unknown YAML error"])[0].strip()
        return f"{path} is not valid YAML ({detail})"
    if loaded is None:
        return f"{path} is empty — nothing configured yet"
    if not isinstance(loaded, dict) or (
        "computer" in loaded and not isinstance(loaded.get("computer"), dict)
    ):
        return f"{path} must contain YAML settings (a top-level mapping)"
    try:
        # Full load + validation. Config prints drive warnings to stderr —
        # that is report noise here, not a reason to open the editor.
        with redirect_stderr(io.StringIO()):
            Config(path)
    except ConfigError as e:
        return str(e)
    except Exception as e:  # any other load failure is still a broken config
        return f"{path} could not be loaded: {e}"
    return None


def needs_editor(config_path: str) -> bool:
    """True when the config is absent or invalid — the rule that makes bare
    `./organize` launch the editor automatically."""
    return config_problem(config_path) is not None


# ── section editors ────────────────────────────────────────────────────────

def edit_drives(drives, input_fn, output_fn) -> Dict[str, str]:
    """Edit the drives: map of NAME -> folder. Add/edit/remove entries.
    Mutates the passed dict in place, so a mid-section save-and-quit
    keeps everything entered so far."""
    if drives is None:
        drives = {}
    hints = {
        "MAIN_DRIVE": "your home folder on this computer",
        "EXTERNAL_DRIVE": "an external/backup disk you use sometimes",
        "PROTON_DRIVE": "the Proton Drive folder this computer mounts",
        "GOOGLE_DRIVE": "the Google Drive sync folder on this computer",
    }
    _section(
        output_fn,
        "Drives",
        "Drive names are shortcuts: 'MAIN_DRIVE/Documents' means\n"
        "'/Users/you/Documents'. Each drive is a folder on this computer,\n"
        "cloud drives included (give the folder the cloud app keeps its\n"
        "files in). A drive that isn't there right now is skipped with a\n"
        "warning, so keep entries for disks you use sometimes.\n"
        "\n"
        "Type e to edit one — you can change BOTH its name and its\n"
        "folder (e4 edits drive 4 directly). r removes, a adds.",
    )
    while True:
        output_fn("")
        names = list(drives)
        if names:
            for i, name in enumerate(names, 1):
                hint = f"  ({hints[name]})" if name in hints else ""
                output_fn(f"  {i}. {name}: {drives[name]}{hint}")
                output_fn(f"     {path_status(drives[name])}")
        else:
            output_fn("  (no drives yet — press a to add one)")
        prompt = ("  [enter]=done, a=add, e=edit, r=remove, "
                  "x=quit, s=save+quit"
                  if names else "  [enter]=done, a=add drive, "
                  "x=quit, s=save+quit")
        try:
            raw = input_fn(prompt + " > ")
        except (EOFError, KeyboardInterrupt):
            raise EditorAbort()
        cmd = raw.strip().lower()
        if cmd == "x":
            raise EditorAbort()
        if cmd == "s":
            raise EditorSaveQuit()
        if not cmd:
            break
        if cmd in ("a", "add"):
            name = ask("  drive name (UPPERCASE, e.g. BACKUP_DRIVE):",
                       input_fn, output_fn, default=None)
            if not name:
                continue
            name = name.strip().upper()
            value = ask(f"  folder for {name}:", input_fn, output_fn,
                        default=None)
            if value is not None:
                drives[name] = value
                output_fn(f"  {name}: {value}  ({path_status(value)})")
            continue
        edit_num = remove_num = None
        if cmd[0] == "e" and cmd[1:].isdigit():
            edit_num = int(cmd[1:])
        elif cmd in ("e", "edit"):
            edit_num = _pick_number("drive", names, input_fn, output_fn)
        elif cmd[0] == "r" and cmd[1:].isdigit():
            remove_num = int(cmd[1:])
        elif cmd in ("r", "remove"):
            remove_num = _pick_number("drive", names, input_fn, output_fn)

        if edit_num is not None:
            if 1 <= edit_num <= len(names):
                name = names[edit_num - 1]
                new_name = ask(f"  name [{name}] (hit Enter to keep it):",
                               input_fn, output_fn, default=name,
                               prefill=name)
                new_name = new_name.strip().upper()
                if new_name != name and new_name in drives:
                    output_fn(f"  {new_name} already exists — keeping {name}")
                    new_name = name
                value = ask(f"  folder [{drives[name]}] (hit Enter to keep it):",
                            input_fn, output_fn, default=drives[name],
                            prefill=drives[name])
                if new_name != name:
                    drives[new_name] = drives.pop(name)
                drives[new_name] = value
                output_fn(f"  {new_name}: {value}  ({path_status(value)})")
            else:
                output_fn(f"  there is no drive {edit_num}")
            continue
        if remove_num is not None:
            if 1 <= remove_num <= len(names):
                name = names.pop(remove_num - 1)
                drives.pop(name, None)
                output_fn(f"  removed {name}")
            else:
                output_fn(f"  there is no drive {remove_num}")
            continue
        output_fn("  (e edits a drive — its name AND its folder, e.g. e4; "
                  "r removes; enter finishes)")
    return drives


def _output_base(cfg: Dict, input_fn, output_fn) -> None:
    current = cfg.get("output_base", "")
    _section(
        output_fn,
        "The 'organized' folder",
        "A browsable tree of soft links into your real files (nothing is\n"
        "copied or moved). Default: the organized/ folder inside this\n"
        "file_organizer folder. Type a path to put it somewhere else,\n"
        "e.g. ~/organized or PROTON_DRIVE/organized.",
    )
    value = ask("  folder for the organized tree [hit Enter to keep it]:",
                input_fn, output_fn, default=current, prefill=current)
    if value in (None, "", "organized", "~/organized"):
        cfg["output_base"] = "organized"
    else:
        cfg["output_base"] = value


def _ask_number(question, input_fn, output_fn, default, converter=int):
    """One number with the current value prefilled (Enter keeps it).
    Anything that isn't a number keeps the current value."""
    value = ask(question, input_fn, output_fn, default="",
                prefill=str(default))
    value = (value or "").strip()
    if not value:
        return default
    try:
        return converter(value)
    except ValueError:
        output_fn("  (not a number — keeping the current value)")
        return default


def _router(cfg: Dict, input_fn, output_fn) -> None:
    router = cfg.setdefault("router", {})
    on = _toggle(
        router, "enabled", "Smart router",
        "Moves files into category folders (documents → ~/Documents, "
        "music →\n~/Music, …) by reading each file's name and contents. "
        "It MOVES\nfiles, so it starts OFF — plan first with "
        "'./organize route'.",
        input_fn, output_fn,
    )
    if not on:
        return

    action = router.get("action", "move")
    output_fn(f"  Router action: {action} (move = the normal choice).")
    keep = confirm("  Keep it that way?", input_fn, output_fn, default=True)
    if not keep:
        router["action"] = "copy" if action == "move" else "move"
        output_fn(f"  action is now: {router['action']}")

    output_fn("")
    output_fn("Your categories — each one is a folder the router can sort")
    output_fn("files into. Enter keeps the current path; a blank path")
    output_fn("removes the category. You can add any category you like.")
    targets = dict(router.get("target_folders") or {})
    for name in list(targets):
        current = str(targets.get(name, "") or "")
        # prefill shows the current path on the line, so Enter keeps it;
        # a genuinely blank answer (delete the text) removes the category.
        value = ask(f"  {name} [{current or 'not set'}]:",
                    input_fn, output_fn, default="",
                    prefill=current or None)
        if value.strip():
            targets[name] = value.strip()
        else:
            targets.pop(name, None)
    for name, desc in (
        ("documents", "general documents"),
        ("pictures", "images"),
        ("videos", "videos"),
        ("music", "music"),
        ("development", "software projects and dev notes"),
        ("misc", "the catch-all for files the router can't place"),
    ):
        if name in targets:
            continue
        suggested = {
            "documents": "MAIN_DRIVE/Documents",
            "pictures": "MAIN_DRIVE/Pictures",
            "videos": "MAIN_DRIVE/Videos",
            "music": "MAIN_DRIVE/Music",
            "development": "MAIN_DRIVE/Development",
            "misc": "MAIN_DRIVE/Downloads",
        }[name]
        # The suggested folder sits on the line: Enter accepts it, typing
        # over it uses the typed folder, clearing the line skips.
        value = ask(
            f"  Add '{name}' ({desc})?\n"
            f"    suggested: {suggested} — Enter accepts, type your own,\n"
            f"    or clear the line to skip:",
            input_fn, output_fn, default="", prefill=suggested,
        )
        if value.strip():
            targets[name] = value.strip()
    while confirm("  Add a category of your own?",
                  input_fn, output_fn, default=False):
        name = ask("  Category name (lowercase, underscores):",
                   input_fn, output_fn, default="")
        if not name.strip():
            continue
        name = name.strip().lower().replace(" ", "_")
        if name in targets:
            output_fn(f"  '{name}' is already a category.")
            continue
        path = ask(f"  Folder for '{name}' files:",
                   input_fn, output_fn, default="")
        if not path.strip():
            continue
        targets[name] = path.strip()
    router["target_folders"] = targets

    # ── keyword hints for every category ──
    output_fn("")
    output_fn("Keyword hints (optional): words or short phrases that mark")
    output_fn("a file for a category — matched in names and contents.")
    output_fn("Enter a series of hints, separated by commas, e.g.")
    output_fn("'donald trump, iran'. Enter keeps what's shown; a blank")
    output_fn("answer removes them.")
    keywords = dict(router.get("keywords") or {})
    for name in targets:
        legacy = "ml" if name == "ml_development" else name
        current = keywords.get(name)
        if not current and legacy != name:
            current = keywords.get(legacy)
        prefill = ", ".join(str(w) for w in (current or []))
        value = ask(f"  hints for '{name}' [{prefill or 'none'}]:",
                    input_fn, output_fn, default="",
                    prefill=prefill or None)
        raw_hints = value.strip()
        if "," in raw_hints:
            # Comma-separated: each item is one hint; spaces inside an
            # item keep it whole as a phrase ('donald trump').
            hints = [w.strip() for w in raw_hints.split(",") if w.strip()]
        else:
            # Plain space-separated words: one hint per word.
            hints = [w for w in raw_hints.split() if w.strip()]
        output_fn(f"  hints for '{name}': "
                  f"{', '.join(hints) if hints else '(none)'}")
        if hints:
            keywords[name] = hints
            if legacy != name:
                keywords.pop(legacy, None)
        else:
            keywords.pop(name, None)
            if legacy != name:
                keywords.pop(legacy, None)
    if keywords:
        router["keywords"] = keywords
    elif "keywords" in router:
        router.pop("keywords")

    router.setdefault("route_roots", [])
    edit_path_list(
        "Route roots",
        router["route_roots"],
        "Folders swept when you run './organize route'. Files there are\n"
        "classified once. Your downloads folder is a good inbox.",
        input_fn, output_fn, example="MAIN_DRIVE/Downloads",
        drives=cfg.get("drives"),
    )
    router.setdefault("watch_folders", [])
    edit_path_list(
        "Watch folders",
        router["watch_folders"],
        "New files dropped here are routed automatically on every\n"
        "production cycle (./organize auto / start).",
        input_fn, output_fn, example="MAIN_DRIVE/Downloads",
        drives=cfg.get("drives"),
    )
    router.setdefault("watch_on_cycles", True)
    _toggle(
        router, "watch_on_cycles", "Route new watch-folder files automatically",
        "Every production cycle (./organize auto / start) classifies new\n"
        "files dropped in the watch folders, not only when you run\n"
        "'./organize route'.",
        input_fn, output_fn,
    )

    router.setdefault("rename_folders", [])
    edit_path_list(
        "Rename-by-content folders",
        router["rename_folders"],
        "Folders './organize rename-by-content' sweeps for files with\n"
        "auto-generated names (4665.jpg, IMG-…, Screenshot_…, DSC…): the\n"
        "content becomes the name. Empty = the pictures and documents\n"
        "targets (plus political when configured).",
        input_fn, output_fn, example="MAIN_DRIVE/Pictures",
        drives=cfg.get("drives"),
    )
    router.setdefault("year_folders", [])
    edit_path_list(
        "Year-sort folders",
        router["year_folders"],
        "Folders './organize tidy' sorts by year: dated files move into\n"
        "<folder>/<year>/ subfolders. Empty = the pictures, videos and\n"
        "documents targets (plus political when configured).",
        input_fn, output_fn, example="MAIN_DRIVE/Pictures",
        drives=cfg.get("drives"),
    )

    router.setdefault("year_subfolders", True)
    if _toggle(
        router, "year_subfolders", "Year subfolders",
        "Dated files inside a category folder get a <year> subfolder\n"
        "(e.g. ~/Pictures/2018, ~/Videos/2026); everything else stays\n"
        "flat.",
        input_fn, output_fn,
    ):
        cats = router.get("year_subfolder_categories")
        cats = (list(cats) if isinstance(cats, list)
                else ["pictures", "videos", "documents"])
        prefill = ", ".join(cats)
        value = ask(
            f"  categories with year subfolders [{prefill}]:",
            input_fn, output_fn, default="",
            prefill=prefill or None,
        )
        picked = [c.strip().lower() for c in value.split(",") if c.strip()]
        router["year_subfolder_categories"] = picked or cats
    elif "year_subfolder_categories" in router:
        router.pop("year_subfolder_categories")

    deepseek = router.setdefault("deepseek", {})
    if _toggle(
        deepseek, "enabled", "DeepSeek classification",
        "Files the local rules can't place are sent to DeepSeek for a\n"
        "category. Needs an API key (a text file path, or the\n"
        "DEEPSEEK_API_KEY environment variable).",
        input_fn, output_fn,
    ):
        deepseek["api_key_path"] = ask(
            f"  API key file path [hit Enter to keep it: "
            f"{deepseek.get('api_key_path', '') or 'use env var'}]:",
            input_fn, output_fn, default=deepseek.get("api_key_path", ""),
            prefill=deepseek.get("api_key_path") or None,
        ) or ""
        deepseek["model"] = ask(
            f"  model [{deepseek.get('model', 'deepseek-chat')}]:",
            input_fn, output_fn,
            default=deepseek.get("model", "deepseek-chat"),
            prefill=deepseek.get("model") or None,
        ) or "deepseek-chat"
        deepseek.setdefault("media_enabled", True)
        _toggle(
            deepseek, "media_enabled", "Send media to DeepSeek",
            "Images and videos reach DeepSeek as OCR text + Vision\n"
            "scene labels when this is on.",
            input_fn, output_fn,
        )
        deepseek["min_confidence"] = _ask_number(
            f"  minimum confidence, 0.0-1.0 "
            f"[{deepseek.get('min_confidence', 0.6)}]:",
            input_fn, output_fn, float(deepseek.get("min_confidence", 0.6)),
            converter=float,
        )
        deepseek["batch_size"] = _ask_number(
            f"  files per DeepSeek request "
            f"[{deepseek.get('batch_size', 20)}]:",
            input_fn, output_fn, int(deepseek.get("batch_size", 20) or 20),
        )
        deepseek["max_calls_per_run"] = _ask_number(
            f"  DeepSeek calls per run "
            f"[{deepseek.get('max_calls_per_run', 50)}]:",
            input_fn, output_fn,
            int(deepseek.get("max_calls_per_run", 50) or 0),
        )
        deepseek["max_chars_per_file"] = _ask_number(
            f"  characters of content sent per file "
            f"[{deepseek.get('max_chars_per_file', 1500)}]:",
            input_fn, output_fn,
            int(deepseek.get("max_chars_per_file", 1500) or 1500),
        )
        deepseek["timeout_seconds"] = _ask_number(
            f"  request timeout in seconds "
            f"[{deepseek.get('timeout_seconds', 90)}]:",
            input_fn, output_fn,
            int(deepseek.get("timeout_seconds", 90) or 90),
        )

    ocr = router.setdefault("ocr", {})
    ocr.setdefault("enabled", True)
    if _toggle(
        ocr, "enabled", "OCR for images and PDFs",
        "Reads text out of images, videos and scanned PDFs so the router\n"
        "can classify them (macOS Vision; skipped on other platforms).",
        input_fn, output_fn,
    ):
        ocr["budget_per_run"] = _ask_number(
            f"  media files OCR'd per run "
            f"[{ocr.get('budget_per_run', 300)}]:",
            input_fn, output_fn, int(ocr.get("budget_per_run", 300) or 0),
        )

    max_bytes = int(router.get("max_file_size", 262144000) or 262144000)
    mb = max(1, max_bytes // (1024 * 1024))
    mb = _ask_number(
        f"  largest file read by content, in MB [{mb}]:",
        input_fn, output_fn, mb,
    )
    router["max_file_size"] = int(mb) * 1024 * 1024


def _phone_pull(cfg: Dict, input_fn, output_fn) -> None:
    pp = cfg.setdefault("phone_pull", {})
    on = _toggle(
        pp, "auto_pull", "Phone pull (USB)",
        "Every production cycle pulls new phone files over USB when a\n"
        "phone is plugged in (exits quietly when none is). Set false to\n"
        "only pull by hand with './organize pull'.",
        input_fn, output_fn,
    )
    if not on:
        return
    pp["local_stage"] = ask(
        f"  where phone files land on this computer "
        f"[{pp.get('local_stage', '')}]:",
        input_fn, output_fn, default=pp.get("local_stage", ""),
        prefill=pp.get("local_stage") or None,
    ) or ""
    pp["remote_stage"] = ask(
        f"  what syncs back to the phone [{pp.get('remote_stage', '')}]:",
        input_fn, output_fn, default=pp.get("remote_stage", ""),
        prefill=pp.get("remote_stage") or None,
    ) or ""
    pp["adb_path"] = ask(
        f"  path to the adb tool [{pp.get('adb_path', '')}]:",
        input_fn, output_fn, default=pp.get("adb_path", ""),
        prefill=pp.get("adb_path") or None,
    ) or ""


# ── review + save ──────────────────────────────────────────────────────────

def _summarize(cfg: Dict, output_fn) -> None:
    on_off = lambda v: "ON" if v else "OFF"  # noqa: E731
    output_fn("")
    output_fn("═" * 60)
    output_fn("  Review — what you set")
    output_fn("═" * 60)
    for name, path in (cfg.get("drives") or {}).items():
        output_fn(f"  {name}: {path}  ({path_status(path)})")
    srcs = cfg.get("source_folders") or []
    output_fn(f"  source folders: {', '.join(srcs) if srcs else '(none)'}")
    output_fn(f"  sync pairs: {len(cfg.get('sync_pairs') or [])}"
              f"    one-way pairs: {len(cfg.get('one_way_pairs') or [])}")
    output_fn(f"  organized folder: {cfg.get('output_base') or 'organized'}")
    output_fn(f"  folder sync: {on_off(cfg.get('enable_folder_sync'))}"
              f"    content analysis: {on_off(cfg.get('enable_content_analysis'))}")
    output_fn(f"  duplicate detection: {on_off(cfg.get('enable_duplicate_detection'))}"
              f"    semantic categories: {on_off(cfg.get('enable_semantic_categories'))}")
    output_fn(f"  auto git: {on_off(cfg.get('auto_git'))}"
              f"    router: {on_off((cfg.get('router') or {}).get('enabled'))}"
              f"    phone pull: {on_off((cfg.get('phone_pull') or {}).get('auto_pull'))}")
    output_fn("═" * 60)


def write_config(path: str, cfg: Dict, output_fn=None) -> Optional[str]:
    """Dump the config with spaces (never tabs), validate it by loading it
    through Config, and only then move it into place. Returns an error
    message, or None on success. The previous file survives any failure."""
    path = Path(path)
    tmp = path.with_name(path.name + ".new")
    try:
        with open(tmp, "w") as f:
            f.write(
                "# config.yaml — written by './organize config'.\n"
                "# Run './organize config' again any time; you can also\n"
                "# edit this file by hand (SPACES for indentation, never\n"
                "# tabs).\n"
            )
            yaml.safe_dump(cfg, f, default_flow_style=False,
                           sort_keys=False, allow_unicode=True)
    except OSError as e:
        return f"could not write {tmp}: {e}"
    # Validate the exact bytes we are about to install (warnings such as
    # missing drives are report noise here, not failure).
    try:
        with redirect_stderr(io.StringIO()):
            Config(str(tmp))
    except Exception as e:  # any load failure means the new file is bad
        tmp.unlink(missing_ok=True)
        return f"the new settings don't validate: {e}"
    os.replace(tmp, path)
    return None


# ── the editor ─────────────────────────────────────────────────────────────

def run_editor(config_path: str, template_path: Optional[str] = None,
               input_fn=None, output_fn=None) -> int:
    """Walk the user through config.yaml section by section.

    Returns 0 when a new config was saved, 1 when nothing was written
    (cancelled, or the result failed validation)."""
    input_fn = input_fn if input_fn is not None else input
    output_fn = output_fn if output_fn is not None else print
    config_path = str(config_path)
    if not template_path:
        template_path = str(Path(config_path).parent / "config_template.yaml")
        if not os.path.exists(template_path):
            from file_organizer.config import find_project_root
            template_path = str(find_project_root() / "config_template.yaml")

    output_fn("")
    output_fn("organize config — change config.yaml section by section.")
    output_fn("I ask, you type; press Enter to keep what's there.")
    output_fn("Type x at any prompt to quit without saving, or s to save")
    output_fn("what you've changed so far and quit.")
    output_fn("")

    cfg = None
    try:
        problem = config_problem(config_path)
        if problem:
            output_fn(f"⚠  {problem}")
            output_fn("")
            if not confirm(
                "Start fresh with safe defaults (everything OFF) and set it up now?",
                input_fn, output_fn, default=True,
            ):
                output_fn("Nothing changed. Run './organize config' again")
                output_fn("when ready, or fix the file by hand (spaces,")
                output_fn("never tabs).")
                return 1
            try:
                cfg = starter_data(template_path)
            except ConfigError as e:
                output_fn(f"✗  {e}")
                return 1
            output_fn("Loaded safe starter settings (everything off —")
            output_fn("nothing moves or syncs until you turn it on).")
        else:
            with open(config_path, "r") as f:
                loaded = yaml.safe_load(f) or {}
            cfg = loaded["computer"] if isinstance(loaded.get("computer"), dict) \
                else loaded
            if not isinstance(cfg, dict):
                cfg = {}
            output_fn("Loaded your current config.yaml — anything you skip")
            output_fn("stays exactly as it is.")

        # ── the section walk ──
        cfg.setdefault("drives", {})
        edit_drives(cfg["drives"], input_fn, output_fn)

        cfg.setdefault("source_folders", [])
        edit_path_list(
            "Source folders",
            cfg["source_folders"],
            "Folders to scan — the 'organized' tree is built from these\n"
            "and duplicate detection looks inside them.",
            input_fn, output_fn,
            example="MAIN_DRIVE/Documents, MAIN_DRIVE/Pictures",
            drives=cfg.get("drives"),
        )

        cfg.setdefault("sync_pairs", [])
        edit_pairs("Sync pairs (both ways)", cfg["sync_pairs"],
                   False, input_fn, output_fn, drives=cfg.get("drives"))
        cfg.setdefault("one_way_pairs", [])
        edit_pairs("One-way backups", cfg["one_way_pairs"],
                   True, input_fn, output_fn, drives=cfg.get("drives"))

        _output_base(cfg, input_fn, output_fn)

        _toggle(
            cfg, "enable_folder_sync", "Folder sync",
            "Keeps the paired folders identical, both ways. Off = files\n"
            "are only organized, never copied between folders.",
            input_fn, output_fn,
        )
        _toggle(
            cfg, "enable_content_analysis", "Content analysis",
            "Groups files by the keywords found inside them (local\n"
            "analysis — no API needed).",
            input_fn, output_fn,
        )
        _toggle(
            cfg, "enable_duplicate_detection", "Duplicate detection",
            "Finds duplicate files in the source folders. CAREFUL —\n"
            "production runs DELETE the extras, so test first.",
            input_fn, output_fn,
        )
        _toggle(
            cfg, "enable_semantic_categories", "Semantic categories",
            "Sorts keyword groups into ~30 broad categories using a small\n"
            "local model (~80 MB, downloaded on first use).",
            input_fn, output_fn,
        )
        if _toggle(
            cfg, "auto_git", "Auto git",
            "Automatically runs git init + first commit in folders under\n"
            "auto_git_folders. Off is safer — preview with\n"
            "'./organize git-preview'.",
            input_fn, output_fn,
        ):
            cfg.setdefault("auto_git_folders", [])
            edit_path_list(
                "Auto-git folders",
                cfg["auto_git_folders"],
                "Any folder under these gets git init + .gitignore +\n"
                "first commit.",
                input_fn, output_fn, example="MAIN_DRIVE/Development",
                drives=cfg.get("drives"),
            )
        _toggle(
            cfg, "collapse_rename_families", "Collapse rename families",
            "Turns 'foo (1).png' download-copy families into one file,\n"
            "DELETING the losing copies. Off by default for safety.",
            input_fn, output_fn,
        )
        _toggle(
            cfg, "dedupe_download_copies", "Download-copy tidy-up",
            "Deletes foo_1 / foo (1) copies when an identical plain-named\n"
            "file exists. Only identical bytes are ever deleted.",
            input_fn, output_fn,
        )

        _router(cfg, input_fn, output_fn)
        _phone_pull(cfg, input_fn, output_fn)

        cfg.setdefault("exclude_patterns", [])
        edit_path_list(
            "Excluded names",
            cfg["exclude_patterns"],
            "Files or folders with these names are never copied, synced\n"
            "or linked. Built-in safety names (.git, node_modules,\n"
            "caches…) stay protected even if you delete them here — this\n"
            "list only ever ADDS.",
            input_fn, output_fn, example=".DS_Store, *.log",
            show_status=False,
        )

        _summarize(cfg, output_fn)
        if not confirm("Save these settings to config.yaml?",
                       input_fn, output_fn, default=True):
            output_fn("Nothing changed — config.yaml was left as it was.")
            return 1
        err = write_config(config_path, cfg, output_fn)
        if err:
            output_fn(f"✗  {err}")
            output_fn("Your previous config.yaml was left untouched.")
            return 1
        output_fn(f"✓  Saved {config_path}")
        output_fn("")
        output_fn("Drive check:")
        for name, path in (cfg.get("drives") or {}).items():
            output_fn(f"  {path_status(path)}   {name}: {path}")
        output_fn("")
        output_fn("Next: ./organize test     (dry run — shows what would happen)")
        output_fn("      ./organize auto     (the real production scan)")
        output_fn("      ./organize start    (run in the background, forever)")
        return 0
    except EditorSaveQuit:
        # 's' anywhere: save exactly what has been entered so far.
        if cfg is None:
            # Quit-save before anything was loaded (at the very first
            # question): save a safe starter with everything off.
            try:
                cfg = starter_data(template_path)
            except ConfigError as e:
                output_fn(f"✗  {e}")
                return 1
            output_fn("(Nothing configured yet — saving a starter with "
                      "everything off.)")
        err = write_config(config_path, cfg, output_fn)
        if err:
            output_fn(f"✗  {err}")
            output_fn("Your previous config.yaml was left untouched.")
            return 1
        output_fn(f"✓  Saved {config_path} (your changes so far) — exiting.")
        return 0
    except EditorAbort:
        output_fn("")
        output_fn("Cancelled — nothing was changed.")
        return 1

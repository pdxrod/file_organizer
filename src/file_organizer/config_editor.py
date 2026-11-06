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
import yaml
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any, Dict, List, Optional

from file_organizer.config import (
    Config,
    ConfigError,
    TEMPLATE_PLACEHOLDER_MARKERS,
    _check_no_tabs,
    starter_data,
)


class EditorAbort(Exception):
    """The user cancelled (Ctrl+C, EOF, 'x', or answered 'no' to saving)."""


class EditorSaveQuit(Exception):
    """The user typed 's' — save what has changed so far and exit."""


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


# ── low-level prompt helpers ───────────────────────────────────────────────

def ask(question: str, input_fn, output_fn=None, default: Any = "",
        allow_blank: bool = True, validate=None) -> str:
    """One line of input. Blank returns `default` (None means 'no answer').
    'x' (case-insensitive) quits the whole editor without saving;
    's' saves what has changed so far and exits.
    validate(value) may return an error string to reject the answer."""
    while True:
        try:
            raw = input_fn(f"{question} ")
        except (EOFError, KeyboardInterrupt):
            raise EditorAbort()
        value = raw.strip()
        low = value.lower()
        if low == "x":
            raise EditorAbort()
        if low == "s":
            raise EditorSaveQuit()
        if not value:
            if allow_blank:
                return default
            if output_fn:
                output_fn("  (please type something, x to quit, s to save "
                          "and quit, or Ctrl+C)")
            continue
        if validate:
            err = validate(value)
            if err:
                if output_fn:
                    output_fn(f"  {err}")
                continue
        return value


def confirm(question: str, input_fn, output_fn=None, default: bool = True) -> bool:
    """y/n question; blank returns the default, 'x' quits the editor,
    's' saves what has changed so far and exits."""
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            raw = input_fn(f"{question} {hint} ")
        except (EOFError, KeyboardInterrupt):
            raise EditorAbort()
        v = raw.strip().lower()
        if v == "x":
            raise EditorAbort()
        if v == "s":
            raise EditorSaveQuit()
        if not v:
            return default
        if v in ("y", "yes"):
            return True
        if v in ("n", "no"):
            return False
        if output_fn:
            output_fn("  (answer y or n, x to quit, s to save and quit)")


# ── path status (shared by every section that shows paths) ─────────────────

def path_status(path: str) -> str:
    """One short status word for a path, in plain language."""
    expanded = os.path.expanduser(str(path))
    if os.path.exists(expanded):
        return "✓ exists"
    low = expanded.lower()
    for marker in TEMPLATE_PLACEHOLDER_MARKERS:
        if marker in low:
            return f"✗ starter example — replace it with your real path"
    return "? not available right now (will be skipped if missing)"


# ── section editors ────────────────────────────────────────────────────────

def _section(output_fn, title: str, text: str) -> None:
    output_fn("")
    output_fn("═" * 60)
    output_fn(f"  {title}")
    output_fn("═" * 60)
    for line in text.splitlines():
        output_fn(f"  {line}")


def _pick_number(kind: str, entries, input_fn, output_fn) -> Optional[int]:
    """Which numbered entry to act on, for a bare 'e'/'r' with no number.
    Auto-picks when there is exactly one entry; blank cancels."""
    if not entries:
        output_fn("  (nothing here yet — press a to add one)")
        return None
    if len(entries) == 1:
        return 1
    answer = ask("  which number?", input_fn, output_fn, default=None)
    if answer is None:
        return None
    if not answer.isdigit():
        output_fn(f"  (type the {kind}'s number, e.g. 4 — or blank to skip)")
        return None
    return int(answer)


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
                new_name = ask(f"  name [{name}] (Enter keeps):",
                               input_fn, output_fn, default=name)
                new_name = new_name.strip().upper()
                if new_name != name and new_name in drives:
                    output_fn(f"  {new_name} already exists — keeping {name}")
                    new_name = name
                value = ask(f"  folder [{drives[name]}] (Enter keeps):",
                            input_fn, output_fn, default=drives[name])
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


def edit_path_list(title: str, items, explanation: str, input_fn, output_fn,
                   example: Optional[str] = None,
                   show_status: bool = True) -> List[str]:
    """Menu-driven editor for a list of paths (or patterns when
    show_status=False)."""
    if items is None:
        items = []
    _section(output_fn, title, explanation)
    if example:
        output_fn(f"  example: {example}")
    while True:
        output_fn("")
        if items:
            for i, item in enumerate(items, 1):
                status = f"  ({path_status(item)})" if show_status else ""
                output_fn(f"  {i}. {item}{status}")
        else:
            output_fn("  (none)")
        prompt = ("  [enter]=done, a=add, e=edit, r=remove, "
                  "x=quit, s=save+quit"
                  if items else "  [enter]=done, a=add, x=quit, s=save+quit")
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
            value = ask("  new folder:", input_fn, output_fn, default=None)
            if value is not None:
                items.append(value)
                output_fn(f"  added {value}")
            continue
        edit_num = remove_num = None
        if cmd[0] == "e" and cmd[1:].isdigit():
            edit_num = int(cmd[1:])
        elif cmd in ("e", "edit"):
            edit_num = _pick_number("entry", items, input_fn, output_fn)
        elif cmd[0] == "r" and cmd[1:].isdigit():
            remove_num = int(cmd[1:])
        elif cmd in ("r", "remove"):
            remove_num = _pick_number("entry", items, input_fn, output_fn)
        if edit_num is not None:
            if 1 <= edit_num <= len(items):
                value = ask(f"  new value for item {edit_num}:", input_fn,
                            output_fn, default=None)
                if value is not None:
                    items[edit_num - 1] = value
            else:
                output_fn(f"  there is no item {edit_num}")
            continue
        if remove_num is not None:
            if 1 <= remove_num <= len(items):
                removed = items.pop(remove_num - 1)
                output_fn(f"  removed {removed}")
            else:
                output_fn(f"  there is no item {remove_num}")
            continue
        output_fn("  (e edits an entry, e.g. e2 — or just e; "
                  "r removes; enter finishes)")
    return items


def edit_pairs(title: str, pairs, one_way: bool, input_fn, output_fn) -> List[Dict]:
    """Edit sync_pairs (one_way=False) or one_way_pairs (one_way=True)."""
    arrow = "→" if one_way else "⇄"
    if one_way:
        explanation = (
            "One-way backups (source → target): files only ever copy from\n"
            "the first folder to the second — a safe backup. The first\n"
            "folder is never changed."
        )
    else:
        explanation = (
            "Sync pairs: the two folders are kept identical, both ways.\n"
            "Missing files are copied across and the newest version wins\n"
            "when both changed. Order doesn't matter."
        )
    pairs = pairs if pairs is not None else []
    _section(output_fn, title, explanation)
    while True:
        output_fn("")
        if pairs:
            for i, pair in enumerate(pairs, 1):
                folders = list(pair.get("folders") or [])
                while len(folders) < 2:
                    folders.append("")
                a, b = folders[0], folders[1]
                output_fn(f"  {i}. {a}  {arrow}  {b}")
                output_fn(f"     ({path_status(a)})   ({path_status(b)})")
        else:
            output_fn("  (none)")
        prompt = ("  [enter]=done, a=add pair, e=edit, r=remove, "
                  "x=quit, s=save+quit"
                  if pairs else "  [enter]=done, a=add pair, "
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
            a = ask("  first folder:", input_fn, output_fn, default=None)
            if a is None:
                continue
            b = ask("  second folder:", input_fn, output_fn, default=None)
            if b is None:
                continue
            pairs.append({"folders": [a, b]})
            output_fn(f"  added: {a}  {arrow}  {b}")
            continue
        edit_num = remove_num = None
        if cmd[0] == "e" and cmd[1:].isdigit():
            edit_num = int(cmd[1:])
        elif cmd in ("e", "edit"):
            edit_num = _pick_number("pair", pairs, input_fn, output_fn)
        elif cmd[0] == "r" and cmd[1:].isdigit():
            remove_num = int(cmd[1:])
        elif cmd in ("r", "remove"):
            remove_num = _pick_number("pair", pairs, input_fn, output_fn)
        if edit_num is not None:
            if 1 <= edit_num <= len(pairs):
                folders = list(pairs[edit_num - 1].get("folders") or [])
                while len(folders) < 2:
                    folders.append("")
                a = ask(f"  first folder [{folders[0]}]:", input_fn,
                        output_fn, default=folders[0])
                b = ask(f"  second folder [{folders[1]}]:", input_fn,
                        output_fn, default=folders[1])
                pairs[edit_num - 1]["folders"] = [a, b]
            else:
                output_fn(f"  there is no pair {edit_num}")
            continue
        if remove_num is not None:
            if 1 <= remove_num <= len(pairs):
                removed = pairs.pop(remove_num - 1)
                output_fn(f"  removed pair {remove_num}")
            else:
                output_fn(f"  there is no pair {remove_num}")
            continue
        output_fn("  (e edits a pair, e.g. e1 — or just e; "
                  "r removes; enter finishes)")
    return pairs


def _toggle(mapping: Dict, key: str, title: str, explanation: str,
            input_fn, output_fn) -> bool:
    """Edit one on/off setting. Returns the new value."""
    current = bool(mapping.get(key))
    output_fn("")
    output_fn(f"{title}")
    output_fn(f"  {explanation.replace(chr(10), chr(10) + '  ')}")
    output_fn(f"  Currently: {'ON' if current else 'OFF'}")
    keep = confirm("  Keep it that way?", input_fn, output_fn, default=True)
    if not keep:
        current = not current
        mapping[key] = current
    return bool(current)


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
    value = ask("  folder for the organized tree [enter keeps current]:",
                input_fn, output_fn, default=current)
    if value in (None, "", "organized", "~/organized"):
        cfg["output_base"] = "organized"
    else:
        cfg["output_base"] = value


def _router(cfg: Dict, input_fn, output_fn) -> None:
    router = cfg.setdefault("router", {})
    on = _toggle(
        router, "enabled", "Smart router",
        "Moves files into category folders (political → ~/pol, music →\n"
        "~/Music, …) by reading each file's name and contents. It MOVES\n"
        "files, so it starts OFF — plan first with './organize route'.",
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
    output_fn("Where each category goes (press Enter to keep each one):")
    targets = dict(router.get("target_folders") or {})
    for name, desc in (
        ("political", "politics, geopolitics, culture war…"),
        ("ml_development", "AI/ML projects, papers, notes, code"),
        ("development", "software projects and dev notes"),
        ("pictures", "images and videos that are not political"),
        ("music", "music"),
        ("documents", "documents"),
        ("misc", "everything the router can't place"),
    ):
        current = targets.get(name, "")
        value = ask(f"  {name} ({desc}) [{current or 'not set'}]:",
                    input_fn, output_fn, default=current)
        targets[name] = value
    router["target_folders"] = targets

    router.setdefault("route_roots", [])
    edit_path_list(
        "Route roots",
        router["route_roots"],
        "Folders swept when you run './organize route'. Files there are\n"
        "classified once.",
        input_fn, output_fn, example="MAIN_DRIVE/misc",
    )
    router.setdefault("watch_folders", [])
    edit_path_list(
        "Watch folders",
        router["watch_folders"],
        "New files dropped here are routed automatically on every\n"
        "production cycle (./organize auto / start).",
        input_fn, output_fn, example="MAIN_DRIVE/Downloads",
    )

    deepseek = router.setdefault("deepseek", {})
    if _toggle(
        deepseek, "enabled", "DeepSeek classification",
        "Files the local rules can't place are sent to DeepSeek for a\n"
        "category. Needs an API key (a text file path, or the\n"
        "DEEPSEEK_API_KEY environment variable).",
        input_fn, output_fn,
    ):
        deepseek["api_key_path"] = ask(
            f"  API key file path [enter keeps: "
            f"{deepseek.get('api_key_path', '') or 'use env var'}]:",
            input_fn, output_fn, default=deepseek.get("api_key_path", ""),
        )


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
    ) or ""
    pp["remote_stage"] = ask(
        f"  what syncs back to the phone [{pp.get('remote_stage', '')}]:",
        input_fn, output_fn, default=pp.get("remote_stage", ""),
    ) or ""
    pp["adb_path"] = ask(
        f"  path to the adb tool [{pp.get('adb_path', '')}]:",
        input_fn, output_fn, default=pp.get("adb_path", ""),
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
        )

        cfg.setdefault("sync_pairs", [])
        edit_pairs("Sync pairs (both ways)", cfg["sync_pairs"],
                   False, input_fn, output_fn)
        cfg.setdefault("one_way_pairs", [])
        edit_pairs("One-way backups", cfg["one_way_pairs"],
                   True, input_fn, output_fn)

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
                input_fn, output_fn, example="MAIN_DRIVE/dev",
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

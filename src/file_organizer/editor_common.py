"""Prompt helpers shared by both guided config editors.

The computer editor (config_editor.py) and the phone editor
(phone_config_editor.py) use the same conversation conventions — Enter
keeps a value, 'e' edits, 'x' quits without saving, 's' saves and quits —
so those helpers live here. This module deliberately imports NO yaml and
NOTHING from file_organizer.config: on a phone (Termux) PyYAML is usually
missing, and config.py needs it, so the phone editor must be able to
import these helpers without triggering that import.

Everything is driven through injectable input_fn/output_fn so tests run
whole editor sessions with scripted answers and no terminal.
"""

import builtins
import os
import sys
from typing import Any, Dict, List, Optional


class EditorAbort(Exception):
    """The user cancelled (Ctrl+C, EOF, 'x', or answered 'no' to saving)."""


class EditorSaveQuit(Exception):
    """The user typed 's' — save what has changed so far and exit."""


# ── low-level prompt helpers ───────────────────────────────────────────────

def _input_with_prefill(prompt: str, prefill: str, input_fn=input) -> str:
    """input_fn(prompt) with `prefill` already on the line as editable
    text when possible, e.g.
        name [GOOGLE_DRIVE] (Enter keeps): GOOGLE_DRIVE▏
    On an interactive terminal (input_fn is the builtin input and stdin
    is a tty) the prefill arrives as real typed text: GNU readline builds
    (Termux, Homebrew, python.org) use the pre-input hook, and macOS's
    libedit builds — whose builtin input() ignores the readline module's
    hooks entirely — get the prefill pushed into the terminal input
    queue with TIOCSTI instead. Either way the user can edit it in place
    and Enter keeps it. Otherwise the prompt is plain — piped/scripted
    answers are taken as complete values.
    (Same idea as phone_sync._input_with_prefill, but the prefill is the
    whole current value here, not a prefix.)
    """
    if not prefill:
        return input_fn(prompt)
    if input_fn is builtins.input and sys.stdin.isatty():
        try:
            import readline

            if "libedit" in (readline.__doc__ or "").lower():
                # macOS libedit: the readline module's hooks are ignored
                # by the builtin input(), so push the prefill into the
                # terminal's input queue (TIOCSTI) — the kernel hands it
                # to the process exactly as if the user had typed it.
                # The tty would echo the injected bytes itself, and then
                # libedit redraws its buffer after the prompt — two
                # copies. Inject with ECHO off so only libedit's redraw
                # shows the text, right after the prompt, cursor after it.
                import fcntl
                import termios
                attrs = termios.tcgetattr(sys.stdin.fileno())
                try:
                    quiet = attrs[:]
                    quiet[3] &= ~termios.ECHO
                    termios.tcsetattr(sys.stdin.fileno(),
                                      termios.TCSADRAIN, quiet)
                    for byte in prefill.encode("utf-8"):
                        fcntl.ioctl(sys.stdin.fileno(),
                                    termios.TIOCSTI, bytes([byte]))
                finally:
                    termios.tcsetattr(sys.stdin.fileno(),
                                      termios.TCSADRAIN, attrs)
                return input_fn(prompt)

            # GNU readline: the pre-input hook inserts editable text.
            def _hook():
                readline.insert_text(prefill)
                readline.redisplay()

            hook_setter = (getattr(readline, "set_pre_input_hook", None)
                           or getattr(readline, "set_startup_hook", None))
            if hook_setter is not None:
                hook_setter(_hook)
                try:
                    return input_fn(prompt)
                finally:
                    hook_setter(None)
        except Exception:
            pass  # no readline/ioctl support — plain prompt
    return input_fn(prompt)


def ask(question: str, input_fn, output_fn=None, default: Any = "",
        allow_blank: bool = True, validate=None,
        prefill: Optional[str] = None) -> str:
    """One line of input. Blank returns `default` (None means 'no answer').
    'x' (case-insensitive) quits the whole editor without saving;
    's' saves what has changed so far and exits. `prefill` puts the
    current value on the line as editable text on a real terminal.
    validate(value) may return an error string to reject the answer."""
    while True:
        try:
            raw = _input_with_prefill(f"{question} ", prefill or "", input_fn)
        except (EOFError, KeyboardInterrupt):
            raise EditorAbort()
        value = raw.strip()
        low = value.lower()
        if low == "x":
            raise EditorAbort()
        if low == "s":
            raise EditorSaveQuit()
        if prefill:
            # 'x'/'s' typed right after the prefilled value also win,
            # like the phone's rename command.
            if low == (prefill + "x").lower():
                raise EditorAbort()
            if low == (prefill + "s").lower():
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

def _placeholder_markers():
    """The template placeholder markers, without importing config.py —
    that module needs PyYAML, which a phone may not have."""
    try:
        from file_organizer.config import TEMPLATE_PLACEHOLDER_MARKERS
        return TEMPLATE_PLACEHOLDER_MARKERS
    except Exception:
        return ("janedoe", "yourexternaldrive", "yourgoogledrive", "yourname")


def path_status(path: str) -> str:
    """One short status word for a path, in plain language."""
    expanded = os.path.expanduser(str(path))
    if os.path.exists(expanded):
        return "✓ exists"
    low = expanded.lower()
    for marker in _placeholder_markers():
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
                current = items[edit_num - 1]
                value = ask(f"  new value for item {edit_num} "
                            f"[{current}] (Enter keeps):", input_fn,
                            output_fn, default=current, prefill=current)
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
                        output_fn, default=folders[0], prefill=folders[0])
                b = ask(f"  second folder [{folders[1]}]:", input_fn,
                        output_fn, default=folders[1], prefill=folders[1])
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

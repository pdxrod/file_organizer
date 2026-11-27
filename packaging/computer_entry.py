#!/usr/bin/env python3
"""Entry point for the INSTALLED organize program (macOS / Windows).

A faithful port of the COMPUTER side of the repo's `organize` launcher,
adapted for a frozen (PyInstaller) bundle:

  * HERE is the folder the `organize` binary lives in; config.yaml,
    config_template.yaml, organized/, the log and phone_pull.sh all live
    there (see find_project_root's frozen branch in config.py).
  * The daemon is respawned as this same binary (no `python -m ...`).
  * The GUI and web UI run in-process (tkinter/flask are bundled).
  * The phone platform and Termux lifecycle are NOT part of the desktop
    install; phone-only subcommands get a pointer to the GitHub repo.

Keep the command tables in sync with the `organize` launcher.
"""

import os
import sys
import time
import signal
import shutil
import subprocess
from pathlib import Path

HERE = (Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent)

# Windows consoles default to a non-UTF-8 codepage; the program prints
# em-dashes, check marks and box-drawing characters.
if os.name == "nt":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

# ── command tables (computer side of the launcher) ─────────────────────────

COMPUTER_COMMANDS = {
    "sanity": ["--scan-once"],               # dry-run scan (safe)
    "test-real": ["--REAL", "--scan-once"],  # production one-shot
    "scan": ["--REAL", "--scan-once"],       # alias of test-real
    "auto": ["--REAL", "--scan-once"],       # production scan — just works
    "sync": ["--REAL", "--sync-only"],
    "dedupe": ["--REAL", "--dedupe-only"],
    "cleanup": ["--REAL", "--cleanup"],
    "git-preview": ["--git-preview"],
    "git-init": ["--git-init"],
    "collapse-renames": ["--collapse-renames"],
    "route": ["--route"],                     # plan files by name+content
    "route-real": ["--REAL", "--route"],
    "route-status": ["--route-status"],
    "route-undo": ["--REAL", "--route-undo"],
    "rename-by-content": ["--rename-by-content"],
    "rename-by-content-real": ["--REAL", "--rename-by-content"],
    "tidy": ["--tidy"],
    "tidy-real": ["--REAL", "--tidy"],
    "sort": ["--route"],
    "sort-real": ["--REAL", "--route"],
    "sort-status": ["--route-status"],
    "sort-undo": ["--REAL", "--route-undo"],
}

COMPUTER_PASSTHROUGH = (
    "--REAL", "--scan-once", "--sync-only", "--dedupe-only",
    "--git-preview", "--git-init", "--cleanup",
    "--route", "--route-status", "--route-undo",
    "--rename-by-content",
    "--tidy",
    "-v", "--verbose", "--config", "-h", "--help",
)

COMPUTER_LIFECYCLE = ("start", "stop", "restart", "status", "log", "clean",
                      "gui", "web", "pull", "push", "list", "setup", "config")

# Subcommands that exist only on the phone — explained instead of leaking
# the CLI's 'unrecognized arguments' error.
PHONE_ONLY_COMMANDS = frozenset({
    "rename", "stats", "make-config", "find-cloud-drives", "dry-run",
})

ADB_MODE_FLAGS = {"pull": [], "push": ["--push"], "list": ["--list"]}


def normalize_flag(token, known):
    """One flag/subcommand token → its canonical spelling (as in the
    launcher: case-insensitive, dash-optional)."""
    name = token.lstrip("-").lower()
    for canon in known:
        if canon.lstrip("-").lower() == name:
            return canon
    return token


def map_command(argv):
    """Map a leading subcommand to computer-CLI args.

    Returns (argv_for_cli, handled_here) where handled_here is the name of
    a lifecycle subcommand implemented in this entry point, or None.
    """
    if not argv:
        return argv, None
    commands = COMPUTER_COMMANDS
    lifecycle = COMPUTER_LIFECYCLE
    head, rest = argv[0], argv[1:]
    key = normalize_flag(head, list(commands) + list(lifecycle))
    if key in commands:
        mapped_rest = [normalize_flag(a, COMPUTER_PASSTHROUGH) for a in rest]
        return commands[key] + mapped_rest, None
    if key in lifecycle:
        mapped_rest = [normalize_flag(a, COMPUTER_PASSTHROUGH) for a in rest]
        return [key] + mapped_rest, key
    if key == "test":
        return [key], "test"
    if key.lstrip("-").lower() in PHONE_ONLY_COMMANDS:
        return [key], "phone-only"
    # Not a subcommand: a flag or value — pass through untouched.
    return argv, None


# ── paths and the CLI bridge ───────────────────────────────────────────────

def config_path() -> Path:
    """config.yaml lives next to the installed program."""
    return HERE / "config.yaml"


def run_cli(args):
    """Bridge to the file_organizer CLI, always with this install's config."""
    from file_organizer import cli
    argv = list(args)
    if not any(a == "--config" for a in argv):
        argv += ["--config", str(config_path())]
    cli.main(argv)


def config_edit():
    """Run the guided section-by-section config.yaml editor."""
    from file_organizer.config_editor import run_editor
    return run_editor(str(config_path()),
                      template_path=str(HERE / "config_template.yaml"))


def needs_config_editor() -> bool:
    """True when config.yaml is missing or invalid."""
    from file_organizer.config_editor import needs_editor
    return needs_editor(str(config_path()))


def pid_log():
    """(pid_file, log_file) from config.yaml, with safe local defaults."""
    from file_organizer.config import Config, resolve_log_path
    pid_file = str(HERE / ".file_organizer.pid")
    log_file = str(HERE / ".file_organizer.log")
    try:
        cfg = Config(str(config_path()))
        pid_file = cfg.data.get("pid_path") or pid_file
        log_file = resolve_log_path(str(config_path()))
    except Exception:
        pass
    pid_file = os.path.expanduser(pid_file)
    log_file = os.path.expanduser(log_file)
    Path(pid_file).parent.mkdir(parents=True, exist_ok=True)
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    return pid_file, log_file


def ps_snapshot():
    """[(pid:int, command:str)] for all running processes (best effort)."""
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/fo", "csv"],
                capture_output=True, text=True,
            ).stdout
            rows = []
            for line in out.splitlines()[1:]:
                parts = line.strip().strip('"').split('","')
                if len(parts) >= 2:
                    try:
                        rows.append((int(parts[1]), parts[0]))
                    except ValueError:
                        pass
            return rows
        out = subprocess.run(
            ["ps", "-axo", "pid=,command="], capture_output=True, text=True
        ).stdout
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_s, _, cmd = line.partition(" ")
        try:
            rows.append((int(pid_s.strip()), cmd.strip()))
        except ValueError:
            pass
    return rows


def kill_pid(pid, log):
    """SIGTERM then SIGKILL a pid. Returns True if it was running."""
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(1)
        try:
            os.kill(pid, 0)
            if os.name == "nt":
                subprocess.run(["taskkill", "/pid", str(pid), "/f"],
                               capture_output=True, text=True)
                log(f"  Force-killed PID {pid}")
            else:
                os.kill(pid, signal.SIGKILL)
                log(f"  Force-killed PID {pid}")
        except OSError:
            log(f"  Stopped PID {pid}")
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def _is_daemon_cmd(cmd):
    """The installed daemon IS this binary, so it shows as 'organize';
    the production signature is the --REAL flag."""
    return "organize" in cmd and "--REAL" in cmd


# ── lifecycle: start/stop/restart/status/log ───────────────────────────────

def computer_start(log):
    if needs_config_editor():
        log("")
        log("config.yaml is missing or invalid — let's set it up first.")
        config_edit()
        if needs_config_editor():
            log("")
            log("config.yaml still needs work. Run 'organize config',")
            log("then 'organize start' again.")
            raise SystemExit(1)

    pid_file, log_file = pid_log()
    from file_organizer.single_instance import read_holder
    holder = read_holder(pid_file)
    if holder:
        log(f"File organizer is already running (PID {holder}) —")
        log("'organize stop' stops it. Only one may run at a time.")
        raise SystemExit(0)
    proc = subprocess.Popen(
        [sys.executable, "--REAL", "--config", str(config_path())],
        cwd=str(HERE),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=(os.name != "nt"),
    )
    Path(pid_file).write_text(str(proc.pid))
    time.sleep(1.5)
    if proc.poll() is None:
        log(f"File organizer started in PRODUCTION MODE (PID {proc.pid})")
        log(f"Log file: {log_file}")
    else:
        log("ERROR: File organizer failed to start!")
        log(f"Check the log file for details: {log_file}")
        Path(pid_file).unlink(missing_ok=True)
        raise SystemExit(1)


def computer_stop(log):
    pid_file, _ = pid_log()
    stopped = 0
    if os.path.exists(pid_file):
        try:
            pid = int(Path(pid_file).read_text().strip())
        except (ValueError, OSError):
            pid = None
        if pid and kill_pid(pid, log):
            stopped += 1
        elif pid is None:
            log("Stale PID file — removing")
        Path(pid_file).unlink(missing_ok=True)

    me = os.getpid()
    for pid, cmd in ps_snapshot():
        if pid == me:
            continue
        if _is_daemon_cmd(cmd):
            if kill_pid(pid, log):
                stopped += 1

    # Kill stuck rsync children on cloud mounts (as the launcher does).
    for pid, cmd in ps_snapshot():
        if "rsync" in cmd and any(
            marker in cmd
            for marker in ("ProtonDrive", "GoogleDrive", "PASSPORT")
        ):
            try:
                os.kill(pid, signal.SIGKILL)
                log(f"  Killed stuck rsync (PID {pid})")
                stopped += 1
            except OSError:
                pass

    if stopped == 0:
        log("No File Organizer processes found")


def computer_status(log):
    pid_file, log_file = pid_log()
    running = False
    if os.path.exists(pid_file):
        try:
            pid = int(Path(pid_file).read_text().strip())
            os.kill(pid, 0)
            log(f"Background program is running (PID {pid})")
            log(f"  Log file: {log_file}")
            running = True
        except (OSError, ValueError):
            log("PID file exists but the process is not running (stale)")
            Path(pid_file).unlink(missing_ok=True)

    me = os.getpid()
    for pid, cmd in ps_snapshot():
        if pid != me and _is_daemon_cmd(cmd):
            log(f"Process running (PID {pid}): {cmd[:80]}")
            running = True

    if not running:
        log("File Organizer is not running")


def _stdin_ready():
    """True when a typed key is already waiting (POSIX tty / Windows)."""
    try:
        import select
        readable, _, _ = select.select([sys.stdin], [], [], 0)
        return bool(readable)
    except (OSError, ValueError):
        try:
            import msvcrt  # Windows console
            return bool(msvcrt.kbhit())
        except (ImportError, OSError):
            return False


def tail_log(path, log):
    """Print the last few lines of a log then follow it (q to stop)."""
    p = Path(path)
    if not p.exists():
        log(f"Log file not found: {path}")
        return
    interactive = sys.stdin.isatty()
    try:
        with open(p, "r", errors="replace") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4000))
            sys.stdout.write(f.read())
        if interactive:
            log("")
            log("Following the log — type q and press Enter to stop.")
        while True:
            if interactive and _stdin_ready():
                if sys.stdin.read(1) in ("q", "Q"):
                    break
            with open(p, "r", errors="replace") as f:
                f.seek(0, 2)
                chunk = f.read()
            if chunk:
                sys.stdout.write(chunk)
                sys.stdout.flush()
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    log("")


# ── GUI and web UI (in-process — tkinter/flask are bundled) ────────────────

def computer_gui(log):
    try:
        import tkinter
        if tkinter.TkVersion < 8.6:
            raise ImportError("Tk too old")
        from file_organizer.desktop_app import OrganizerGUI
        OrganizerGUI().run()
        return 0
    except Exception as exc:  # noqa: BLE001 — broken Tk / missing GUI deps
        log(
            "The desktop GUI could not start (needs Tk 8.6 or newer): "
            f"{exc}\n"
            "\nEverything else works — the GUI is one of several ways to\n"
            "run the program. Use 'organize' (guided setup), 'organize\n"
            "auto', or 'organize web' for a browser UI."
        )
        return 1


def computer_web(log):
    try:
        from file_organizer import web_app
        from werkzeug.serving import make_server
    except Exception as exc:  # noqa: BLE001 — flask missing from the bundle
        log(f"The web UI could not start: {exc}")
        return 1
    port = 5000
    try:
        server = make_server("0.0.0.0", port, web_app.app, threaded=True)
    except OSError as exc:
        log(
            f"Cannot start the web UI on port {port}: {exc}\n"
            "\nOn macOS, AirPlay Receiver often claims port 5000. Either\n"
            "turn it off (System Settings > General > AirDrop & Handoff >\n"
            "AirPlay Receiver), or pick another port in the source.\n"
        )
        return 1

    lan = web_app._lan_ip()
    log("File Organizer web UI — view the organized folder and run scans",
        )
    log("from a phone or tablet on the same Wi-Fi network:\n")
    log(f"  this computer:  http://127.0.0.1:{port}")
    if lan and lan != "127.0.0.1":
        log(f"  phone/tablet:   http://{lan}:{port}")
    log("\nIf the page hangs, use the other address — VPN/tunnel addresses\n"
        "(utun, Tailscale, ...) are not reachable from a browser.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


# ── phone over USB (adb) ───────────────────────────────────────────────────

def computer_pull_push_list(log, argv):
    """'pull' runs the in-process Python puller; 'push'/'list' delegate to
    phone_pull.sh (needs bash — Git Bash on Windows)."""
    if argv and argv[0] == "pull":
        try:
            from file_organizer.phone_pull import Puller
            cfg = {}
            try:
                from file_organizer.config import Config
                cfg = Config(str(config_path())).raw_config
            except Exception:
                pass  # no/partial config — Puller falls back to defaults
            ok = Puller(cfg).pull_all()
            return 0 if ok else 1
        except Exception as exc:  # noqa: BLE001 — clean adb failure
            log(f"Phone pull failed: {exc}")
            return 1
    script = HERE / "phone_pull.sh"
    if not script.exists():
        log(f"Phone sync tool not found: {script}")
        raise SystemExit(1)
    bash = shutil.which("bash")
    if bash is None:
        log("push/list need the bash shell. Install Git for Windows "
            "(gitforwindows.org) or WSL, then run this again.")
        raise SystemExit(1)
    flags = ADB_MODE_FLAGS.get(argv[0], [])
    subprocess.run([bash, str(script)] + flags + list(argv[1:]))


# ── setup / clean / test ───────────────────────────────────────────────────

def computer_setup(log):
    if needs_config_editor():
        computer_config_edit_walk(log)
    out_dir = computer_output_base()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    log("")
    log(f"Soft-link folder (replaces ~/organized): {out_dir}")
    log("Next steps:")
    log("  1. Change any setting: organize config (guided, section by section).")
    log(f"     - Or edit {config_path()} (TextEdit/Notepad) — spaces, never tabs.")
    log("  2. Dry-run test:   organize test")
    log("  3. Production run: organize test-real")
    log("  4. Run in the background: organize start")


def computer_config_edit_walk(log):
    """The guided editor with a friendly header (used by setup)."""
    log("")
    log("config.yaml is missing or invalid — let's set it up first.")
    config_edit()


def computer_output_base():
    """The configured organized folder: inside the install folder by
    default, or wherever output_base points."""
    from file_organizer.config import Config
    try:
        cfg = Config(str(config_path()))
        return Path(cfg.data.get("output_base") or HERE / "organized")
    except Exception:
        return HERE / "organized"


def computer_clean(log):
    """Wipe the organized folder, log, and progress — then a fresh
    production scan."""
    for path in (
        computer_output_base(),
        pid_log()[1],
        HERE / ".file_organizer_progress.json",
        HERE / ".file_organizer_discovered_categories.json",
        Path.home() / ".file_organizer_progress.json",
        Path.home() / ".file_organizer_discovered_categories.json",
    ):
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
    log("Cleaned. Starting fresh production scan...")
    run_cli(COMPUTER_COMMANDS["test-real"])


# ── usage ──────────────────────────────────────────────────────────────────

def usage():
    lines = [
        "",
        "organize — one program for your phone and your computer",
        "",
        "This device looks like a computer (macOS/Linux/Windows).",
        "",
        "╔══════════════════════════════════════════════════════╗",
        "║  START HERE — run this one command:                  ║",
        "║                                                      ║",
        "║      organize auto                                  ║",
        "║                                                      ║",
        "║  auto does everything (see below). Running it again   ║",
        "║  is safe — already-done work is skipped instantly.    ║",
        "╚══════════════════════════════════════════════════════╝",
        "",
        "auto on this computer — one production scan: pulls phone files",
        "over USB first (when a phone is plugged in), then builds the",
        "'organized' soft-link folder, syncs folders with Proton Drive/",
        "Google Drive/external drives, runs auto-git, and sorts new files",
        "from your watch folders into their buckets. One command — it",
        "just works.",
        "",
        "New here? Run 'organize sanity' once first (dry-run, changes",
        "nothing), 'organize setup' for a guided first run, or",
        "'organize test' for the guided end-to-end test.",
        "",
        "All commands for THIS device (computer):",
        "  organize auto          ★ production scan — just works",
        "  organize test          ★ guided end-to-end test (with the phone)",
        "  organize sanity        dry-run scan — safe, changes nothing",
        "  organize test-real     one production scan (alias: scan)",
        "  organize start         run in the background, repeatedly",
        "  organize stop          stop the background program",
        "  organize restart       stop, wait, start again",
        "  organize status        is it running?",
        "  organize version       print the version number",
        "  organize log           follow the log file (q to stop)",
        "  organize sync          sync folders only",
        "  organize dedupe        remove duplicate files only",
        "  organize cleanup       remove broken/stale symlinks",
        "  organize clean         wipe the organized folder + log, fresh scan",
        "  organize git-preview   which folders would get 'git init'",
        "  organize git-init      run auto-git for real",
        "  organize collapse-renames  plan 'foo (1).png' family collapse",
        "  organize route         PLAN where files go (by name + content)",
        "  organize route-real    do it — actually move files into place",
        "  organize route-status  show the plan / decisions so far",
        "  organize route-undo    put the last moved files back",
        "  organize rename-by-content  rename generic-named images",
        "                              from their OCR text (plan only)",
        "  organize rename-by-content-real  do it — actually rename them",
        "  organize tidy           ONE command: OCR renames + year",
        "                              subfolders + folder tidy + Music",
        "                              mirror to Proton Drive (plan)",
        "  organize tidy-real      do it — apply everything at once",
        "  (sort, sort-real, sort-status, sort-undo are the same commands)",
        "  organize setup         first-time setup (config + report)",
        "  organize config        change config.yaml section by section —",
        "                           a guided editor: Enter keeps a value, e",
        "                           edits (a drive's name AND folder), x",
        "                           quits without saving, s saves and quits",
        "  organize gui           desktop app      organize web   web UI",
        "  organize pull|push|list   phone sync over USB (needs adb)",
        "",
        "Flags: --REAL means 'actually do it' — without it, commands only",
        "  plan. Others: scan-once, sync-only, dedupe-only, git-preview,",
        "  git-init, cleanup, collapse-renames, route, route-status,",
        "  route-undo, rename-by-content, tidy, config FILE, verbose",
        "",
        "USB debugging (phone → computer over USB):",
        "  1. Phone: Settings → About phone → tap 'Build number' 7 times",
        "     to unlock Developer options.",
        "  2. Enable Developer options → USB debugging, connect the phone,",
        "     then 'organize pull'.",
        "",
        "The phone program runs on Android/Termux from the GitHub repo:",
        "  github.com/pdxrod/file_organizer",
        "",
    ]
    return "\n".join(lines)


def phone_only_message(command):
    return (
        f"\n'{command}' is a phone-only command — it exists only on the\n"
        "phone (Android/Termux), not on this computer.\n"
        "\nThe phone program runs from the GitHub repo:\n"
        "  github.com/pdxrod/file_organizer\n"
        "\nRun 'organize' for this computer's commands.\n"
    )


# ── main ───────────────────────────────────────────────────────────────────

def main(argv=None):
    argv = list(sys.argv[1:]) if argv is None else list(argv)

    if not argv:
        # Plain `organize`: normally the usage text. When config.yaml is
        # missing or invalid, open the guided config editor instead.
        if needs_config_editor():
            sys.stderr.write(
                "\nThe config is missing or invalid, so let's fix that "
                "first —\nopening the guided config editor instead of the "
                "command list.\n(Run 'organize help' for the commands any "
                "time.)\n"
            )
            return config_edit()
        sys.stdout.write(usage())
        return 0

    if argv[0].lstrip("-").lower() in ("help", "h"):
        sys.stdout.write(usage())
        return 0

    if argv[0].lstrip("-").lower() == "version":
        from file_organizer import __version__
        sys.stdout.write(f"organize {__version__}\n")
        return 0

    mapped, handled = map_command(argv)
    log = lambda msg: print(msg, flush=True)

    if handled == "phone-only":
        sys.stderr.write(phone_only_message(mapped[0]))
        return 1

    if handled == "config":
        return config_edit()
    if handled == "test":
        from file_organizer.test_plan import run_computer_test
        return run_computer_test()
    if handled == "start":
        computer_start(log)
        return 0
    if handled == "stop":
        computer_stop(log)
        return 0
    if handled == "restart":
        computer_stop(log)
        time.sleep(2)
        computer_start(log)
        return 0
    if handled == "status":
        computer_status(log)
        return 0
    if handled == "log":
        tail_log(pid_log()[1], log)
        return 0
    if handled == "setup":
        computer_setup(log)
        return 0
    if handled == "clean":
        computer_clean(log)
        return 0
    if handled == "gui":
        return computer_gui(log)
    if handled == "web":
        return computer_web(log)
    if handled in ("pull", "push", "list"):
        return computer_pull_push_list(log, mapped)

    # plain CLI passthrough
    run_cli(mapped)
    return 0


if __name__ == "__main__":
    sys.exit(main())

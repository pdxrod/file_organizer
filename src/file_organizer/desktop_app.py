#!/usr/bin/env python3
"""Desktop GUI wrapper for file_organizer using Tkinter."""

import sys, os, shutil, threading, subprocess
from pathlib import Path

# Add src/ to path so we can import the package
sys.path.insert(0, str(Path(__file__).parent.parent))

# config.yaml lives at the project root (next to organize)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

try:
    from file_organizer.config import resolve_log_path
except ImportError:
    resolve_log_path = None

try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox, filedialog
except ImportError:
    print("Tkinter not available — GUI requires Python with tkinter support.")
    print("Install: brew install python-tk@3.x (macOS) or apt install python3-tk (Linux)")
    sys.exit(1)

# Apple's bundled /usr/bin/python3 ships Tk 8.5 (2010-era Aqua Tk), which
# draws an empty black window and burns CPU on modern macOS. If this
# process was launched directly with such an interpreter, re-launch under
# a newer python3 (Tk 8.6+). './organize gui' already picks one; this
# heals direct runs like 'python3 desktop_app.py'.
if tk.TkVersion < 8.6 and sys.platform == "darwin":
    _relaunch = None
    for _name in ("python3.14", "python3.13", "python3.12",
                  "python3.11", "python3.10"):
        _exe = shutil.which(_name)
        if not _exe or _exe == sys.executable:
            continue
        try:
            _proc = subprocess.run(
                [_exe, "-c",
                 "import tkinter,sys;sys.exit(0 if tkinter.TkVersion>=8.6 else 1)"],
                capture_output=True, timeout=20,
            )
        except Exception:
            continue
        if _proc.returncode == 0:
            _relaunch = _exe
            break
    if _relaunch:
        # flush=True: os.execv discards buffered stdout, so the message
        # must be written before the exec replaces this process.
        print(f"(re-launching with {_relaunch} — this python3's Tk "
              f"{tk.TkVersion} draws a black window on modern macOS)",
              flush=True)
        os.execv(_relaunch,
                 [_relaunch, str(Path(__file__).resolve())] + sys.argv[1:])
    print(
        "This python3's Tk is too old for the GUI (it draws an empty\n"
        "black window on modern macOS) and no python3 with Tk 8.6+\n"
        "was found.\n"
        "\n"
        "Fix it with Homebrew, then run './organize gui' again:\n"
        "  brew install python-tk@3.14\n"
    )
    sys.exit(1)


class OrganizerGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("File Organizer v2")
        self.root.geometry("800x600")
        self._running = False
        self._daemon_thread = None
        self._build_ui()

    def _build_ui(self):
        # Menu bar
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Load Config...", command=self._load_config)
        file_menu.add_command(label="Edit Config", command=self._edit_config)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        actions_menu = tk.Menu(menubar, tearoff=0)
        actions_menu.add_command(label="Test Run (Dry Run)", command=self._test_run)
        actions_menu.add_command(label="Run Full Cycle", command=self._full_cycle)
        actions_menu.add_command(label="Sync Only", command=self._sync_only)
        actions_menu.add_separator()
        actions_menu.add_command(label="Start Daemon", command=self._start_daemon)
        actions_menu.add_command(label="Stop Daemon", command=self._stop_daemon)
        menubar.add_cascade(label="Actions", menu=actions_menu)

        # Main frame
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # Status bar
        self.status_var = tk.StringVar(value="Ready.")
        status = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status.pack(side=tk.BOTTOM, fill=tk.X)

        # Config info
        config_frame = ttk.LabelFrame(main, text="Configuration", padding=5)
        config_frame.pack(fill=tk.X, pady=(0, 10))

        self.config_label = ttk.Label(config_frame, text="No config loaded.")
        self.config_label.pack(anchor=tk.W)

        # Control buttons
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=5)

        ttk.Button(btn_frame, text="Test Run", command=self._test_run).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Full Cycle", command=self._full_cycle).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Sync Only", command=self._sync_only).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Start Daemon", command=self._start_daemon).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Stop", command=self._stop_daemon).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Exit", command=self._exit_app).pack(side=tk.RIGHT, padx=2)

        # Log output
        log_frame = ttk.LabelFrame(main, text="Log", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=20, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Try to load default config, then start the log auto-refresh
        self._auto_load_config()
        self._refresh_log()

    def _default_config(self):
        """config.yaml lives at the project root (next to organize)."""
        candidates = [PROJECT_ROOT / "config.yaml", Path.cwd() / "config.yaml"]
        return next((p for p in candidates if p.exists()), PROJECT_ROOT / "config.yaml")

    def _auto_load_config(self):
        config_path = self._default_config()
        if config_path.exists():
            self.config_label.config(text=f"Config: {config_path.resolve()}")
        else:
            self.config_label.config(text="No config.yaml found — File > Load Config")

    def _load_config(self):
        path = filedialog.askopenfilename(
            title="Select config.yaml",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")]
        )
        if path:
            self.config_label.config(text=f"Config: {path}")

    def _edit_config(self):
        config_path = self._default_config()
        if not config_path.exists():
            messagebox.showwarning("No Config", "No config.yaml found. Create one first.")
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(config_path)])
        elif sys.platform == "win32":
            os.startfile(str(config_path))
        else:
            subprocess.Popen(["xdg-open", str(config_path)])

    def _log(self, message):
        # _refresh_log leaves the widget DISABLED (read-only); enable it
        # around the insert or the write silently raises.
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self.root.update_idletasks()

    def _refresh_log(self):
        """Auto-refresh the log viewer every 3 seconds."""
        try:
            log_path = resolve_log_path() if resolve_log_path else str(
                PROJECT_ROOT / ".file_organizer.log")
            if os.path.exists(log_path):
                with open(log_path, 'r') as f:
                    # Read last 500 lines
                    lines = f.readlines()
                    content = ''.join(lines[-200:])
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.delete(1.0, tk.END)
                self.log_text.insert(tk.END, content)
                self.log_text.see(tk.END)
                self.log_text.configure(state=tk.DISABLED)
        except Exception:
            pass
        self.root.after(3000, self._refresh_log)

    def _run_command(self, args):
        self._log(f"Running: python -m file_organizer {' '.join(args)}")
        self._running = True
        self.status_var.set("Running...")

        def target():
            try:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "file_organizer"] + args,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    cwd=str(Path(__file__).parent.parent),
                    text=True, bufsize=1
                )
                for line in proc.stdout:
                    self.root.after(0, lambda l=line: self._log(l.rstrip()))
                proc.wait()
                self.root.after(0, lambda: self.status_var.set("Done."))
            except Exception as e:
                self.root.after(0, lambda: self._log(f"Error: {e}"))
                self.root.after(0, lambda: self.status_var.set("Error."))
            finally:
                self._running = False

        t = threading.Thread(target=target, daemon=True)
        t.start()

    def _test_run(self):
        self._run_command(["--scan-once"])

    def _full_cycle(self):
        if not messagebox.askyesno("Confirm", "Run FULL CYCLE in production mode? This modifies real files."):
            return
        self._run_command(["--REAL", "--scan-once"])

    def _sync_only(self):
        if not messagebox.askyesno("Confirm", "Run SYNC ONLY in production mode?"):
            return
        self._run_command(["--REAL", "--sync-only"])

    def _start_daemon(self):
        if not messagebox.askyesno("Confirm", "Start daemon in PRODUCTION mode? Runs continuously."):
            return
        self._run_command(["--REAL"])

    def _exit_app(self):
        """Kill everything and exit."""
        self._running = False
        # Kill any running file_organizer processes
        try:
            subprocess.run(["pkill", "-f", "python.*file_organizer"], capture_output=True)
        except Exception:
            pass
        self.root.destroy()

    def _stop_daemon(self):
        self._log("Stopping daemon...")
        self.status_var.set("Stopping...")
        killed = 0
        try:
            result = subprocess.run(
                ["pgrep", "-f", "python.*file_organizer"],
                capture_output=True, text=True
            )
            for pid in result.stdout.strip().split("\n"):
                pid = pid.strip()
                if pid:
                    try:
                        os.kill(int(pid), 15)  # SIGTERM
                        self._log(f"Sent SIGTERM to PID {pid}")
                        killed += 1
                    except OSError:
                        pass
        except Exception as e:
            self._log(f"Error stopping daemon: {e}")
        self._running = False
        if killed:
            self.status_var.set(f"Stopped {killed} process(es).")
            self._log(f"Daemon stopped ({killed} process{'es' if killed > 1 else ''}).")
        else:
            self.status_var.set("No daemon running.")
            self._log("No file_organizer daemon found.")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    gui = OrganizerGUI()
    gui.run()

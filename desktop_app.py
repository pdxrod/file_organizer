#!/usr/bin/env python3
"""Desktop GUI wrapper for file_organizer using Tkinter."""

import sys, os, threading, subprocess
from pathlib import Path

# Add parent to path so we can import
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox, filedialog
except ImportError:
    print("Tkinter not available — GUI requires Python with tkinter support.")
    print("Install: brew install python-tk@3.x (macOS) or apt install python3-tk (Linux)")
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

        # Log output
        log_frame = ttk.LabelFrame(main, text="Log", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=20, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Try to load default config
        self._auto_load_config()

    def _auto_load_config(self):
        config_path = Path("config.yaml")
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
        config_path = Path("config.yaml")
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
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()

    def _run_command(self, args):
        self._log(f"Running: python -m file_organizer {' '.join(args)}")
        self._running = True
        self.status_var.set("Running...")

        def target():
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "file_organizer"] + args,
                    capture_output=True, text=True, timeout=3600,
                    cwd=str(Path(__file__).parent.parent)
                )
                self.root.after(0, lambda: self._log(result.stdout))
                if result.stderr:
                    self.root.after(0, lambda: self._log("[stderr] " + result.stderr))
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

    def _stop_daemon(self):
        self._running = False
        self.status_var.set("Stopping...")
        self._log("Daemon stop requested.")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    gui = OrganizerGUI()
    gui.run()

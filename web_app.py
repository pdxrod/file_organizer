#!/usr/bin/env python3
"""Mobile web UI for File Organizer — access from phone/tablet on same network."""

import os, sys, json, subprocess, threading
from pathlib import Path
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)
ORGANIZED = Path.home() / "organized"
LOG_FILE = Path.home() / ".file_organizer.log"
PROJECT_DIR = Path(__file__).parent

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def api_status():
    """Return folder stats and running state."""
    categories = []
    if ORGANIZED.exists():
        for d in sorted(ORGANIZED.iterdir()):
            if d.is_dir():
                links = sum(1 for _ in d.rglob("*") if _.is_symlink())
                if links > 0:
                    categories.append({"name": d.name, "links": links})
    categories.sort(key=lambda x: x["links"], reverse=True)
    return jsonify({
        "total_categories": len(categories),
        "total_links": sum(c["links"] for c in categories),
        "categories": categories[:100],  # top 100
        "running": _is_running(),
    })

@app.route("/api/category/<name>")
def api_category(name):
    """List files in a category folder."""
    folder = ORGANIZED / name
    if not folder.exists():
        return jsonify({"error": "not found"}), 404
    files = []
    for link in sorted(folder.iterdir()):
        if link.is_symlink():
            try:
                target = os.readlink(link)
                files.append({"name": link.name, "target": target})
            except OSError:
                pass
    return jsonify({"name": name, "files": files})

@app.route("/api/log")
def api_log():
    """Return last 100 lines of log."""
    if LOG_FILE.exists():
        with open(LOG_FILE) as f:
            lines = f.readlines()[-100:]
        return jsonify({"log": "".join(lines)})
    return jsonify({"log": "No log yet."})

@app.route("/api/scan", methods=["POST"])
def api_scan():
    """Trigger a scan-once in production mode."""
    if _is_running():
        return jsonify({"error": "Already running"}), 409
    def run():
        subprocess.run(
            [sys.executable, "-m", "file_organizer", "--REAL", "--scan-once"],
            cwd=str(PROJECT_DIR),
        )
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return jsonify({"status": "started"})

def _is_running():
    try:
        result = subprocess.run(["pgrep", "-f", "file_organizer"], capture_output=True, text=True)
        return bool(result.stdout.strip())
    except Exception:
        return False

if __name__ == "__main__":
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    print(f"\n  Mobile UI: http://{local_ip}:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=False)

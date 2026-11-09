#!/usr/bin/env python3
"""Mobile web UI for File Organizer — access from phone/tablet on same network."""

import os, sys, json, subprocess, threading
from pathlib import Path

# The package lives at src/file_organizer; src/ must be importable and the
# HTML template lives at src/templates/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from flask import Flask, render_template, jsonify, request

try:
    from file_organizer.config import resolve_log_path
except ImportError:
    resolve_log_path = None

app = Flask(__name__, template_folder=str(Path(__file__).resolve().parent.parent / "templates"))


def _organized_folder() -> Path:
    """Item 15: the organized soft-link folder — inside the file_organizer
    folder by default, or wherever output_base points."""
    try:
        from file_organizer.config import Config, find_project_root
        cfg = Config(str(find_project_root() / "config.yaml"))
        return Path(cfg.data.get("output_base") or Path.home() / "organized")
    except Exception:
        return Path.home() / "organized"


ORGANIZED = _organized_folder()
LOG_FILE = Path(resolve_log_path() if resolve_log_path else str(
    Path(__file__).resolve().parent.parent.parent / ".file_organizer.log"))
SRC_DIR = Path(__file__).resolve().parent.parent

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
            cwd=str(SRC_DIR),
        )
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return jsonify({"status": "started"})

def _is_running():
    try:
        result = subprocess.run(["pgrep", "-f", "python.*file_organizer"], capture_output=True, text=True)
        return bool(result.stdout.strip())
    except Exception:
        return False

def _lan_ip():
    """Best physical-LAN IPv4 address to print as the browser URL.

    Werkzeug's own banner lists every interface, including VPN tunnels
    (utun*). Opening a tunnel address in a browser hangs: the request is
    routed into the tunnel and never answered. Ask the OS for the
    physical interface address first; fall back to the default-route
    trick (which is itself fooled by VPNs — that is why the OS is asked
    first).
    """
    import socket
    if sys.platform == "darwin":
        for dev in ("en0", "en1", "en2", "en3"):
            try:
                out = subprocess.run(
                    ["ipconfig", "getifaddr", dev],
                    capture_output=True, text=True, timeout=5,
                )
                ip = (out.stdout or "").strip()
                if ip:
                    return ip
            except Exception:
                pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    from werkzeug.serving import make_server

    port = 5000
    try:
        server = make_server("0.0.0.0", port, app, threaded=True)
    except OSError as exc:
        print(
            f"Cannot start the web UI on port {port}: {exc}\n"
            "\n"
            "On macOS, AirPlay Receiver often claims port 5000. Either turn\n"
            "it off (System Settings > General > AirDrop & Handoff >\n"
            "AirPlay Receiver), or pick another port by editing PORT in\n"
            "web_app.py.\n"
        )
        sys.exit(1)

    lan = _lan_ip()
    print("File Organizer web UI — view the organized folder and run scans",
          flush=True)
    print("from a phone or tablet on the same Wi-Fi network:\n", flush=True)
    print(f"  this computer:  http://127.0.0.1:{port}", flush=True)
    if lan and lan != "127.0.0.1":
        print(f"  phone/tablet:   http://{lan}:{port}", flush=True)
    print(
        "\nIf the page hangs, use the other address — VPN/tunnel addresses\n"
        "(utun, Tailscale, ...) are not reachable from a browser.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

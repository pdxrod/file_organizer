#!/bin/bash
# ============================================================================
# manage_phone_daemon.sh — start, stop, status, and test the phone daemon.
#
# Usage:
#   ./manage_phone_daemon.sh start        Start daemon in background
#   ./manage_phone_daemon.sh stop         Stop the daemon
#   ./manage_phone_daemon.sh restart      Stop then start
#   ./manage_phone_daemon.sh status       Check if running
#   ./manage_phone_daemon.sh test         Dry-run scan (no files copied)
#   ./manage_phone_daemon.sh scan         One-shot real scan
#   ./manage_phone_daemon.sh log          Tail the log
#   ./manage_phone_daemon.sh stats        Show sync database statistics
#   ./manage_phone_daemon.sh find-proton  Locate Proton Drive folder
#   ./manage_phone_daemon.sh setup        First-time setup wizard
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DAEMON_SCRIPT="$SCRIPT_DIR/phone_daemon.py"
CONFIG_FILE="$SCRIPT_DIR/phone_daemon_config.yaml"

# Default log/pid/db paths — overridden by phone_daemon_config.yaml
LOG_FILE="$HOME/.phone_daemon.log"
PID_FILE="$HOME/.phone_daemon.pid"
DB_FILE="$HOME/.phone_daemon.db"

if [ -f "$CONFIG_FILE" ] && command -v python3 >/dev/null 2>&1; then
    eval "$(python3 - "$CONFIG_FILE" <<'PYEOF'
import sys, os, shlex
try:
    import yaml
except ImportError:
    sys.exit(0)
cfg = yaml.safe_load(open(sys.argv[1])) or {}
defaults = {
    "log_path": "~/.phone_daemon.log",
    "pid_path": "~/.phone_daemon.pid",
    "db_path": "~/.phone_daemon.db",
}
for key in ("log_path", "pid_path", "db_path"):
    val = str(cfg.get(key, defaults[key]))
    print("%s=%s" % (key.upper(), shlex.quote(os.path.expanduser(val))))
PYEOF
)"
fi

# Python — prefer python3 on macOS/Linux, python on Termux
if command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    echo "ERROR: Python not found. Install Python 3.8+ first."
    exit 1
fi

# ── Colours ──────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

banner() {
    echo -e "${CYAN}╔════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║        📱 Phone Daemon Manager         ║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════╝${NC}"
    echo
}

# ── Check config exists ──────────────────────────────────────────────────

check_config() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo -e "${YELLOW}No config found at $CONFIG_FILE${NC}"
        echo
        echo "Creating starter config from template..."
        if [ -f "$SCRIPT_DIR/phone_daemon_config.template.yaml" ]; then
            cp "$SCRIPT_DIR/phone_daemon_config.template.yaml" "$CONFIG_FILE"
            echo -e "${GREEN}✔ Created $CONFIG_FILE from template — includes common Android folders.${NC}"
            echo
            echo -e "${YELLOW}⚠  Review $CONFIG_FILE before running:${NC}"
            echo "  - source_directories lists DCIM/Pictures/Documents/Download plus app media"
            echo "  - Missing folders are skipped automatically — no need to trim the list"
            echo "  - On Android: target_directory = /storage/emulated/0/file_organizer_staging"
            echo "  - On macOS: run './manage_phone_daemon.sh find-proton' to locate Proton Drive"
        else
            echo -e "${RED}✗ Template not found. Run from the file_organizer directory.${NC}"
            exit 1
        fi
        echo
    fi
}

# ── Daemon running? ──────────────────────────────────────────────────────

is_running() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE" 2>/dev/null || true)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    # Also check via pgrep as fallback
    if pgrep -f "phone_daemon.py" > /dev/null 2>&1; then
        return 0
    fi
    return 1
}

# ── Commands ─────────────────────────────────────────────────────────────

cmd_start() {
    check_config

    if is_running; then
        echo -e "${YELLOW}⚠  Phone daemon is already running.${NC}"
        cmd_status
        return 1
    fi

    echo -e "${GREEN}Starting phone daemon in background…${NC}"
    cd "$SCRIPT_DIR"
    $PYTHON "$DAEMON_SCRIPT" --config "$CONFIG_FILE" --daemon

    sleep 2
    if is_running; then
        echo -e "${GREEN}✔ Daemon started successfully.${NC}"
        cmd_status
    else
        echo -e "${RED}✗ Daemon failed to start. Check log:${NC}"
        echo "  tail -f $LOG_FILE"
        return 1
    fi
}

cmd_stop() {
    if ! is_running; then
        echo -e "${YELLOW}Daemon is not running.${NC}"
        # Clean up stale PID file
        rm -f "$PID_FILE"
        return 0
    fi

    echo "Stopping phone daemon…"

    # Try graceful stop first
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE" 2>/dev/null || true)
        if [ -n "$pid" ]; then
            kill "$pid" 2>/dev/null || true
            # Wait up to 10 seconds
            for i in $(seq 1 10); do
                if ! kill -0 "$pid" 2>/dev/null; then
                    echo -e "${GREEN}✔ Daemon stopped (PID $pid)${NC}"
                    rm -f "$PID_FILE"
                    return 0
                fi
                sleep 1
            done
            # Force kill
            kill -9 "$pid" 2>/dev/null || true
            echo -e "${YELLOW}⚠  Force-killed PID $pid${NC}"
        fi
    fi

    # Also try via the script's own --stop
    $PYTHON "$DAEMON_SCRIPT" --config "$CONFIG_FILE" --stop 2>/dev/null || true

    rm -f "$PID_FILE"
    echo -e "${GREEN}✔ Daemon stopped.${NC}"
}

cmd_restart() {
    cmd_stop
    sleep 1
    cmd_start
}

cmd_status() {
    if is_running; then
        local pid=""
        if [ -f "$PID_FILE" ]; then
            pid=$(cat "$PID_FILE" 2>/dev/null || true)
        fi
        if [ -z "$pid" ]; then
            pid=$(pgrep -f "phone_daemon.py" | head -1)
        fi
        echo -e "${GREEN}● Phone daemon is RUNNING${NC} (PID: ${pid:-unknown})"

        # Show stats from DB
        if [ -f "$DB_FILE" ]; then
            echo
            $PYTHON "$DAEMON_SCRIPT" --config "$CONFIG_FILE" --stats 2>/dev/null || true
        fi
    else
        echo -e "${RED}○ Phone daemon is STOPPED${NC}"
    fi
}

cmd_test() {
    check_config
    echo -e "${CYAN}Running DRY-RUN scan (no files will be copied)…${NC}"
    echo
    cd "$SCRIPT_DIR"
    $PYTHON "$DAEMON_SCRIPT" --config "$CONFIG_FILE" --scan-once --dry-run --verbose
    echo
    echo -e "${GREEN}✔ Dry-run complete. Review the log above.${NC}"
    echo "  If everything looks right, run: ./manage_phone_daemon.sh scan"
}

cmd_scan() {
    check_config
    echo -e "${YELLOW}Running PRODUCTION scan (files WILL be copied)…${NC}"
    echo "  Press Ctrl+C to cancel within 3 seconds…"
    sleep 3
    cd "$SCRIPT_DIR"
    $PYTHON "$DAEMON_SCRIPT" --config "$CONFIG_FILE" --scan-once --verbose
    echo
    echo -e "${GREEN}✔ Scan complete.${NC}"
}

cmd_log() {
    if [ -f "$LOG_FILE" ]; then
        tail -f "$LOG_FILE"
    else
        echo "No log file yet at $LOG_FILE"
    fi
}

cmd_stats() {
    check_config
    cd "$SCRIPT_DIR"
    $PYTHON "$DAEMON_SCRIPT" --config "$CONFIG_FILE" --stats
}

cmd_find_proton() {
    echo -e "${CYAN}Searching for Proton Drive folder…${NC}"
    echo
    cd "$SCRIPT_DIR"
    $PYTHON "$DAEMON_SCRIPT" --find-proton
    echo
    echo "Tip: On macOS set that path as target_directory; on Android use"
    echo "     /storage/emulated/0/file_organizer_staging instead."
}

cmd_setup() {
    banner
    echo "This wizard helps you set up the phone daemon for the first time."
    echo

    # 1. Check Python
    echo -n "Python version: "
    $PYTHON --version
    echo

    # 2. Find Proton Drive
    echo "Searching for Proton Drive…"
    cd "$SCRIPT_DIR"
    $PYTHON "$DAEMON_SCRIPT" --find-proton
    echo

    # 3. Config
    if [ -f "$CONFIG_FILE" ]; then
        echo -e "${GREEN}✔ Config exists at $CONFIG_FILE${NC}"
    else
        cp "$SCRIPT_DIR/phone_daemon_config.template.yaml" "$CONFIG_FILE"
        echo -e "${GREEN}✔ Created $CONFIG_FILE${NC}"
    fi

    echo
    echo "── Setup complete ──"
    echo
    echo "Next steps:"
    echo "  1. Edit $CONFIG_FILE"
    echo "     - On Android: target_directory = /storage/emulated/0/file_organizer_staging"
    echo "     - On macOS: set the Proton Drive path (see find-proton)"
    echo "     - Adjust source_directories if needed"
    echo "  2. Run a dry-run test:  ./manage_phone_daemon.sh test"
    echo "  3. Run a real scan:     ./manage_phone_daemon.sh scan"
    echo "  4. Start the daemon:    ./manage_phone_daemon.sh start"
    echo
}

# ── Main dispatcher ──────────────────────────────────────────────────────

banner

case "${1:-}" in
    start)
        cmd_start
        ;;
    stop)
        cmd_stop
        ;;
    restart)
        cmd_restart
        ;;
    status)
        cmd_status
        ;;
    test|dry-run)
        cmd_test
        ;;
    scan|scan-once)
        cmd_scan
        ;;
    log|logs|tail)
        cmd_log
        ;;
    stats)
        cmd_stats
        ;;
    find-proton|find|locate)
        cmd_find_proton
        ;;
    setup|wizard)
        cmd_setup
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|test|scan|log|stats|find-proton|setup}"
        echo
        echo "Commands:"
        echo "  start         Start daemon in background"
        echo "  stop          Stop the daemon"
        echo "  restart       Stop then start"
        echo "  status        Check if daemon is running"
        echo "  test          Dry-run scan (safe — no files copied)"
        echo "  scan          One-shot production scan"
        echo "  log           Tail the log file"
        echo "  stats         Show sync database statistics"
        echo "  find-proton   Locate Proton Drive folder on this device"
        echo "  setup         First-time setup wizard"
        echo
        exit 1
        ;;
esac

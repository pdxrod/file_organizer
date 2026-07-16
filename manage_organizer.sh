#!/bin/bash
# Management script for File Organizer v2
# Usage: ./manage_organizer.sh {start|stop|restart|status|log|test|test-real|sync|dedupe|cleanup|gui}

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PACKAGE_NAME="file_organizer"
MAIN_SCRIPT="$SCRIPT_DIR/file_organizer.py"
LOG_FILE="$HOME/.file_organizer.log"
PID_FILE="/tmp/file_organizer.pid"

# Ensure we can import the package
export PYTHONPATH="$SCRIPT_DIR/..:$PYTHONPATH"
cd "$SCRIPT_DIR" || exit 1

# Helper: run python with the package
run_organizer() {
    python3 "$MAIN_SCRIPT" "$@"
}

case "$1" in
    start)
        echo "Starting File Organizer v2 in PRODUCTION MODE..."
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if ps -p "$PID" > /dev/null 2>&1; then
                echo "File Organizer is already running (PID: $PID)"
                exit 1
            fi
        fi

        echo "Validating configuration..."
        if [ ! -f "$SCRIPT_DIR/config.yaml" ]; then
            echo "No config.yaml found. Creating starter config..."
            run_organizer --config "$SCRIPT_DIR/config.yaml" --scan-once 2>&1 | head -10
            echo ""
            echo "A starter config.yaml has been created."
            echo "Please edit it before starting the daemon."
            exit 1
        fi

        # Start in background
        nohup python3 "$MAIN_SCRIPT" --REAL --config "$SCRIPT_DIR/config.yaml" > /dev/null 2>&1 &
        PID=$!
        echo $PID > "$PID_FILE"

        sleep 1
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "File Organizer v2 started in PRODUCTION MODE (PID: $PID)"
            echo "Log file: $LOG_FILE"
        else
            echo "ERROR: File Organizer failed to start!"
            echo "Check the log file for details: $LOG_FILE"
            rm -f "$PID_FILE"
            exit 1
        fi
        ;;

    stop)
        echo "Stopping all File Organizer processes..."
        STOPPED=0

        # Try PID file first
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if ps -p "$PID" > /dev/null 2>&1; then
                echo "Stopping daemon (PID: $PID)..."
                kill "$PID"
                sleep 1
                if ps -p "$PID" > /dev/null 2>&1; then
                    kill -9 "$PID" 2>/dev/null
                fi
                rm "$PID_FILE"
                echo "  Daemon stopped"
                STOPPED=1
            else
                rm "$PID_FILE"
            fi
        fi

        # Find and stop any remaining file_organizer processes
        PIDS=$(ps auxw | grep -i "file_organizer" | grep -v grep | grep -v manage_organizer | awk '{print $2}' || true)
        if [ -n "$PIDS" ]; then
            for PID in $PIDS; do
                if ps -p "$PID" > /dev/null 2>&1; then
                    ELAPSED=$(ps -p "$PID" -o etime= 2>/dev/null | xargs || echo "unknown")
                    echo "Stopping file_organizer (PID: $PID, running: $ELAPSED)..."
                    kill "$PID" 2>/dev/null
                    sleep 1
                    if ps -p "$PID" > /dev/null 2>&1; then
                        kill -9 "$PID" 2>/dev/null
                        echo "  Force stopped"
                    else
                        echo "  Stopped gracefully"
                    fi
                    STOPPED=1
                fi
            done
        fi

        # Kill stuck rsync child processes
        RSYNC_PIDS=$(ps auxw | grep "rsync" | grep -E "GoogleDrive|ProtonDrive|PASSPORT" | grep -v grep | awk '{print $2}' || true)
        if [ -n "$RSYNC_PIDS" ]; then
            for PID in $RSYNC_PIDS; do
                if ps -p "$PID" > /dev/null 2>&1; then
                    ELAPSED=$(ps -p "$PID" -o etime= 2>/dev/null | xargs || echo "unknown")
                    echo "Stopping stuck rsync (PID: $PID, running: $ELAPSED)..."
                    kill -9 "$PID" 2>/dev/null
                    echo "  Killed stuck rsync"
                    STOPPED=1
                fi
            done
        fi

        [ $STOPPED -eq 0 ] && echo "No File Organizer processes found"
        ;;

    restart)
        "$0" stop
        sleep 2
        "$0" start
        ;;

    status)
        RUNNING=0

        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if ps -p "$PID" > /dev/null 2>&1; then
                echo "Daemon is running (PID: $PID)"
                echo "  Log file: $LOG_FILE"
                RUNNING=1
            else
                echo "Daemon PID file exists but process not running (stale)"
                rm "$PID_FILE"
            fi
        fi

        PIDS=$(ps auxw | grep -i "file_organizer" | grep -v grep | grep -v manage_organizer | awk '{print $2}' || true)
        if [ -n "$PIDS" ]; then
            for PID in $PIDS; do
                if ps -p "$PID" > /dev/null 2>&1; then
                    ELAPSED=$(ps -p "$PID" -o etime= 2>/dev/null | xargs || echo "unknown")
                    CMDLINE=$(ps -p "$PID" -o command= 2>/dev/null | cut -c 1-80 || echo "")
                    echo "Process running (PID: $PID, elapsed: $ELAPSED)"
                    echo "  Command: $CMDLINE"
                    RUNNING=1
                fi
            done
        fi

        [ $RUNNING -eq 0 ] && echo "File Organizer is not running"
        ;;

    log)
        if [ -f "$LOG_FILE" ]; then
            tail -f "$LOG_FILE"
        else
            echo "Log file not found: $LOG_FILE"
        fi
        ;;

    test)
        echo "Running single scan in TEST MODE (dry run)..."
        run_organizer --config "$SCRIPT_DIR/config.yaml" --scan-once
        ;;

    test-real)
        echo "Running single scan in PRODUCTION MODE..."
        echo "WARNING: This modifies real files!"
        run_organizer --REAL --config "$SCRIPT_DIR/config.yaml" --scan-once
        ;;

    sync)
        echo "Running folder synchronization only (PRODUCTION MODE)..."
        run_organizer --REAL --config "$SCRIPT_DIR/config.yaml" --sync-only
        ;;

    dedupe)
        echo "Running duplicate detection and removal (PRODUCTION MODE)..."
        run_organizer --REAL --config "$SCRIPT_DIR/config.yaml" --dedupe-only
        ;;

    cleanup)
        echo "Cleaning up broken and stale symlinks in ~/organized..."
        run_organizer --REAL --config "$SCRIPT_DIR/config.yaml" --cleanup
        ;;

    create-test)
        echo "Creating test environment..."
        run_organizer --config "$SCRIPT_DIR/config.yaml" --create-test
        ;;

    gui)
        echo "Starting File Organizer Desktop App..."
        if [[ "$OSTYPE" == "darwin"* ]]; then
            python3 "$SCRIPT_DIR/desktop_app.py" &
            sleep 0.5
            osascript -e 'tell application "System Events" to set frontmost of first process whose name is "Python" to true' 2>/dev/null || true
        else
            python3 "$SCRIPT_DIR/desktop_app.py"
        fi
        ;;

    *)
        echo "File Organizer v2 — Management Script"
        echo ""
        echo "Usage: $0 {start|stop|restart|status|log|test|test-real|sync|dedupe|cleanup|create-test|gui}"
        echo ""
        echo "Background Daemon:"
        echo "  start       Start organizer as background daemon (PRODUCTION MODE)"
        echo "  stop        Stop all file_organizer processes"
        echo "  restart     Restart the background daemon"
        echo "  status      Check if daemon is running"
        echo "  log         Tail the log file (~/.file_organizer.log)"
        echo ""
        echo "Interactive Commands:"
        echo "  test        Run single scan in dry-run TEST MODE"
        echo "  test-real   Run single scan in PRODUCTION MODE"
        echo "  sync        Synchronize folders only (PRODUCTION MODE)"
        echo "  dedupe      Remove duplicate files only (PRODUCTION MODE)"
        echo "  cleanup     Remove broken/stale symlinks from ~/organized"
        echo "  create-test Create test environment with sample files"
        echo "  gui         Launch desktop GUI application"
        exit 1
        ;;
esac

exit 0

#!/usr/bin/env bash
# start_stop_magic_mirror.sh — start or stop a local MagicMirror install.
#
# Usage:
#   scripts/start_stop_magic_mirror.sh {start|stop} [MM_DIR]
#
# Environment / arguments:
#   MM_DIR  MagicMirror install dir (default: ~/Documents/repos/MagicMirror)
#           May also be passed as the second positional argument.

set -euo pipefail

MM_DIR="${MM_DIR:-$HOME/Documents/repos/MagicMirror}"
COMMAND="${1:-}"

if [[ -z "$COMMAND" ]]; then
    echo "Usage: $0 {start|stop} [MM_DIR]" >&2
    exit 2
fi

case "$COMMAND" in
    start|stop) ;;
    *)
        echo "❌ Unknown command: $COMMAND" >&2
        echo "Usage: $0 {start|stop} [MM_DIR]" >&2
        exit 2
        ;;
esac

if [[ $# -gt 2 ]]; then
    echo "❌ Too many arguments." >&2
    echo "Usage: $0 {start|stop} [MM_DIR]" >&2
    exit 2
fi

if [[ $# -eq 2 ]]; then
    MM_DIR="$2"
fi

[[ -d "$MM_DIR" ]] || { echo "❌ MagicMirror install not found: $MM_DIR" >&2; exit 1; }
[[ -f "$MM_DIR/package.json" ]] || { echo "❌ MagicMirror package.json not found: $MM_DIR/package.json" >&2; exit 1; }

echo "🪞 MagicMirror:   $MM_DIR"

# Print PIDs of MagicMirror processes belonging to this install.
mm_pids() {
    local mm_real pid cwd
    mm_real="$(realpath "$MM_DIR" 2>/dev/null || echo "$MM_DIR")"
    { pgrep -f "js/electron.js" || true; pgrep -f "serveronly" || true; } | sort -un | while read -r pid; do
        [[ -n "$pid" ]] || continue
        cwd="$(realpath "/proc/$pid/cwd" 2>/dev/null)" || continue
        [[ "$cwd" == "$mm_real" ]] && echo "$pid"
    done
}

MM_RUN_METHOD="none"

detect_running() {
    if command -v pm2 &>/dev/null && pm2 jlist 2>/dev/null | grep -q '"MagicMirror"'; then
        MM_RUN_METHOD="pm2"
        return
    fi
    if [[ -n "$(mm_pids)" ]]; then
        MM_RUN_METHOD="proc"
        return
    fi
    MM_RUN_METHOD="none"
}

stop_running() {
    case "$MM_RUN_METHOD" in
        pm2)
            echo "🛑 Stopping MagicMirror (pm2)…"
            pm2 stop MagicMirror >/dev/null
            ;;
        proc)
            echo "🛑 Stopping MagicMirror (electron/node)…"
            local pids
            pids=$(mm_pids | tr '\n' ' ')
            [[ -n "${pids// }" ]] && kill $pids 2>/dev/null || true
            sleep 2
            for pid in $pids; do
                kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
            done
            ;;
    esac
}

start_mm() {
    case "$MM_RUN_METHOD" in
        pm2)
            echo "▶️  Starting MagicMirror (pm2)…"
            pm2 restart MagicMirror >/dev/null
            ;;
        proc)
            echo "ℹ️  MagicMirror is already running."
            ;;
        *)
            local logfile="$MM_DIR/magicmirror.log"
            command -v npm >/dev/null 2>&1 || { echo "❌ npm is required to start MagicMirror." >&2; return 1; }
            echo "▶️  Starting MagicMirror (npm start)…"
            echo "📝 Logs → $logfile"
            ( cd "$MM_DIR" && DISPLAY="${DISPLAY:-:0}" nohup npm start >"$logfile" 2>&1 & )
            ;;
    esac
}

detect_running

case "$COMMAND" in
    start)
        start_mm
        ;;
    stop)
        if [[ "$MM_RUN_METHOD" == "none" ]]; then
            echo "ℹ️  MagicMirror was not running; nothing to stop."
        else
            stop_running
            echo "✅ MagicMirror stopped."
        fi
        ;;
esac

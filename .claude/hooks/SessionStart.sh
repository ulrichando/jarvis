#!/usr/bin/env bash
# SessionStart hook — prints JARVIS service + telemetry state at session
# start so Claude knows the system's current state without having to
# probe. Read-only, side-effect free.

set -u

echo "## JARVIS state at session start"
echo

# is-active prints the state AND exits non-zero when not active, so
# `|| echo unknown` would emit a second line. Capture, then default.
vstatus=$(systemctl --user is-active jarvis-voice-agent.service 2>/dev/null) || true
[[ -n "$vstatus" ]] || vstatus="unknown"
# The bridge has no systemd unit by design — start-desktop.sh launches it
# and it dies with the desktop. Probe the process, not a unit.
if pgrep -f 'bridge/server.ts' >/dev/null 2>&1; then
    bstatus="running (desktop-launched process; no systemd unit by design)"
else
    bstatus="not running (starts with the desktop)"
fi
echo "- voice-agent: $vstatus"
echo "- bridge:      $bstatus"

DB="$HOME/.local/share/jarvis/turn_telemetry.db"
if [[ -f "$DB" ]]; then
    last=$(sqlite3 "$DB" "SELECT ts_utc, route, llm_used FROM turns ORDER BY ts_utc DESC LIMIT 1" 2>/dev/null || true)
    if [[ -n "$last" ]]; then
        echo "- last turn:   $last"
    fi
    last_ts=$(sqlite3 "$DB" "SELECT ts_utc FROM turns ORDER BY ts_utc DESC LIMIT 1" 2>/dev/null || true)
    if [[ -n "$last_ts" ]]; then
        now_epoch=$(date -u +%s)
        last_epoch=$(date -u -d "$last_ts" +%s 2>/dev/null || echo 0)
        if [[ "$last_epoch" -gt 0 ]]; then
            age=$((now_epoch - last_epoch))
            echo "- session age: ${age}s since last turn"
            if [[ "$age" -lt 60 ]]; then
                echo "- WARNING:     <60s since last turn — voice session may be active. Don't restart without asking."
            fi
        fi
    fi
fi

branch=$(git -C "$(dirname "$(readlink -f "$0")")/../.." rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?")
echo "- branch:      $branch"

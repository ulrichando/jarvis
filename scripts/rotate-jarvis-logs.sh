#!/usr/bin/env bash
# Daily rotation for ~/.local/share/jarvis/logs/voice-agent.log
#
# Strategy: copytruncate-style rotation (no service restart needed).
# - If today's log is > 50 MB, OR > 1 day old, rename it to
#   voice-agent.log.<YYYYMMDD-HHMMSS>.gz and truncate the live file.
# - Keep the newest 14 archives; delete older.
#
# Run via systemd user timer (jarvis-log-rotate.timer) daily, OR
# manually: `bash scripts/rotate-jarvis-logs.sh`.
set -euo pipefail

LOG_DIR="${HOME}/.local/share/jarvis/logs"
LIVE="${LOG_DIR}/voice-agent.log"
SIZE_THRESHOLD_BYTES=$((50 * 1024 * 1024))  # 50 MB
KEEP=14

[[ -d "$LOG_DIR" ]] || exit 0
[[ -f "$LIVE" ]] || exit 0

size=$(stat -c%s "$LIVE" 2>/dev/null || echo 0)
mtime_epoch=$(stat -c%Y "$LIVE" 2>/dev/null || echo 0)
now_epoch=$(date +%s)
age_s=$((now_epoch - mtime_epoch))

# Rotate if over size OR over 1 day old
if [[ "$size" -lt "$SIZE_THRESHOLD_BYTES" && "$age_s" -lt 86400 ]]; then
    exit 0
fi

stamp=$(date +%Y%m%d-%H%M%S)
archive="${LOG_DIR}/voice-agent.log.${stamp}"

# Copy + truncate so the running service's append fd stays valid.
cp "$LIVE" "$archive"
: > "$LIVE"
gzip -9 "$archive"

# Prune old archives — keep the newest $KEEP
mapfile -t archives < <(ls -1t "${LOG_DIR}"/voice-agent.log.*.gz 2>/dev/null || true)
if [[ "${#archives[@]}" -gt "$KEEP" ]]; then
    for old in "${archives[@]:$KEEP}"; do
        rm -f -- "$old"
    done
fi

echo "[$(date -Is)] rotated voice-agent.log → ${archive}.gz (size=${size}B, age=${age_s}s)"

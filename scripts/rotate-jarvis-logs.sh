#!/usr/bin/env bash
# Rotation for ~/.local/share/jarvis/logs/*.log
#
# Strategy: copytruncate-style rotation (no service restart needed —
# every writer holds an O_APPEND fd, which keeps working after truncate).
# - voice-agent.log (the main log): rotate when > 50 MB OR > 1 day old;
#   keep the newest 14 archives.
# - every other *.log (voice-client, livekit-server, proxy, health, …):
#   rotate when > 10 MB; keep the newest 5 archives. Size-only — these
#   are slow growers and daily gzips of 12 KB files would just be clutter.
#
# Run via systemd user timer (jarvis-log-rotate.timer) daily, OR
# manually: `bash scripts/rotate-jarvis-logs.sh`.
set -euo pipefail

LOG_DIR="${HOME}/.local/share/jarvis/logs"
[[ -d "$LOG_DIR" ]] || exit 0

# rotate <live-file> <size-threshold-bytes> <max-age-s (0 = size-only)> <keep>
rotate() {
    local live="$1" thr="$2" max_age="$3" keep="$4"
    [[ -f "$live" ]] || return 0
    local size mtime age
    size=$(stat -c%s "$live" 2>/dev/null || echo 0)
    mtime=$(stat -c%Y "$live" 2>/dev/null || echo 0)
    age=$(( $(date +%s) - mtime ))
    if [[ "$size" -lt "$thr" ]] && { [[ "$max_age" -eq 0 ]] || [[ "$age" -lt "$max_age" ]]; }; then
        return 0
    fi
    [[ "$size" -gt 0 ]] || return 0   # never archive an empty file
    local archive
    archive="${live}.$(date +%Y%m%d-%H%M%S)"
    # Copy + truncate so the running service's append fd stays valid.
    cp "$live" "$archive"
    : > "$live"
    gzip -9 "$archive"
    # Prune old archives — keep the newest $keep
    local archives
    mapfile -t archives < <(ls -1t "${live}".*.gz 2>/dev/null || true)
    if [[ "${#archives[@]}" -gt "$keep" ]]; then
        local old
        for old in "${archives[@]:$keep}"; do
            rm -f -- "$old"
        done
    fi
    echo "[$(date -Is)] rotated $(basename "$live") → $(basename "$archive").gz (size=${size}B, age=${age}s)"
}

rotate "${LOG_DIR}/voice-agent.log" $((50 * 1024 * 1024)) 86400 14

for f in "${LOG_DIR}"/*.log; do
    [[ "$f" == "${LOG_DIR}/voice-agent.log" ]] && continue
    rotate "$f" $((10 * 1024 * 1024)) 0 5
done

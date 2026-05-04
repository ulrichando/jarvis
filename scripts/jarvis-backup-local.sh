#!/usr/bin/env bash
# Local hourly snapshots of:
#   - ~/.jarvis/hub/state.db          (canonical hub store — memories live here)
#   - ~/.local/share/jarvis/turn_telemetry.db (per-turn TTFW / route / emotion / interrupted)
#
# Atomic via SQLite's online-backup API — safe to run while writers are active.
#
# Restore:
#   systemctl --user stop jarvis-hub
#   cp ~/.jarvis/snapshots/state-latest.db ~/.jarvis/hub/state.db
#   systemctl --user start jarvis-hub
#
# Telemetry restore (no service to stop — the writer reopens on next turn):
#   cp ~/.jarvis/snapshots/turn_telemetry-latest.db ~/.local/share/jarvis/turn_telemetry.db
set -euo pipefail

DST_DIR="${HOME}/.jarvis/snapshots"
RETENTION_DAYS="${JARVIS_SNAPSHOT_RETENTION_DAYS:-30}"

mkdir -p "${DST_DIR}"

stamp="$(date +%Y-%m-%d-%H%M)"

# Snapshot a single sqlite db via the online-backup API. Returns 0 if
# the source exists and the backup succeeded; non-zero otherwise.
backup_one() {
    local label="$1" src="$2"
    if [[ ! -f "$src" ]]; then
        echo "[jarvis-backup] skip ${label}: no source at ${src}" >&2
        return 1
    fi
    local dst="${DST_DIR}/${label}-${stamp}.db"
    sqlite3 "$src" ".backup '${dst}'"
    ln -sfn "$(basename "$dst")" "${DST_DIR}/${label}-latest.db"
    local size; size=$(du -h "$dst" | awk '{print $1}')
    local count; count=$(find "${DST_DIR}" -maxdepth 1 -name "${label}-*.db" -type f | wc -l)
    echo "[jarvis-backup] ${label}: ${dst} (${size}) — ${count} retained"
    find "${DST_DIR}" -maxdepth 1 -name "${label}-*.db" -type f -mtime "+${RETENTION_DAYS}" -delete
}

backup_one "state" "${HOME}/.jarvis/hub/state.db" || true
backup_one "turn_telemetry" "${HOME}/.local/share/jarvis/turn_telemetry.db" || true

# Pre-retirement conversations-*.db snapshots — prune.
find "${DST_DIR}" -maxdepth 1 -name 'conversations-*.db' -type f -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null || true

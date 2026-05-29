#!/usr/bin/env bash
# Local hourly snapshots of:
#   - ~/.local/share/jarvis/turn_telemetry.db (per-turn TTFW / route / emotion / interrupted)
#
# Atomic via SQLite's online-backup API — safe to run while writers are active.
#
# Restore (no service to stop — the writer reopens on next turn):
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

backup_one "turn_telemetry" "${HOME}/.local/share/jarvis/turn_telemetry.db" || true

# Hub state DB — holds messages, sessions, and memories from the voice agent hub.
# This is the real conversation store (~2–3 MB); ~/.jarvis/conversations.db is a
# known-empty stub (0 bytes) and is intentionally skipped.
backup_one "hub_state" "${HOME}/.jarvis/hub/state.db" || true

# Markdown memory store — per-project memory files written by the auto-extractor.
# Backed up as a tarball (plain text, not SQLite) so individual files are recoverable.
MEMORY_DIR="${HOME}/.claude/projects/-home-ulrich-Documents-Projects-jarvis/memory"
if [[ -d "${MEMORY_DIR}" ]]; then
    memory_dst="${DST_DIR}/memory-${stamp}.tar.gz"
    tar czf "${memory_dst}" -C "$(dirname "${MEMORY_DIR}")" "$(basename "${MEMORY_DIR}")"
    ln -sfn "$(basename "${memory_dst}")" "${DST_DIR}/memory-latest.tar.gz"
    mem_size=$(du -h "${memory_dst}" | awk '{print $1}')
    mem_count=$(find "${DST_DIR}" -maxdepth 1 -name 'memory-*.tar.gz' -type f | wc -l)
    echo "[jarvis-backup] memory: ${memory_dst} (${mem_size}) — ${mem_count} retained"
    find "${DST_DIR}" -maxdepth 1 -name 'memory-*.tar.gz' -type f -mtime "+${RETENTION_DAYS}" -delete
else
    echo "[jarvis-backup] skip memory: no directory at ${MEMORY_DIR}" >&2
fi

# Prune any pre-existing snapshot families that are no longer produced.
find "${DST_DIR}" -maxdepth 1 \( -name 'state-*.db' -o -name 'conversations-*.db' \) -type f -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null || true

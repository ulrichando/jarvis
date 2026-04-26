#!/usr/bin/env bash
# Local hourly snapshot of ~/.jarvis/conversations.db.
#
# Atomic via SQLite's online-backup API — safe to run while the
# voice-agent and bridge are actively writing (WAL mode is on).
#
# Convex's data store is intentionally NOT backed up: convex-data is
# a derived mirror of conversations.db. If it gets corrupted, wipe
# ~/.jarvis/convex-data, restart the systemd unit, and re-run
#   src/voice-agent/.venv/bin/python src/voice-agent/backfill_convex.py
# to rebuild the replica from SQLite. Backing up only the source of
# truth keeps the snapshot footprint tiny (~700 KB per snapshot) and
# avoids the extra complexity of stop-and-tar around a live container.
#
# Restore:
#   systemctl --user stop jarvis-voice-agent jarvis-bridge
#   cp ~/.jarvis/snapshots/conversations-latest.db ~/.jarvis/conversations.db
#   systemctl --user start jarvis-voice-agent jarvis-bridge
set -euo pipefail

SRC="${HOME}/.jarvis/conversations.db"
DST_DIR="${HOME}/.jarvis/snapshots"
RETENTION_DAYS="${JARVIS_SNAPSHOT_RETENTION_DAYS:-30}"

if [[ ! -f "$SRC" ]]; then
    echo "[jarvis-backup] no source db at ${SRC}" >&2
    exit 1
fi

mkdir -p "${DST_DIR}"

stamp="$(date +%Y-%m-%d-%H%M)"
dst="${DST_DIR}/conversations-${stamp}.db"

# SQLite .backup uses the online-backup API (block-level copy with
# WAL coordination). Concurrent readers/writers are fine.
sqlite3 "$SRC" ".backup '${dst}'"

# Convenience symlink → restore commands always know a stable path.
ln -sfn "$(basename "$dst")" "${DST_DIR}/conversations-latest.db"

# Prune snapshots older than RETENTION_DAYS.
find "${DST_DIR}" -name 'conversations-*.db' -type f -mtime "+${RETENTION_DAYS}" -delete

size=$(du -h "$dst" | awk '{print $1}')
count=$(find "${DST_DIR}" -name 'conversations-*.db' -type f | wc -l)
echo "[jarvis-backup] snapshot ${dst} (${size}) — ${count} retained"

#!/usr/bin/env bash
# Local hourly snapshot of ~/.jarvis/hub/state.db (the canonical
# event-hub state DB — populated by jarvis-hub.service from the
# Redis Streams `events:conversation` log).
#
# Atomic via SQLite's online-backup API — safe to run while the hub
# daemon is actively writing (WAL mode is on).
#
# Pre-2026-05-03 this snapshotted ~/.jarvis/conversations.db (now
# retired) and skipped the Convex mirror as derived. Today the hub's
# state.db is the single canonical store; if it's lost, replay from
# the Redis events stream rebuilds it (the daemon's consumer group
# has the offset, and `XRANGE events:conversation - +` re-applies
# every event idempotently via UNIQUE(source, source_event_id)).
#
# Restore:
#   systemctl --user stop jarvis-hub
#   cp ~/.jarvis/snapshots/state-latest.db ~/.jarvis/hub/state.db
#   systemctl --user start jarvis-hub
set -euo pipefail

SRC="${HOME}/.jarvis/hub/state.db"
DST_DIR="${HOME}/.jarvis/snapshots"
RETENTION_DAYS="${JARVIS_SNAPSHOT_RETENTION_DAYS:-30}"

if [[ ! -f "$SRC" ]]; then
    echo "[jarvis-backup] no source db at ${SRC}" >&2
    exit 1
fi

mkdir -p "${DST_DIR}"

stamp="$(date +%Y-%m-%d-%H%M)"
dst="${DST_DIR}/state-${stamp}.db"

# SQLite .backup uses the online-backup API (block-level copy with
# WAL coordination). Concurrent readers/writers are fine.
sqlite3 "$SRC" ".backup '${dst}'"

# Convenience symlink → restore commands always know a stable path.
ln -sfn "$(basename "$dst")" "${DST_DIR}/state-latest.db"

# Prune snapshots older than RETENTION_DAYS.
find "${DST_DIR}" -name 'state-*.db' -type f -mtime "+${RETENTION_DAYS}" -delete
# Also prune any pre-retirement conversations-*.db snapshots.
find "${DST_DIR}" -name 'conversations-*.db' -type f -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null || true

size=$(du -h "$dst" | awk '{print $1}')
count=$(find "${DST_DIR}" -name 'state-*.db' -type f | wc -l)
echo "[jarvis-backup] snapshot ${dst} (${size}) — ${count} retained"

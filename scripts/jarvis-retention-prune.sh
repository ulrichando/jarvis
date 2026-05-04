#!/usr/bin/env bash
# Telemetry retention — keep turns + launch_attempts within a bounded
# window so the SQLite db doesn't grow unbounded over years of use.
#
# Default cap: 180 days. Override via JARVIS_TELEMETRY_RETENTION_DAYS.
# `state.db` (memories, conversation history) is NOT pruned here —
# that's user-curated content and the user explicitly asked the
# memory layer to survive (Phase 12 contract).
#
# Idempotent: re-running with the same window is a no-op past the
# first call. Safe to run via systemd timer.
set -euo pipefail

DB="${JARVIS_TELEMETRY_PATH:-${HOME}/.local/share/jarvis/turn_telemetry.db}"
DAYS="${JARVIS_TELEMETRY_RETENTION_DAYS:-180}"

if [[ ! -f "$DB" ]]; then
    echo "[retention] no telemetry db at ${DB} — nothing to do" >&2
    exit 0
fi

cutoff="$(date -u -d "${DAYS} days ago" +%Y-%m-%dT%H:%M:%SZ)"

before_turns=$(sqlite3 "$DB" "SELECT COUNT(*) FROM turns;")
before_launches=$(sqlite3 "$DB" "SELECT COUNT(*) FROM launch_attempts;" 2>/dev/null || echo 0)

# Prune both tables. ts_utc is ISO-8601 (string compare works because
# the format is sortable).
sqlite3 "$DB" <<EOF
DELETE FROM turns WHERE ts_utc < '${cutoff}';
DELETE FROM launch_attempts WHERE ts_utc < '${cutoff}';
VACUUM;
EOF

after_turns=$(sqlite3 "$DB" "SELECT COUNT(*) FROM turns;")
after_launches=$(sqlite3 "$DB" "SELECT COUNT(*) FROM launch_attempts;" 2>/dev/null || echo 0)

pruned_turns=$((before_turns - after_turns))
pruned_launches=$((before_launches - after_launches))

echo "[retention] cutoff=${cutoff} (${DAYS}d) — pruned ${pruned_turns} turns, ${pruned_launches} launch_attempts"
echo "[retention] now: ${after_turns} turns, ${after_launches} launch_attempts in ${DB}"

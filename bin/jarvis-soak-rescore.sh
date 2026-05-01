#!/usr/bin/env bash
# Phase 11 — soak re-score helper.
#
# After ≥6h of dogfood with the Phase 10.x changes shipped, run this
# to see the data and decide which axis bumps to claim. Looks at:
#
#   - Phase 10.7: ack-opener variety (need to grep agent logs since
#     openers aren't in turn_telemetry — proxy via jarvis_text first
#     word distribution).
#   - Phase 10.5: interrupt rate per route (validate overlay tuning).
#   - Phase 10.6: launch_app outcomes per binary (suggest prompt
#     updates for MISSING patterns).
#   - Phase 10.3: rms_db distribution (sanity-check ±6 dB threshold).
#
# Usage:
#   bin/jarvis-soak-rescore.sh           # last 24h
#   bin/jarvis-soak-rescore.sh 6         # last N hours
set -euo pipefail
HOURS="${1:-24}"
DB="${HOME}/.local/share/jarvis/turn_telemetry.db"
PYTHON="${HOME}/Documents/Projects/jarvis/src/voice-agent/.venv/bin/python"

if [ ! -f "$DB" ]; then
  echo "no telemetry db at $DB"; exit 1
fi

echo "=== Soak window: last ${HOURS}h ==="
echo

echo "--- Standard report ---"
"$PYTHON" "${HOME}/Documents/Projects/jarvis/src/voice-agent/turn_telemetry.py" --report --days 1
echo

echo "--- Ack-opener distribution (jarvis_text first ~3 words) ---"
sqlite3 "$DB" <<SQL
.headers off
.mode column
WITH recent AS (
  SELECT
    LOWER(SUBSTR(jarvis_text, 1, 30)) AS prefix,
    COUNT(*) AS n
  FROM turns
  WHERE ts_utc > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-${HOURS} hours')
    AND jarvis_text != ''
  GROUP BY prefix
  ORDER BY n DESC
  LIMIT 15
)
SELECT prefix, n FROM recent;
SQL
echo

echo "--- Sir-frequency check (% replies containing 'sir') ---"
sqlite3 "$DB" <<SQL
SELECT
  ROUND(100.0 * SUM(CASE WHEN LOWER(jarvis_text) LIKE '% sir%' OR LOWER(jarvis_text) LIKE '%sir,%' OR LOWER(jarvis_text) LIKE '%sir.%' THEN 1 ELSE 0 END) / COUNT(*), 1) || '%'
FROM turns
WHERE ts_utc > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-${HOURS} hours')
  AND jarvis_text != '';
SQL
echo

echo "--- Per-binary launch outcomes (last ${HOURS}h) ---"
sqlite3 "$DB" <<SQL
.headers on
.mode column
SELECT binary,
       SUM(CASE outcome WHEN 'OK' THEN 1 ELSE 0 END) AS ok,
       SUM(CASE outcome WHEN 'MISSING' THEN 1 ELSE 0 END) AS missing,
       SUM(CASE outcome WHEN 'CRASHED' THEN 1 ELSE 0 END) AS crashed
FROM launch_attempts
WHERE ts_utc > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-${HOURS} hours')
GROUP BY binary
ORDER BY ok+missing+crashed DESC
LIMIT 10;
SQL
echo

echo "--- Interrupt rate per route (last ${HOURS}h) ---"
sqlite3 "$DB" <<SQL
.headers on
.mode column
SELECT COALESCE(route, '?') AS route,
       COUNT(*) AS turns,
       SUM(COALESCE(interrupted, 0)) AS interrupts,
       ROUND(100.0 * SUM(COALESCE(interrupted, 0)) / COUNT(*), 1) AS rate_pct
FROM turns
WHERE ts_utc > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-${HOURS} hours')
GROUP BY route
HAVING turns >= 5
ORDER BY rate_pct DESC;
SQL
echo

echo "Re-score guidance:"
echo "  axis 6 (ack vocab): if top opener < 30% AND >= 6 distinct in top-15 → bump 8 → 9"
echo "  axis 7 (interrupt):  no route should exceed 25% interrupt rate"
echo "  Phase 10.6: any binary with ≥3 MISSING → update specialist prompt"

#!/usr/bin/env bash
# One-shot cleanup of historical assistant turns containing now-banned
# patterns from state.db. Removes the in-context examples that poison
# the LLM via the recall-seed path (industry pattern: scrub the example
# pool, don't replay raw history).
#
# What gets deleted:
#   - Assistant rows starting with archaic openers
#     (Indeed/Quite/Splendid/Naturally/Very well/At once/Excellent/
#      An interesting question/A fine result/Worth examining/I see)
#   - Assistant rows that are JUST a meta-silence ack
#     (Silence/Silent/Quietly/Listening/Standing by/etc.)
#
# Pre-flight: hourly snapshot at ~/.jarvis/snapshots/state-latest.db
# is your rollback point. Restore with:
#   cp ~/.jarvis/snapshots/state-latest.db ~/.jarvis/hub/state.db
# (Pre-2026-05-22 a `systemctl --user stop jarvis-hub` was needed
# around the copy; the hub daemon was removed entirely on that date,
# so the residual state.db file has no writer now and can be safely
# overwritten in place.)
set -euo pipefail

DB="${HOME}/.jarvis/hub/state.db"

if [[ ! -f "$DB" ]]; then
    echo "[purge] no state.db at ${DB}" >&2
    exit 1
fi

# Force a fresh snapshot before destructive op.
echo "[purge] taking pre-purge snapshot…"
"$(dirname "$0")/jarvis-backup-local.sh" || {
    echo "[purge] snapshot failed — aborting" >&2
    exit 1
}

# SQL patterns mirror the Python regexes in jarvis_agent.py.
# Anchor with leading optional whitespace; case-insensitive via LOWER.
PRE_COUNT=$(sqlite3 "$DB" "SELECT COUNT(*) FROM messages WHERE role='assistant';")
echo "[purge] pre-purge assistant row count: ${PRE_COUNT}"

# Show what's about to die first.
echo "[purge] sample of rows that will be deleted (first 10):"
sqlite3 -separator " | " "$DB" "
    SELECT datetime(ts/1000,'unixepoch') AS t, substr(text,1,80)
    FROM messages
    WHERE role='assistant' AND (
        LOWER(TRIM(text)) GLOB 'indeed[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'indeed,*'
        OR LOWER(TRIM(text)) GLOB 'indeed.*'
        OR LOWER(TRIM(text)) GLOB 'quite[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'quite,*'
        OR LOWER(TRIM(text)) GLOB 'quite.*'
        OR LOWER(TRIM(text)) GLOB 'splendid[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'naturally[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'very well[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'at once[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'excellent[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'silence[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'silent[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'an interesting question*'
        OR LOWER(TRIM(text)) GLOB 'a fine result*'
        OR LOWER(TRIM(text)) GLOB 'worth examining*'
        OR LOWER(TRIM(text)) GLOB 'i see.*'
    )
    ORDER BY ts DESC LIMIT 10;"

# Count what will go.
TO_DELETE=$(sqlite3 "$DB" "
    SELECT COUNT(*) FROM messages
    WHERE role='assistant' AND (
        LOWER(TRIM(text)) GLOB 'indeed[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'indeed,*'
        OR LOWER(TRIM(text)) GLOB 'indeed.*'
        OR LOWER(TRIM(text)) GLOB 'quite[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'quite,*'
        OR LOWER(TRIM(text)) GLOB 'quite.*'
        OR LOWER(TRIM(text)) GLOB 'splendid[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'naturally[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'very well[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'at once[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'excellent[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'silence[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'silent[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'an interesting question*'
        OR LOWER(TRIM(text)) GLOB 'a fine result*'
        OR LOWER(TRIM(text)) GLOB 'worth examining*'
        OR LOWER(TRIM(text)) GLOB 'i see.*'
    );")
echo "[purge] ${TO_DELETE} rows will be deleted"

if [[ "$TO_DELETE" -eq 0 ]]; then
    echo "[purge] nothing to do"
    exit 0
fi

# Do it.
sqlite3 "$DB" "
    DELETE FROM messages
    WHERE role='assistant' AND (
        LOWER(TRIM(text)) GLOB 'indeed[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'indeed,*'
        OR LOWER(TRIM(text)) GLOB 'indeed.*'
        OR LOWER(TRIM(text)) GLOB 'quite[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'quite,*'
        OR LOWER(TRIM(text)) GLOB 'quite.*'
        OR LOWER(TRIM(text)) GLOB 'splendid[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'naturally[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'very well[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'at once[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'excellent[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'silence[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'silent[ ,.!?]*'
        OR LOWER(TRIM(text)) GLOB 'an interesting question*'
        OR LOWER(TRIM(text)) GLOB 'a fine result*'
        OR LOWER(TRIM(text)) GLOB 'worth examining*'
        OR LOWER(TRIM(text)) GLOB 'i see.*'
    );
    VACUUM;
"

POST_COUNT=$(sqlite3 "$DB" "SELECT COUNT(*) FROM messages WHERE role='assistant';")
DIFF=$((PRE_COUNT - POST_COUNT))
echo "[purge] done — ${DIFF} rows removed (now ${POST_COUNT} assistant rows)"
echo "[purge] pre-purge snapshot is at ~/.jarvis/snapshots/state-latest.db"

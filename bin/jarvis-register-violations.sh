#!/usr/bin/env bash
# Count how often the post-process filters caught the LLM violating
# the register (archaic openers, meta-silence acks, voice closers).
#
# This is the "evals" axis from the industry pattern: continuous
# grading. Filter fires == LLM produced banned output == prompt is
# either being ignored (more reinforcement needed) or the model is
# pulling a pattern from chat_ctx (recall-scrub gap).
#
# Usage:
#   bin/jarvis-register-violations.sh           # last 24h
#   bin/jarvis-register-violations.sh --since "2 hours ago"
set -euo pipefail

SINCE="${1:-1 day ago}"

# Convert "since" to journalctl form. journalctl uses --since "string".
# tail of /tmp log doesn't have date filtering — we'll grep instead and
# count both sources.

LOG=/tmp/jarvis-voice-agent.log

if [[ ! -f "$LOG" ]]; then
    echo "no log at $LOG" >&2
    exit 1
fi

# Cutoff timestamp for filtering log lines.
CUTOFF=$(date -u -d "$SINCE" +%Y-%m-%dT%H:%M:%S)

count_pattern() {
    local label="$1" pattern="$2"
    local n
    n=$(awk -v cutoff="$CUTOFF" -v pat="$pattern" '
        match($0, /[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}/) {
            ts = substr($0, RSTART, RLENGTH)
            if (ts >= cutoff && index($0, pat)) c++
        }
        END { print c+0 }
    ' "$LOG")
    printf "  %-30s %d\n" "$label" "$n"
}

echo "=== JARVIS register-violation report ==="
echo "Window: since $SINCE (cutoff $CUTOFF UTC)"
echo
echo "Post-process filter fires (LLM produced banned output):"
count_pattern "archaic-opener trimmed:"  "[archaic-strip]"
count_pattern "meta-silence dropped:"    "[meta-silence-strip]"
count_pattern "voice-closer stripped:"   "[closer-strip]"
count_pattern "preamble stripped:"       "[preamble-strip]"
count_pattern "STT-gate dropped:"        "[stt-gate]"
echo
echo "Recall scrubbing (poison kept out of chat_ctx):"
count_pattern "archaic-trimmed at seed:" "archaic-trimmed"
count_pattern "dropped at seed:"         "dropped)"
count_pattern "tool-leak cleaned at seed:" "tool-leak-cleaned"
echo
echo "Total assistant turns in window (from telemetry):"
sqlite3 "${HOME}/.local/share/jarvis/turn_telemetry.db" \
    "SELECT printf('  %-30s %d', 'total turns:', COUNT(*))
     FROM turns
     WHERE ts_utc > '${CUTOFF}Z' AND jarvis_text != '';" 2>/dev/null
echo
echo "Interpretation:"
echo "  - 0 fires = prompt holding firmly OR no test inputs hit the filters"
echo "  - few fires per 100 turns = normal LLM drift, filters are catching it"
echo "  - many fires = prompt may need tightening / few-shot reinforcement"

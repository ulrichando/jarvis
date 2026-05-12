#!/usr/bin/env bash
# JARVIS self-evolution soak — day-N sanity check.
#
# Invoked by ~/.config/systemd/user/jarvis-evolution-soak-check.timer
# at 09:00 local each day of the 7-day soak. Writes a dated report to
# ~/.jarvis/soak-check-<date>.txt for the operator (Ulrich) to read
# the next morning.
#
# Watches for:
#   1. Any actual STAGED rules in learned_rules.md — should be zero
#      while JARVIS_EVOLUTION_LOGGING_ONLY is unset/=1.
#   2. Count of would_stage events in evolution_log.jsonl — reflects
#      live_capture firings; gut-check the totals look reasonable.
#   3. Sample of recent would_stage entries — check they're triggered
#      by real user corrections, not Whisper-mis-transcribed noise.
#   4. [evolution] WARNING lines in voice-agent.log — bug signals from
#      the producer / evaluator paths.

set -uo pipefail   # NOT -e — we want to keep going on partial failures.

REPO_ROOT="/home/ulrich/Documents/Projects/jarvis"
JARVIS_DIR="$HOME/.jarvis"
LOG_DIR="$HOME/.local/share/jarvis/logs"

DATE_LOCAL="$(date '+%Y-%m-%d')"
OUT="$JARVIS_DIR/soak-check-$DATE_LOCAL.txt"

mkdir -p "$JARVIS_DIR"
exec >"$OUT" 2>&1

echo "===================================================="
echo " JARVIS Self-Evolution Soak — day-N sanity check"
echo " Run: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo " Read by: Ulrich (next morning)"
echo "===================================================="
echo

echo "─── 1. STAGED rules (should be 0 while LOGGING_ONLY) ───"
if cd "$REPO_ROOT" 2>/dev/null; then
    ./bin/jarvis-rules list --tier=staged 2>&1 | sed 's/^/  /'
else
    echo "  ERR: cannot cd $REPO_ROOT"
fi
echo

echo "─── 2. would_stage event count ───"
if [[ -f "$JARVIS_DIR/evolution_log.jsonl" ]]; then
    COUNT=$(grep -c '"kind":"would_stage"' "$JARVIS_DIR/evolution_log.jsonl" 2>/dev/null || echo 0)
    echo "  total would_stage: $COUNT"
    LIVE=$(grep -c '"kind":"live_capture_proposal"' "$JARVIS_DIR/evolution_log.jsonl" 2>/dev/null || echo 0)
    echo "  total live_capture_proposal: $LIVE"
    MINE=$(grep -c '"kind":"mining_cycle"' "$JARVIS_DIR/evolution_log.jsonl" 2>/dev/null || echo 0)
    echo "  total mining_cycle runs: $MINE"
else
    echo "  evolution_log.jsonl absent — no producer has fired yet"
fi
echo

echo "─── 3. Last 5 would_stage entries (gut-check signal quality) ───"
if [[ -f "$JARVIS_DIR/evolution_log.jsonl" ]]; then
    grep '"kind":"would_stage"' "$JARVIS_DIR/evolution_log.jsonl" 2>/dev/null \
        | tail -5 \
        | python3 -c "
import json, sys
for line in sys.stdin:
    try:
        d = json.loads(line)
    except Exception:
        continue
    ts = d.get('ts','?')
    rid = d.get('rule_id','?')
    src = d.get('source','?')
    print(f'  [{ts}] {rid} via {src}: turns={d.get(\"evidence_turns\",[])}')
" || echo "  (no entries yet)"
else
    echo "  evolution_log.jsonl absent"
fi
echo

echo "─── 4. Evolution-related WARNINGs in voice-agent.log (last 24h) ───"
if [[ -f "$LOG_DIR/voice-agent.log" ]]; then
    SINCE=$(date -d '24 hours ago' '+%Y-%m-%dT%H:%M:%S')
    grep -E '"level":\s*"WARNING"|"level":\s*"ERROR"' "$LOG_DIR/voice-agent.log" 2>/dev/null \
        | grep -E '\[wireup\]|\[evolution\]|\[lifecycle\]|\[live-capture\]|\[miner\]|\[contradiction\]|\[poll\]|\[red-team\]|\[replay\]|\[golden_eval\]|\[store\]|\[evaluator\]' \
        | tail -15 \
        | sed 's/^/  /' || echo "  (no evolution warnings)"
    if ! grep -qE '\[wireup\]|\[evolution\]' "$LOG_DIR/voice-agent.log" 2>/dev/null; then
        echo "  (no evolution-tagged log entries today — wireup never fired)"
    fi
else
    echo "  voice-agent.log absent at $LOG_DIR/voice-agent.log"
fi
echo

echo "─── 5. Daily evolution report (written by report_loop) ───"
if [[ -f "$JARVIS_DIR/evolution_report.md" ]]; then
    echo "  age: $(($(date +%s) - $(stat -c %Y "$JARVIS_DIR/evolution_report.md"))) seconds"
    echo "  ---"
    sed 's/^/  /' "$JARVIS_DIR/evolution_report.md"
else
    echo "  evolution_report.md absent — report_loop hasn't completed a cycle"
    echo "  (loop runs every 24h; if agent restarted recently, expected to be missing)"
fi
echo

echo "─── 6. Service health ───"
systemctl --user is-active jarvis-voice-agent.service 2>&1 | sed 's/^/  voice-agent: /'
systemctl --user is-active livekit-server.service 2>&1 | sed 's/^/  livekit-server: /'
systemctl --user is-active jarvis-bridge.service 2>&1 | sed 's/^/  bridge: /'
echo

echo "─── 7. Autonomous transitions (~/Documents/jarvis-evolution/<date>.md) ───"
CHANGELOG_DIR="$HOME/Documents/jarvis-evolution"
TODAY_FILE="$CHANGELOG_DIR/$DATE_LOCAL.md"
YESTERDAY_LOCAL="$(date -d 'yesterday' '+%Y-%m-%d')"
YDAY_FILE="$CHANGELOG_DIR/$YESTERDAY_LOCAL.md"
echo "  changelog dir: $CHANGELOG_DIR"
if [[ -d "$CHANGELOG_DIR" ]]; then
    for f in "$YDAY_FILE" "$TODAY_FILE"; do
        if [[ -f "$f" ]]; then
            echo "  -- $(basename "$f") --"
            # grep -c prints the count and exits 1 if zero matches —
            # using "|| echo 0" would double-print. Just assign directly.
            STG=$(grep -c '^## .* — AUTO-STAGED ' "$f" 2>/dev/null) || STG=0
            ARC=$(grep -c '^## .* — ARCHIVED ' "$f" 2>/dev/null) || ARC=0
            PRO=$(grep -c '^## .* — PROMOTED-TO-ACCEPTED ' "$f" 2>/dev/null) || PRO=0
            RES=$(grep -c '^## .* — RESTORED ' "$f" 2>/dev/null) || RES=0
            echo "    auto-staged: $STG    archived: $ARC    promoted: $PRO    restored: $RES"
            echo "    --- entries ---"
            sed 's/^/      /' "$f"
        fi
    done
    if [[ ! -f "$TODAY_FILE" && ! -f "$YDAY_FILE" ]]; then
        echo "  (no changelog files for today or yesterday — no autonomous transitions)"
    fi
else
    echo "  (directory absent — autonomous-mode evolution has never written here)"
fi
echo

echo "===================================================="
echo " Verdict heuristic (manual review still required):"
echo "   - autonomous-mode: section 7 entries are the source"
echo "       of truth; review them for false positives"
echo "       (like the R-0007 dead-subsystem incident 2026-05-12)"
echo "   - many [evolution] WARNINGs → producer/evaluator bug"
echo "===================================================="

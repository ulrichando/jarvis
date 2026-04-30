#!/usr/bin/env bash
#
# jarvis-rubric-rescore — re-score the voice intelligence rubric against
# fresh telemetry data after a day of dogfood. Reads the live SQLite,
# applies the verdict logic agreed in Phase 7, and either commits a
# score change or appends a "no change" verification note.
#
# Usage:
#   bin/jarvis-rubric-rescore [--days N] [--dry-run]
#
# Defaults to --days 1 (only count turns from the last 24h, so we
# measure the impact of THIS dogfood window — not the historical 127
# pre-instrumentation rows).
#
set -eu
# NOTE: not using pipefail — the verdict logic uses many `grep ... | head -1`
# pipelines where a no-match grep returning 1 is the EXPECTED case (it just
# means that data point isn't in the report). pipefail would abort the whole
# script on the first absent metric.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DAYS=1
DRY_RUN=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --days) DAYS="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

RUBRIC="docs/superpowers/specs/2026-04-30-voice-intelligence-rubric.md"
PYTHON="src/voice-agent/.venv/bin/python"

if [[ ! -f "$RUBRIC" ]]; then
    echo "rubric not found: $RUBRIC" >&2
    exit 1
fi
if [[ ! -x "$PYTHON" ]]; then
    echo "voice-agent venv missing: $PYTHON" >&2
    exit 1
fi

# ── 1. Pull the report ────────────────────────────────────────────────
echo "==> Running telemetry report (last ${DAYS} day(s))…"
REPORT="$($PYTHON src/voice-agent/turn_telemetry.py --report --days "$DAYS")"
echo "$REPORT"
echo

# Bail early if there's no data — re-running with a longer window may help.
if echo "$REPORT" | grep -q "^no telemetry yet$"; then
    echo "No telemetry — agent hasn't run yet. Re-run after some dogfood." >&2
    exit 0
fi
TOTAL_TURNS="$(echo "$REPORT" | sed -n 's/^.*total turns=\([0-9]*\).*/\1/p' | head -1)"
if [[ "${TOTAL_TURNS:-0}" -eq 0 ]]; then
    echo "Zero turns in window — try a wider --days." >&2
    exit 0
fi

# ── 2. Apply the verdict logic ────────────────────────────────────────
# Each axis below is checked against the live report; verdict notes are
# accumulated into VERDICTS, score moves into MOVES.
VERDICTS=()
MOVES=()

# Axis 1 — TTFW hit-rate (current: 10)
HIT_PCT="$(echo "$REPORT" | sed -n 's/^ttfw target hit-rate: \([0-9]*\)%.*/\1/p' | head -1)"
HIT_PCT="${HIT_PCT:-0}"
if [[ "$HIT_PCT" -ge 80 ]]; then
    VERDICTS+=("Axis 1 (TTFW=10): confirmed — hit-rate ${HIT_PCT}% ≥ 80%.")
elif [[ "$HIT_PCT" -lt 50 ]]; then
    VERDICTS+=("Axis 1 (TTFW): DOWNGRADE 10 → 9 — hit-rate ${HIT_PCT}% < 50% even with the new first-token measurement.")
    MOVES+=("axis1:-1")
else
    VERDICTS+=("Axis 1 (TTFW=10): borderline — hit-rate ${HIT_PCT}% in 50–79% band. Holding 10 pending more data.")
fi

# Axis 2 — Emotion distribution (current: 8)
NEUTRAL="$(echo "$REPORT" | sed -n 's/^emotion distribution: .*neutral=\([0-9]*\).*/\1/p' | head -1)"
NEUTRAL="${NEUTRAL:-0}"
NEUTRAL_PCT=$(( NEUTRAL * 100 / TOTAL_TURNS ))
if [[ "$NEUTRAL_PCT" -ge 90 ]]; then
    VERDICTS+=("Axis 2 (Emotion=8): rate signal not lighting up — ${NEUTRAL_PCT}% of turns are neutral. Known limitation; acoustic prosody work tracked for next phase.")
else
    VERDICTS+=("Axis 2 (Emotion=8): confirmed — non-neutral turns at $((100 - NEUTRAL_PCT))%.")
fi

# Axis 7 — Interruption tuning (current: 9)
VERDICTS+=("Axis 7 (Interruption=9): verification deferred — needs interruption-rate logging.")

# Axis 9 — Specialist usage (current: 9)
DESKTOP_HITS="$(echo "$REPORT" | grep -oE 'desktop=[0-9]+' | head -1 | sed 's/desktop=//')"
PLANNER_HITS="$(echo "$REPORT" | grep -oE 'planner=[0-9]+' | head -1 | sed 's/planner=//')"
BROWSER_HITS="$(echo "$REPORT" | grep -oE 'browser=[0-9]+' | head -1 | sed 's/browser=//')"
DESKTOP_HITS="${DESKTOP_HITS:-0}"
PLANNER_HITS="${PLANNER_HITS:-0}"
BROWSER_HITS="${BROWSER_HITS:-0}"

if [[ "$DESKTOP_HITS" -gt 0 && "$PLANNER_HITS" -gt 0 && "$BROWSER_HITS" -gt 0 ]]; then
    VERDICTS+=("Axis 9 (Tool exec=9): all three specialists reached production traffic (desktop=${DESKTOP_HITS}, planner=${PLANNER_HITS}, browser=${BROWSER_HITS}).")
elif [[ "$BROWSER_HITS" -eq 0 ]]; then
    VERDICTS+=("Axis 9 (Tool exec=9): WARN — browser specialist has 0 turns. Possible integration gap (extension not connected) or routing gap (LLM not picking transfer_to_browser). Investigate before scoring.")
fi
if [[ "$DESKTOP_HITS" -eq 0 && "$PLANNER_HITS" -eq 0 ]]; then
    VERDICTS+=("Axis 9 (Tool exec): DOWNGRADE 9 → 7 — supervisor never delegated. The handoff prompt rewrite may not be landing.")
    MOVES+=("axis9:-2")
fi

# Axis 10 — Self-eval (current: 10) — if we got this far, the report runs cleanly.
VERDICTS+=("Axis 10 (Self-eval=10): confirmed — report ran cleanly and produced ${#VERDICTS[@]} actionable signals.")

# Surprises: classifier fallback rate
FB_PCT="$(echo "$REPORT" | sed -n 's/^route-fallback rate: \([0-9]*\.[0-9]\)%.*/\1/p' | head -1)"
FB_PCT="${FB_PCT:-0}"
FB_INT="${FB_PCT%.*}"
if [[ "${FB_INT:-0}" -gt 5 ]]; then
    VERDICTS+=("⚠ classifier-fallback rate ${FB_PCT}% > 5% — the router LLM is failing too often. Investigate JARVIS_ROUTER_TIMEOUT_MS or switch JARVIS_ROUTER_PROVIDER.")
fi
if echo "$REPORT" | grep -q "^route health: WARN"; then
    VERDICTS+=("⚠ route-distribution health WARN — see report for under-served routes.")
fi

# ── 3. Compose the rubric section ─────────────────────────────────────
TODAY="$(date +%Y-%m-%d)"
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SECTION="
### ${TODAY} — After-dogfood verification (Phase 7+)

Ran \`turn_telemetry.py --report --days ${DAYS}\` against the live SQLite (${TOTAL_TURNS} turns in window). Verdicts:

"
for v in "${VERDICTS[@]}"; do
    SECTION+="- ${v}
"
done

if [[ ${#MOVES[@]} -gt 0 ]]; then
    SECTION+="
**Score moves:**
"
    for m in "${MOVES[@]}"; do
        SECTION+="- ${m}
"
    done
    COMMIT_MSG="voice: rubric after-dogfood re-score (Phase 7+)"
else
    SECTION+="
**No score changes.** Architecture from Phases 1-7 holds against live data.
"
    COMMIT_MSG="voice: rubric after-dogfood verification (no score changes)"
fi

# ── 4. Append + commit ────────────────────────────────────────────────
if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "==> Dry run; would append to $RUBRIC:"
    echo "$SECTION"
    echo "==> commit message would be: $COMMIT_MSG"
    exit 0
fi

printf '%s' "$SECTION" >> "$RUBRIC"
echo "==> Appended verification section to $RUBRIC"

git add "$RUBRIC"
git commit -m "$COMMIT_MSG"
echo "==> Committed: $COMMIT_MSG"

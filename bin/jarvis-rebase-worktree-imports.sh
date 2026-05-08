#!/usr/bin/env bash
#
# Rebase a worktree's voice-agent imports onto the post-RFC-001-Stage-A+B
# layout. Runs the same longest-prefix-first replacement table that the
# Stage A + Stage B patches used, against any directory the caller names.
#
# Why this exists: the four worktrees active on 2026-05-05
# (kimi-supreme, news-widget, screen-watching, voice-quality) all
# reference module paths that no longer exist on `main` because Stage A
# moved 10 tool modules to `tools/` and Stage B moved 19 more into
# `resilience/`, `sanitizers/`, `taps/`, `pipeline/`, `tts/`. Merging
# the worktree without rebasing those imports first produces 3-36
# conflicts depending on the worktree (kimi-supreme is the worst).
# Run THIS in the worktree before merging into main and the merge
# becomes trivial.
#
# Usage:
#   bin/jarvis-rebase-worktree-imports.sh                  # runs in cwd
#   bin/jarvis-rebase-worktree-imports.sh path/to/worktree # runs there
#   bin/jarvis-rebase-worktree-imports.sh --dry-run        # show planned edits, change nothing
#
# Safe to re-run; sed substitutions are idempotent against already-
# rewritten paths. Skips __pycache__ and node_modules and .venv. Does
# not write to .git internals.
#
# RFC-001 Stage A + B authority. F-arch-008 in JARVIS-REPAIR/03-STATE.md.

set -euo pipefail

DRY_RUN=0
TARGET=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -*) echo "unknown flag: $arg" >&2; exit 2 ;;
    *) TARGET="$arg" ;;
  esac
done
TARGET="${TARGET:-$PWD}"

if [[ ! -d "$TARGET" ]]; then
  echo "not a directory: $TARGET" >&2
  exit 2
fi

cd "$TARGET"

# Files to edit: every .py outside .venv / __pycache__ / .worktrees
# (we don't recurse into nested worktrees) / node_modules / .git.
mapfile -t FILES < <(
  find . -type f -name '*.py' \
    -not -path '*/.venv/*' \
    -not -path '*/__pycache__/*' \
    -not -path '*/.worktrees/*' \
    -not -path '*/node_modules/*' \
    -not -path '*/.git/*'
)

# Pre-filter: only files that contain at least one legacy import path.
# Avoids touching the 99% of files that are unaffected.
mapfile -t TOUCHED < <(
  printf '%s\n' "${FILES[@]}" | xargs -I{} grep -lE \
    'jarvis_(browser|computer_use|github|log_analyzer|memory|validator|code_reviewer)|^(import|from) (circuit_breaker|watchdog|reconnect_ladder|llm_idle_timeout|livekit_track_guard|dsml_sanitizer|pycall_sanitizer|tool_name_sanitizer|handoff_text_suppressor|deepseek_roundtrip|acoustic_tap|vision_tap|turn_router|turn_graph|turn_telemetry|dispatching_llm|dispatching_tts|edge_tts_plugin|canned_phrases)\b' \
    {} 2>/dev/null
)

if (( ${#TOUCHED[@]} == 0 )); then
  echo "[rebase] no files in $TARGET reference legacy import paths — nothing to do."
  exit 0
fi

echo "[rebase] target: $TARGET"
echo "[rebase] files needing rewrite: ${#TOUCHED[@]}"
if (( DRY_RUN )); then
  echo "[rebase] DRY RUN — listing affected files:"
  printf '  %s\n' "${TOUCHED[@]}"
  exit 0
fi

# Replacement table — Stage A first (tool modules), Stage B second
# (resilience, sanitizers, taps, pipeline, tts). Longest-first inside
# each stage to avoid jarvis_memory eating jarvis_memory_recall.
SED_PROG=$(cat <<'EOF'
# ── Stage A: tool modules (jarvis_<X>.py → tools.<X>) ─────────
s/jarvis_memory_recall/tools.memory_recall/g
s/jarvis_browser_ext/tools.browser_ext/g
s/jarvis_browser_v2/tools.browser_v2/g
s/jarvis_code_reviewer/tools.code_reviewer/g
s/jarvis_computer_use/tools.computer_use/g
s/jarvis_log_analyzer/tools.log_analyzer/g
s/jarvis_validator/tools.validator/g
s/jarvis_browser/tools.browser/g
s/jarvis_github/tools.github/g
s/jarvis_memory/tools.memory/g

# ── Stage B: resilience/ ──────────────────────────────────────
s/^import livekit_track_guard\b/import resilience.track_guard/g
s/^from livekit_track_guard\b/from resilience.track_guard/g
s/import livekit_track_guard as /import resilience.track_guard as /g
s/^import circuit_breaker\b/import resilience.circuit_breaker/g
s/^from circuit_breaker\b/from resilience.circuit_breaker/g
s/    from circuit_breaker\b/    from resilience.circuit_breaker/g
s/^import reconnect_ladder\b/import resilience.reconnect_ladder/g
s/^from reconnect_ladder\b/from resilience.reconnect_ladder/g
s/    from reconnect_ladder\b/    from resilience.reconnect_ladder/g
s/^import llm_idle_timeout\b/import resilience.llm_idle_timeout/g
s/^from llm_idle_timeout\b/from resilience.llm_idle_timeout/g
s/    from watchdog\b/    from resilience.watchdog/g
s/from watchdog import/from resilience.watchdog import/g
s/from circuit_breaker import/from resilience.circuit_breaker import/g
s/from reconnect_ladder import/from resilience.reconnect_ladder import/g
s/from llm_idle_timeout import/from resilience.llm_idle_timeout import/g

# ── Stage B: sanitizers/ (suffix stripped) ────────────────────
s/^import handoff_text_suppressor\b/import sanitizers.handoff_text/g
s/^from handoff_text_suppressor\b/from sanitizers.handoff_text/g
s/^import tool_name_sanitizer\b/import sanitizers.tool_name as tool_name_sanitizer/g
s/^from tool_name_sanitizer\b/from sanitizers.tool_name/g
s/^import pycall_sanitizer\b/import sanitizers.pycall as pycall_sanitizer/g
s/^from pycall_sanitizer\b/from sanitizers.pycall/g
s/^import dsml_sanitizer\b/import sanitizers.dsml as dsml_sanitizer/g
s/^from dsml_sanitizer\b/from sanitizers.dsml/g
s/^import deepseek_roundtrip\b/import sanitizers.deepseek_roundtrip as deepseek_roundtrip/g
s/^from deepseek_roundtrip\b/from sanitizers.deepseek_roundtrip/g

# ── Stage B: taps/ (suffix stripped) ──────────────────────────
s/^import acoustic_tap\b/import taps.acoustic as acoustic_tap/g
s/^from acoustic_tap\b/from taps.acoustic/g
s/^import vision_tap\b/import taps.vision as vision_tap/g
s/^from vision_tap\b/from taps.vision/g
s/"vision_tap\./"taps.vision./g

# ── Stage B: pipeline/ (names unchanged inside the package) ───
s/^import turn_router\b/import pipeline.turn_router as turn_router/g
s/^from turn_router\b/from pipeline.turn_router/g
s/^import turn_graph\b/import pipeline.turn_graph as turn_graph/g
s/^from turn_graph\b/from pipeline.turn_graph/g
s/^import turn_telemetry\b/import pipeline.turn_telemetry as turn_telemetry/g
s/^from turn_telemetry\b/from pipeline.turn_telemetry/g
s/^import dispatching_llm\b/import pipeline.dispatching_llm as dispatching_llm/g
s/^from dispatching_llm\b/from pipeline.dispatching_llm/g
s/^import dispatching_tts\b/import pipeline.dispatching_tts as dispatching_tts/g
s/^from dispatching_tts\b/from pipeline.dispatching_tts/g

# ── Stage B: tts/ (edge_tts_plugin → edge) ────────────────────
s/^import edge_tts_plugin\b/import tts.edge as edge_tts_plugin/g
s/^from edge_tts_plugin\b/from tts.edge/g
s/^import canned_phrases\b/import tts.canned_phrases as canned_phrases/g
s/^from canned_phrases\b/from tts.canned_phrases/g
EOF
)

# Apply sed to each affected file. -i for in-place; the sed program
# above uses GNU sed syntax (g flag, anchors). On macOS users would
# need gsed; the worktrees here are on Linux so plain sed -i works.
for f in "${TOUCHED[@]}"; do
  sed -i -e "$SED_PROG" "$f"
  echo "  rewrote $f"
done

echo "[rebase] done. ${#TOUCHED[@]} files updated. Run the test suite + a 'git diff' before committing."

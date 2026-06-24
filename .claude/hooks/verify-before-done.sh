#!/usr/bin/env bash
# .claude/hooks/verify-before-done.sh
# Stop hook for JARVIS — runs the relevant test suite for files edited
# during a session and blocks turn-end if any suite fails.
# Spec: docs/superpowers/specs/2026-05-07-regression-prevention-design.md

set -uo pipefail

REPO_ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null)" || REPO_ROOT="$PWD"
cd "$REPO_ROOT" || exit 0

# 1. Read stdin JSON
INPUT="$(cat)"
TRANSCRIPT_PATH="$(jq -r '.transcript_path // empty' <<<"$INPUT" 2>/dev/null)"
STOP_HOOK_ACTIVE="$(jq -r '.stop_hook_active // false' <<<"$INPUT" 2>/dev/null)"

# 2. Recursion guard — never block twice
[[ "$STOP_HOOK_ACTIVE" == "true" ]] && exit 0

# 3. Escape hatch
[[ "${JARVIS_SKIP_VERIFY:-0}" == "1" ]] && exit 0

# 4. Edit detection — extract unique edited file paths from transcript JSONL
[[ -z "$TRANSCRIPT_PATH" || ! -f "$TRANSCRIPT_PATH" ]] && exit 0

EDITED_FILES="$(jq -r '
  select(.message.content) | .message.content[]?
  | select(.type=="tool_use" and (.name=="Edit" or .name=="Write" or .name=="MultiEdit"))
  | .input.file_path
' "$TRANSCRIPT_PATH" 2>/dev/null | sort -u)"

[[ -z "$EDITED_FILES" ]] && exit 0

# 5. Subtree classification
declare -A SUITES=()
WARN_CLI=0
while IFS= read -r f; do
  case "$f" in
    */src/voice-agent/*) SUITES["voice-agent"]=1 ;;
    */src/voice-agent/desktop-tauri/*) SUITES["desktop-tauri"]=1 ;;
    */src/web/*) SUITES["web"]=1 ;;
    */src/cli/*) WARN_CLI=1 ;;
  esac
done <<<"$EDITED_FILES"

# 6. Run suites — collect failures into parallel arrays
FAIL_NAMES=()
FAIL_CMDS=()
FAIL_OUTS=()

run_suite() {
  local name="$1" cmd="$2"
  shift 2
  if ! "$@" >/dev/null 2>&1; then
    echo "[verify-before-done] WARN: $name prereq missing — skipping" >&2
    return 0
  fi
  echo "[verify-before-done] running $name…" >&2
  local out_file
  out_file="$(mktemp)"
  if ! bash -c "$cmd" >"$out_file" 2>&1; then
    FAIL_NAMES+=("$name")
    FAIL_CMDS+=("$cmd")
    FAIL_OUTS+=("$(tail -n 40 "$out_file")")
  fi
  rm -f "$out_file"
}

if [[ -n "${SUITES[voice-agent]:-}" ]]; then
  run_suite "voice-agent" \
    "cd src/voice-agent && .venv/bin/python -m pytest tests/ -x --tb=line --no-header" \
    test -x src/voice-agent/.venv/bin/python
fi
if [[ -n "${SUITES[desktop-tauri]:-}" ]]; then
  # `npm run build` alone produces dist/ but does NOT re-embed it into
  # the Rust binary. The .claude/rules/desktop-tauri.md rule says both
  # steps are required to ship JS changes. `cargo check --release`
  # catches cfg drift, Rust-side syntax errors, and the embed step's
  # codegen, in ~30s incrementally without the full link cost.
  run_suite "desktop-tauri" \
    "cd src/voice-agent/desktop-tauri && npm run build && cd src-tauri && cargo check --release" \
    test -d src/voice-agent/desktop-tauri/node_modules
fi
if [[ -n "${SUITES[web]:-}" ]]; then
  run_suite "web" \
    "cd src/web && npm run test" \
    test -x src/web/node_modules/.bin/vitest
fi

# 7. CLI warning (non-blocking)
if [[ "$WARN_CLI" == "1" ]]; then
  echo "[verify-before-done] WARN: CLI files edited. CLAUDE.md says src/cli/ is off-limits without asking — was this intentional?" >&2
fi

# 8. Decision
if [[ ${#FAIL_NAMES[@]} -eq 0 ]]; then
  exit 0
fi

# 9. Build block JSON for Claude Code
REASON="Verification failed before claiming done. Address these and re-run:"
for i in "${!FAIL_NAMES[@]}"; do
  REASON+=$'\n\n--- '"${FAIL_NAMES[$i]}"' (run: '"${FAIL_CMDS[$i]}"') ---\n'"${FAIL_OUTS[$i]}"
done

jq -n --arg reason "$REASON" '{decision: "block", reason: $reason}'
exit 0

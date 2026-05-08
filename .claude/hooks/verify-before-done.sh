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
TRANSCRIPT_PATH="$(jq -r '.transcript_path // empty' <<<"$INPUT")"
STOP_HOOK_ACTIVE="$(jq -r '.stop_hook_active // false' <<<"$INPUT")"

# 2. Recursion guard — never block twice
[[ "$STOP_HOOK_ACTIVE" == "true" ]] && exit 0

# 3. Escape hatch
[[ "${JARVIS_SKIP_VERIFY:-0}" == "1" ]] && exit 0

# 4. Edit detection (filled in by Task 3)
exit 0

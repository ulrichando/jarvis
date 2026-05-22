#!/usr/bin/env bash
# Canonical JARVIS voice-agent test runner. Run this instead of bare `pytest`
# so a local run matches CI exactly (.github/workflows/voice-agent-tests.yml).
#
# Usage:
#   scripts/run_tests.sh                    # full suite, CI flags
#   scripts/run_tests.sh tests/test_x.py    # a subset — args passed straight through
#   scripts/run_tests.sh -k "memory or web" # any pytest args
#
# JARVIS-native adaptation of the upstream run_tests.sh (venv discovery + a
# pytest wrapper). The voice-agent has its own pinned .venv (see CLAUDE.md /
# .claude/rules/voice-agent.md — never use the system Python or root venv).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VA_DIR="$REPO_ROOT/src/voice-agent"
PYTHON="$VA_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "error: voice-agent venv python not found at $PYTHON" >&2
  echo "  create it:  cd $VA_DIR && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

cd "$VA_DIR"

# Subset / custom run: pass every arg straight through to pytest.
if [[ $# -gt 0 ]]; then
  exec "$PYTHON" -m pytest "$@"
fi

# Full run — mirror the CI flags exactly (voice-agent-tests.yml):
#   -q              quiet
#   --durations=10  surface the 10 slowest tests
#   --timeout=60    one hung test can't wedge the run
#   --ignore=...    skip the github-subagent test as CI does
exec "$PYTHON" -m pytest tests/ -q --durations=10 --timeout=60 \
  --ignore=tests/test_github_subagent.py

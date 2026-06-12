#!/usr/bin/env bash
# Container-session setup for the jarvis repo (run by the /code "Run setup
# script" init step inside the jarvis-workbench image — node:20 + bun + pnpm
# + python3; see src/web/src/lib/bridge/containers.ts).
#
# Best-effort by design: a failed optional step prints a warning instead of
# failing the whole session init — the agent can finish installs itself once
# the session is up. Keep this script idempotent and fast; it runs on every
# fresh container (no snapshot caching yet, decisions-pending §12).
set -uo pipefail

note() { printf '\n[setup] %s\n' "$*"; }
warn() { printf '\n[setup] WARNING: %s\n' "$*" >&2; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Web app (Next.js) — needed for src/web work, vitest, and next build.
if [ -f src/web/package.json ]; then
  note "Installing src/web dependencies (npm ci)…"
  (cd src/web && npm ci --no-audit --no-fund) || warn "src/web npm ci failed"
fi

# CLI (Bun) — needed for src/cli work and its bun tests.
if [ -f src/cli/package.json ] && command -v bun >/dev/null 2>&1; then
  note "Installing src/cli dependencies (bun install)…"
  (cd src/cli && bun install --frozen-lockfile) || warn "src/cli bun install failed"
fi

# Voice agent (Python) — deliberately NOT installed here: the LiveKit/audio
# dependency tree is heavy and most container sessions don't run the voice
# runtime. To work on it inside a session:
#   cd src/voice-agent && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
note "Skipping src/voice-agent python deps (heavy; install on demand — see comment)."

note "Setup finished."
exit 0

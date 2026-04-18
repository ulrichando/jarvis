#!/usr/bin/env bash
# Starts the Jarvis speech sidecar (STT + TTS proxy to Groq) on port 8766.
# Loads GROQ_API_KEY from src/cli/.env.local if present.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$DESKTOP_DIR/../.." && pwd)"
ENV_FILE="$PROJECT_ROOT/src/cli/.env.local"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if ! command -v bun >/dev/null 2>&1; then
  echo "[speech] bun not found in PATH — install bun or set PATH" >&2
  exit 1
fi

exec bun "$DESKTOP_DIR/server/speech.ts"

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

# Find a bun — check PATH, project-vendored, and ~/.bun/bin in that order.
# Login sessions (autostart) don't include ~/.bun/bin in PATH, so we must
# fall back to absolute paths instead of relying on `command -v bun`.
VENDORED_BUN="$PROJECT_ROOT/src/cli/vendor/bun/linux-x64/bun"
BUN=""
if   command -v bun >/dev/null 2>&1;        then BUN="$(command -v bun)"
elif [ -x "$VENDORED_BUN" ];                then BUN="$VENDORED_BUN"
elif [ -x "$HOME/.bun/bin/bun" ];           then BUN="$HOME/.bun/bin/bun"
fi

if [ -z "$BUN" ]; then
  echo "[speech] bun not found — looked in PATH, $VENDORED_BUN, ~/.bun/bin" >&2
  exit 1
fi

exec "$BUN" "$DESKTOP_DIR/server/speech.ts"

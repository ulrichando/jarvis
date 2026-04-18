#!/usr/bin/env bash
# Jarvis Desktop Launcher
# Starts: proxy (4000) + bridge (8765) + Tauri desktop
# Switch provider: ./start-desktop.sh groq|deepseek|openai|gemini|ollama

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."
PROJECT_ROOT="$(cd "$ROOT/../.." && pwd)"
BUN="$SCRIPT_DIR/bunw.sh"
DESKTOP_BIN_RELEASE="$PROJECT_ROOT/src/desktop-tauri/src-tauri/target/release/jarvis-desktop"
DESKTOP_BIN_DEBUG="$PROJECT_ROOT/src/desktop-tauri/src-tauri/target/debug/jarvis-desktop"
if [ -x "$DESKTOP_BIN_RELEASE" ]; then
  DESKTOP_BIN="$DESKTOP_BIN_RELEASE"
else
  DESKTOP_BIN="$DESKTOP_BIN_DEBUG"
fi

# Load API keys
if [ -f "$ROOT/.env.local" ]; then
  set -a
  source "$ROOT/.env.local"
  set +a
fi

case "${1:-}" in
  deepseek|groq|openai|gemini|ollama)
    export JARVIS_PROVIDER="$1"
    shift
    ;;
  *)
    export JARVIS_PROVIDER="${JARVIS_PROVIDER:-deepseek}"
    ;;
esac

export JARVIS_DISABLE_AUTH="${JARVIS_DISABLE_AUTH:-1}"
export JARVIS_MODEL_REGISTRY_ENABLED=1

# ── Kill stale processes ──────────────────────────────────────────────
pkill -f "bun.*proxy/server.ts" 2>/dev/null || true
pkill -f "bun.*bridge/server.ts" 2>/dev/null || true
pkill -f "jarvis-desktop" 2>/dev/null || true
sleep 1

# ── Start proxy (4000) ────────────────────────────────────────────────
"$BUN" "$ROOT/src/proxy/server.ts" &>/tmp/jarvis-proxy.log &
PROXY_PID=$!

for i in $(seq 1 15); do
  curl -s http://localhost:4000/health >/dev/null 2>&1 && break
  sleep 1
done
echo "[jarvis] proxy up on :4000 (provider: $JARVIS_PROVIDER)"

# ── Start bridge (8765) ───────────────────────────────────────────────
"$BUN" "$ROOT/src/bridge/server.ts" &>/tmp/jarvis-bridge.log &
BRIDGE_PID=$!

for i in $(seq 1 15); do
  curl -s http://localhost:8765/health >/dev/null 2>&1 && break
  sleep 1
done
echo "[jarvis] bridge up on :8765"

# ── Launch desktop ────────────────────────────────────────────────────
if [ ! -x "$DESKTOP_BIN" ]; then
  echo "[jarvis] desktop binary not found at $DESKTOP_BIN"
  echo "[jarvis] build it with: cd $PROJECT_ROOT/src/desktop-tauri && npm run tauri build"
  kill $PROXY_PID $BRIDGE_PID 2>/dev/null || true
  exit 1
fi

echo "[jarvis] launching desktop..."
trap "kill $PROXY_PID $BRIDGE_PID 2>/dev/null" EXIT
# WebKit workarounds for tauri:// custom protocol on Linux:
# - WEBKIT_DISABLE_COMPOSITING_MODE: fixes rendering on some GPUs
# - WEBKIT_DISABLE_DMABUF_RENDERER: fallback renderer (fixes blank/error pages)
DISPLAY=${DISPLAY:-:0} \
  WEBKIT_DISABLE_DMABUF_RENDERER=1 \
  "$DESKTOP_BIN"

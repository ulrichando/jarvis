#!/usr/bin/env bash
# JARVIS desktop launcher — used by the .desktop menu entry.
# Ensures the whole voice stack is running, then starts the desktop:
#   • Proxy         :4000  — LLM router (Anthropic-compat → Groq/DeepSeek/…)
#   • Bridge        :8765  — legacy WS for browser UI / model-switch
#   • Speech sidecar:8766  — STT + agent + TTS for voice
# Each is only started if not already listening. Idempotent.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$DESKTOP_DIR/../.." && pwd)"
CLI_DIR="$PROJECT_ROOT/src/cli"
BUN="$CLI_DIR/vendor/bun/linux-x64/bun"
BIN="$DESKTOP_DIR/src-tauri/target/release/jarvis-desktop"
SPEECH_LAUNCH="$DESKTOP_DIR/scripts/start-speech.sh"
ENV_FILE="$CLI_DIR/.env.local"

if [ ! -x "$BIN" ]; then
  notify-send "JARVIS" "Binary missing — run: cd $DESKTOP_DIR/src-tauri && cargo build --release" 2>/dev/null || true
  exit 1
fi

# ── Helpers ───────────────────────────────────────────────────────────
# wait_port <port> — return 0 as soon as /health on the given port answers,
# up to ~8 s. Used after spawning a backend so we only move on once live.
wait_port() {
  local port=$1
  for _ in $(seq 1 16); do
    curl -sS -m 1 "http://127.0.0.1:$port/health" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  return 1
}

# start_bun_bg <log> <ts-entry>
# Launches a Bun script in a detached session with .env.local sourced.
start_bun_bg() {
  local log=$1
  local script=$2
  nohup bash -c "set -a; [ -f '$ENV_FILE' ] && . '$ENV_FILE'; set +a; exec '$BUN' '$script'" \
    </dev/null >"$log" 2>&1 &
  disown || true
}

# ── Bring up the backend stack, in order ──────────────────────────────
if ! curl -sS -m 1 http://127.0.0.1:4000/health >/dev/null 2>&1; then
  start_bun_bg /tmp/jarvis-proxy.log "$CLI_DIR/src/proxy/server.ts"
  wait_port 4000 || notify-send "JARVIS" "Proxy failed to start (see /tmp/jarvis-proxy.log)" 2>/dev/null || true
fi

if ! curl -sS -m 1 http://127.0.0.1:8765/health >/dev/null 2>&1; then
  start_bun_bg /tmp/jarvis-bridge.log "$CLI_DIR/src/bridge/server.ts"
  wait_port 8765 || notify-send "JARVIS" "Bridge failed to start (see /tmp/jarvis-bridge.log)" 2>/dev/null || true
fi

if ! curl -sS -m 1 http://127.0.0.1:8766/health >/dev/null 2>&1; then
  setsid bash "$SPEECH_LAUNCH" </dev/null >/tmp/jarvis-speech.log 2>&1 &
  disown || true
  wait_port 8766 || notify-send "JARVIS" "Speech sidecar failed to start (see /tmp/jarvis-speech.log)" 2>/dev/null || true
fi

# If a desktop instance is already up, confirm it still has mic capture.
# A silent failure mode we've seen: Tauri process is alive but the WebKit
# audio session got dropped, so Silero VAD is dead and voice never works.
# Detect by checking PulseAudio/PipeWire for a live source-output owned
# by a WebKitWebProcess whose parent is jarvis-desktop. If missing → kill
# and relaunch so Silero re-initialises from scratch.
if pgrep -x jarvis-desktop >/dev/null 2>&1; then
  MIC_OK=0
  for out in $(pactl list short source-outputs 2>/dev/null | awk '{print $1}'); do
    client_pid=$(pactl list source-outputs 2>/dev/null \
      | awk -v o="$out" '$0 ~ "Source Output #"o {p=1} p && /application.process.id/ {gsub(/"/,""); print $3; exit}')
    [ -z "$client_pid" ] && continue
    # Walk up the process tree: is jarvis-desktop an ancestor?
    cur=$client_pid
    while [ -n "$cur" ] && [ "$cur" != "1" ]; do
      name=$(ps -o comm= -p "$cur" 2>/dev/null)
      if [ "$name" = "jarvis-desktop" ]; then MIC_OK=1; break; fi
      cur=$(ps -o ppid= -p "$cur" 2>/dev/null | tr -d ' ')
      [ "$cur" = "0" ] && break
    done
    [ "$MIC_OK" = 1 ] && break
  done
  if [ "$MIC_OK" = 1 ]; then
    command -v xdotool >/dev/null && xdotool search --name "J.A.R.V.I.S." windowactivate 2>/dev/null || true
    exit 0
  fi
  echo "[launch] mic session dead on existing desktop — relaunching" >&2
  pkill -x jarvis-desktop
  sleep 1
fi

exec "$BIN"

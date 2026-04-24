#!/usr/bin/env bash
# JARVIS desktop launcher — used by the .desktop menu entry.
# Ensures the backend services are running, then starts the desktop.
# Voice lives in a native LiveKit-peer process (jarvis-voice-client) —
# not in this launcher. Services:
#   • Proxy    :4000  — LLM router (Anthropic-compat → Groq/DeepSeek/…)
#   • Bridge   :8765  — WS + REST API for the Tauri UI
# Each is only started if not already listening. Idempotent.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$DESKTOP_DIR/../.." && pwd)"
CLI_DIR="$PROJECT_ROOT/src/cli"
BUN="$CLI_DIR/vendor/bun/linux-x64/bun"
BIN="$DESKTOP_DIR/src-tauri/target/release/jarvis-desktop"
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
# Appends to the log (>>) so history survives restarts for postmortem.
start_bun_bg() {
  local log=$1
  local script=$2
  printf '\n\n=== start %s %s ===\n' "$(date -Iseconds)" "$script" >>"$log"
  nohup bash -c "set -a; [ -f '$ENV_FILE' ] && . '$ENV_FILE'; set +a; exec '$BUN' '$script'" \
    </dev/null >>"$log" 2>&1 &
  disown || true
}

# ── Backend stack is owned by systemd user units ──────────────────────
# Proxy (4000), bridge (8765), voice agent + voice client, and
# mic_aec / sink_aec routing are all managed by systemd user units.
# Install the stack once with:
#   systemctl --user enable --now \
#     jarvis-proxy jarvis-bridge jarvis-audio-defaults \
#     livekit-server jarvis-voice-agent jarvis-voice-client
#
# This launcher just confirms the two ports the Tauri UI needs (4000
# + 8765) are alive; if a unit hasn't come up yet (e.g. cold boot)
# we nudge it via systemctl. Voice services are handled entirely by
# their own units — no inline fallback here.
for port_host in "4000:proxy" "8765:bridge"; do
  port="${port_host%%:*}"
  name="${port_host##*:}"
  wait_port "$port" && continue
  if systemctl --user cat "jarvis-${name}.service" >/dev/null 2>&1; then
    systemctl --user start "jarvis-${name}.service" >/dev/null 2>&1 || true
    wait_port "$port" || notify-send "JARVIS" "jarvis-${name} failed to come up" 2>/dev/null || true
  else
    # Pre-systemd fallback — runs inline so a fresh clone still boots
    # a usable app before the user has run `systemctl enable`. Once
    # the units are installed this block is skipped (wait_port above
    # already returned 0).
    case "$name" in
      proxy)  start_bun_bg /tmp/jarvis-proxy.log "$CLI_DIR/src/proxy/server.ts"; wait_port 4000 || true ;;
      bridge) start_bun_bg /tmp/jarvis-bridge.log "$CLI_DIR/src/bridge/server.ts"; wait_port 8765 || true ;;
    esac
  fi
done

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

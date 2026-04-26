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

# ── Concurrency guard ─────────────────────────────────────────────────
# When the user clicks the launcher icon several times in quick
# succession, each invocation can race past the "is instance running?"
# check below before any has spawned the binary, ending in 2-3
# jarvis-desktop processes. flock with non-blocking mode means the
# first launcher grabs the lock and runs; concurrent ones exit
# immediately.
#
# IMPORTANT: the fd holding the lock MUST be closed before we exec
# the desktop binary, otherwise the exec inherits the fd and the
# desktop process holds the lock for its entire lifetime — blocking
# every subsequent launcher click "forever". `exec {LOCK_FD}>&-`
# at the end of the script (and before the final `exec "$BIN"`)
# closes it cleanly. flock(2) drops the lock when no fd references
# the file anymore.
LOCK_FILE="${XDG_RUNTIME_DIR:-/tmp}/jarvis-launcher.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "[launch] another launcher in progress; exiting" >&2
  exit 0
fi
release_lock() {
  flock -u 200 2>/dev/null || true
  exec 200>&-
}

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
# This launcher confirms the HTTP ports the Tauri UI needs are alive
# (4000 = proxy, 8765 = bridge) and nudges any stopped units via
# systemctl. After the proxy/bridge nudge it also (re)starts the
# voice agent + voice client since the tray's "Quit JARVIS" stops
# them — without this block, clicking the launcher icon after a
# previous Quit would bring up the window but leave JARVIS deaf.
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

# ── Voice services ────────────────────────────────────────────────────
# Started in pairs because the voice-client races the voice-agent on
# fresh starts: LiveKit dispatches a job only when a participant joins,
# so if the client connects before the agent has registered with the
# SFU, no dispatch fires and the pill is stuck on "JARVIS booting".
# Avoid the race by starting the agent FIRST, giving it a moment to
# register, THEN starting the voice-client whose preflight room-delete
# forces a fresh dispatch into the now-ready agent.
if ! systemctl --user is-active --quiet jarvis-voice-agent; then
  systemctl --user start jarvis-voice-agent >/dev/null 2>&1 || true
  sleep 3   # let the worker register with LiveKit before client connects
fi
# Always (re)start the voice-client when the launcher fires — a restart
# is ~1 s and its preflight delete_room shakes loose any zombie agent
# participants left over from a prior session.
systemctl --user restart jarvis-voice-client >/dev/null 2>&1 || true
wait_port 8767 || true

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
    # An instance is already up and the mic is alive. Just raising the
    # window is invisible (the overlay is transparent + click-through
    # everywhere except the chat panel), so the user clicks the launcher
    # and "nothing happens". Instead, fire the same global hotkey the
    # tray uses (Ctrl+Shift+Space) to toggle the chat panel — that's
    # what they actually want when re-clicking the launcher icon.
    if command -v xdotool >/dev/null; then
      xdotool search --name "J.A.R.V.I.S." windowactivate 2>/dev/null || true
      xdotool key --clearmodifiers ctrl+shift+space 2>/dev/null || true
    fi
    release_lock
    exit 0
  fi
  echo "[launch] mic session dead on existing desktop — relaunching" >&2
  pkill -x jarvis-desktop
  sleep 1
fi

release_lock
exec "$BIN"

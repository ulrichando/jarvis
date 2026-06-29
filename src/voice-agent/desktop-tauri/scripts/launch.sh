#!/usr/bin/env bash
# JARVIS desktop launcher — used by the .desktop menu entry.
# Ensures the backend services are running, then starts the desktop.
# Voice lives in a native LiveKit-peer process (jarvis-voice-client) —
# not in this launcher. Services:
#   • Proxy    :4000  — LLM router (Anthropic-compat → Groq/DeepSeek/…)
#   • Bridge   :8765  — WS + REST API for the Tauri UI
#   • Web      :3001  — Next.js dev server (the JARVIS web UI)
# Each is only started if not already listening. Idempotent. Matches
# what JupyterLab Desktop / Docker Desktop / VS Code Server do — the
# launcher owns the lifecycle of its dependencies so the user never
# has to remember to start them in a separate terminal.
set -u

# Maya-class speech intelligence defaults — override by setting in env.
: "${JARVIS_DISPATCH_DISABLED:=0}"
: "${JARVIS_ROUTER_ENABLED:=1}"
: "${JARVIS_ROUTER_TIMEOUT_MS:=500}"
: "${JARVIS_ROUTER_MODEL:=qwen/qwen3.6-27b}"
: "${JARVIS_VOICE_BANTER:=austin}"
: "${JARVIS_VOICE_TASK:=troy}"
: "${JARVIS_VOICE_REASONING:=troy}"
: "${JARVIS_VOICE_EMOTIONAL:=daniel}"
: "${JARVIS_TELEMETRY_PATH:=$HOME/.local/share/jarvis/turn_telemetry.db}"

# Local bridge bearer token. File is KEY=VALUE format (chmod 600);
# safe to source directly. Empty when the file isn't present yet —
# the bridge ignores the value unless JARVIS_REQUIRE_LOCAL_AUTH=1.
if [ -r "$HOME/.jarvis/local-api-token.env" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.jarvis/local-api-token.env"
  export JARVIS_LOCAL_API_TOKEN
fi

# Machine-local desktop overrides (NOT committed — per-machine, like
# local-api-token.env above). Point the tray's "Open in Browser" at the
# DEPLOYED web app instead of a local dev server:
#   JARVIS_WEB_URL=https://0wlan.com   (main.rs returns it before probing localhost)
#   JARVIS_WEB_AUTO_START=false        (skip spawning the local Next.js you don't need)
if [ -r "$HOME/.jarvis/desktop.env" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.jarvis/desktop.env"
fi
# Export (empty when unset → main.rs falls back to probing localhost, unchanged).
export JARVIS_WEB_URL="${JARVIS_WEB_URL:-}"

export JARVIS_DISPATCH_DISABLED JARVIS_ROUTER_ENABLED JARVIS_ROUTER_TIMEOUT_MS \
       JARVIS_ROUTER_MODEL \
       JARVIS_VOICE_BANTER JARVIS_VOICE_TASK JARVIS_VOICE_REASONING JARVIS_VOICE_EMOTIONAL \
       JARVIS_TELEMETRY_PATH

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
# jarvis-desktop processes. flock prevents this.
#
# Staleness detection: the lock file stores the holder's PID. We read
# it BEFORE opening for write (which would truncate it). If we can't
# acquire the lock after 5 s AND the stored PID is no longer alive,
# the lock is stale (left by a killed launcher). We clear and retry.
# This self-heals the failure mode where "the tray icon disappeared
# and clicking the launcher icon does nothing" — caused by a stuck
# launcher process that held the lock without ever reaching the binary.
#
# IMPORTANT: the fd holding the lock MUST be closed before we exec
# the desktop binary, otherwise the exec inherits the fd and the
# desktop process holds the lock for its entire lifetime — blocking
# every subsequent launcher click "forever". `exec 200>&-` at the end
# of the script (and before the final `exec "$BIN"`) closes it cleanly.
# flock(2) drops the lock when no fd references the file anymore.
LOCK_FILE="${XDG_RUNTIME_DIR:-/tmp}/jarvis-launcher.lock"

# Snapshot the prior holder's PID BEFORE we truncate the file on open.
_prior_holder=$(head -1 "$LOCK_FILE" 2>/dev/null | tr -d '[:space:]')
exec 200>"$LOCK_FILE"
if ! flock -w 5 200; then
  # Still locked after 5 s. Check if the recorded holder is alive.
  if [ -n "$_prior_holder" ] && kill -0 "$_prior_holder" 2>/dev/null; then
    echo "[launch] another launcher already running (pid=$_prior_holder); exiting" >&2
    exec 200>&-
    exit 0
  fi
  # Holder PID is gone — stale lock. Clear the file and try once more.
  echo "[launch] stale lock (holder pid=${_prior_holder:-unknown} is dead); clearing" >&2
  exec 200>&-
  rm -f "$LOCK_FILE"
  exec 200>"$LOCK_FILE"
  if ! flock -w 3 200; then
    echo "[launch] could not acquire lock after clearing stale; exiting" >&2
    exec 200>&-
    exit 0
  fi
fi
# Record our PID so future launchers can detect our liveness.
printf '%s\n' "$$" >&200

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

# start_web_bg <log>
# Launches the Next.js dev server in src/web from a detached session.
# Different shape from start_bun_bg because `bun run dev` needs a cwd
# (src/web) to resolve package.json's "dev" script, plus we use the
# system `bun` for the web (matches how the user runs it from a
# terminal — vendored bun is for the CLI only).
start_web_bg() {
  local log=$1
  local web_dir="$PROJECT_ROOT/src/web"
  if [ ! -d "$web_dir" ]; then
    echo "[launch] web dir missing at $web_dir — skipping" >&2
    return 1
  fi
  if ! command -v bun >/dev/null 2>&1; then
    echo "[launch] bun not in PATH — web auto-start skipped" >&2
    return 1
  fi
  printf '\n\n=== start %s next dev ===\n' "$(date -Iseconds)" >>"$log"
  ( cd "$web_dir" && \
    nohup bash -c "set -a; [ -f '$ENV_FILE' ] && . '$ENV_FILE'; set +a; exec bun run dev" \
      </dev/null >>"$log" 2>&1 & disown ) || true
}

# is_jarvis_web_live — check if a JARVIS web is responding on a known
# port. Mirrors the tray's probe logic: TCP connect → GET /api/conversations
# → expect 200 + application/json (so we don't false-positive on a stale
# Open-WebUI / random server on the same port).
is_jarvis_web_live() {
  for port in 3001 3002 3000; do
    local body
    body=$(curl -sS -m 1 -o /dev/null -w '%{http_code} %{content_type}' \
      "http://127.0.0.1:$port/api/conversations" 2>/dev/null) || continue
    case "$body" in
      "200 application/json"*) return 0 ;;
    esac
  done
  return 1
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

# ── Web UI (Next.js dev server on :3001) ────────────────────────────────
# Spawn the JARVIS web in the background if it's not already running.
# This is what JupyterLab Desktop / Docker Desktop / VS Code Server do —
# the launcher owns the lifecycle so the tray's "Open in Browser" always
# has something live to open.
#
# Idempotent: if the user already has `bun run dev` running in a terminal
# (or another launcher invocation already started one), is_jarvis_web_live
# returns 0 and we skip. No port collision possible.
#
# Non-blocking: we DON'T wait_port here. Next.js cold-compile is 5-15 s
# on first run, and blocking would stall the tray boot for that long.
# By the time the user clicks "Open in Browser", compile is usually
# done. If they click during compile, the tray's diagnostic window
# (web-not-running.html) shows a friendly "starting up" message.
#
# The user can opt out via JARVIS_WEB_AUTO_START=false (skip spawn,
# tray will show diagnostic when web is needed) or override the URL
# via JARVIS_WEB_URL=https://… in the binary's env.
if [ "${JARVIS_WEB_AUTO_START:-true}" = "true" ]; then
  if is_jarvis_web_live; then
    echo "[launch] jarvis web already up — skipping spawn" >&2
  else
    echo "[launch] spawning jarvis web (Next.js dev) in background" >&2
    start_web_bg /tmp/jarvis-web.log
  fi
fi

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

# Capture stdout+stderr to a persistent log so we can postmortem
# crashes. XDG autostart pipes the launcher's outputs to /dev/null,
# so without this redirect a Tauri panic / GTK fault leaves no trace
# (the tray just disappears with no log). Append mode preserves
# history across launches; the startup marker delineates runs.
DESKTOP_LOG=/tmp/jarvis-desktop.log
printf '\n\n=== launch %s pid=%s ===\n' "$(date -Iseconds)" "$$" >>"$DESKTOP_LOG"
exec "$BIN" >>"$DESKTOP_LOG" 2>&1

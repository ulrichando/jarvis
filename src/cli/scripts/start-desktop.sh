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

# Load API keys. Order (bash `KEY=value` source = last-wins on collision):
#   1) repo-root .env       — centralized LLM provider keys
#                             (consolidated 2026-05-15)
#   2) .env.local            — per-machine overlay (proxy flags etc.)
for envfile in "$PROJECT_ROOT/.env" "$ROOT/.env.local"; do
  if [ -f "$envfile" ]; then
    set -a
    source "$envfile"
    set +a
  fi
done
# Also load ~/.jarvis/keys.env (user-local secret store, gitignored).
# Mirrors the voice-agent's `_load_user_keys_env()` pattern. Values
# here OVERRIDE .env.local on collision, so newer keys placed here
# (e.g. ANTHROPIC_API_KEY for the proxy's anthropic-native passthrough)
# take effect without editing the repo's .env files.
if [ -f "$HOME/.jarvis/keys.env" ]; then
  set -a
  source "$HOME/.jarvis/keys.env"
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

# Bridge auth (added 2026-05-16 per global review §P0-1). Without this,
# any local process or malicious web page can mint LiveKit JWTs, hijack
# the OpenAI model, and execute Chrome-extension actions on every
# authenticated tab. Generate a 32-byte token at install/first-run; the
# bridge (server.ts:64-76) requires it on /api/* + the /ws upgrade.
TOKEN_FILE="${HOME}/.jarvis/local-api-token.env"
if [ ! -f "$TOKEN_FILE" ]; then
  mkdir -p "${HOME}/.jarvis"
  umask 077
  printf 'JARVIS_LOCAL_API_TOKEN=%s\n' \
    "$(head -c 32 /dev/urandom | base64 | tr -d '+/=' | head -c 43)" \
    > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
  echo "[jarvis] generated $TOKEN_FILE (chmod 600)"
fi
# Load + export the token. The bridge reads JARVIS_LOCAL_API_TOKEN from
# its env; voice-agent reads from the same file via systemd EnvironmentFile
# already wired in jarvis-voice-agent.service.
# shellcheck disable=SC1090
. "$TOKEN_FILE"
export JARVIS_LOCAL_API_TOKEN
export JARVIS_REQUIRE_LOCAL_AUTH=1
echo "[jarvis] bridge auth ENABLED (JARVIS_REQUIRE_LOCAL_AUTH=1)"

# ── Idempotent unit-file sync (added 2026-05-17) ──────────────────────
# Auto-deploy any setup/systemd/*.service or *.timer changes on every
# launch. Without this, hub sandbox tweaks / timer-unit changes ship
# in git but never reach systemd until the user re-runs install.sh.
# Pattern: diff repo template vs installed unit; if different, cp +
# daemon-reload. Already-running services are NOT restarted (those
# stay live with their old definition until the user explicitly
# restarts them — see CLAUDE.md operational rule on voice-agent).
USER_SYSTEMD="$HOME/.config/systemd/user"
SETUP_SYSTEMD="$PROJECT_ROOT/setup/systemd"
if [ -d "$SETUP_SYSTEMD" ] && [ -d "$USER_SYSTEMD" ]; then
  changed=0
  for src in "$SETUP_SYSTEMD"/*.service "$SETUP_SYSTEMD"/*.timer; do
    [ -f "$src" ] || continue
    name="$(basename "$src")"
    dst="$USER_SYSTEMD/$name"
    if [ ! -f "$dst" ] || ! cmp -s "$src" "$dst"; then
      # Run the same sed-path-subs install.sh does. Inline since
      # this script can't source install.sh's functions.
      sed -e "s|%h/Documents/Projects/jarvis|$PROJECT_ROOT|g" \
          -e "s|/home/[^/]*/Documents/Projects/jarvis|$PROJECT_ROOT|g" \
          -e "s|/home/[^/]*/jarvis|$PROJECT_ROOT|g" \
          "$src" > "$dst" && echo "[jarvis] unit updated: $name" && changed=1
    fi
  done
  if [ "$changed" = "1" ]; then
    systemctl --user daemon-reload && echo "[jarvis] systemd daemon reloaded"
    # Enable any newly-shipped timer units (idempotent).
    for unit in jarvis-backup-local.timer jarvis-log-rotate.timer jarvis-retention-prune.timer; do
      if [ -f "$USER_SYSTEMD/$unit" ] && ! systemctl --user is-enabled "$unit" >/dev/null 2>&1; then
        systemctl --user enable --now "$unit" >/dev/null 2>&1 \
          && echo "[jarvis] enabled $unit"
      fi
    done
  fi
fi

# ── Kill stale processes ──────────────────────────────────────────────
pkill -f "bun.*proxy/server.ts" 2>/dev/null || true
pkill -f "bun.*bridge/server.ts" 2>/dev/null || true
pkill -f "jarvis-desktop" 2>/dev/null || true
sleep 1

# ── Ensure voice services are up ──────────────────────────────────────
# The Tauri tray's Quit handler (src-tauri/src/main.rs::handle_quit)
# stops jarvis-voice-agent + jarvis-voice-client + jarvis-bridge +
# jarvis-proxy as one symmetric shutdown. Without a corresponding
# start here, re-running this launcher after a Quit leaves voice-agent
# and voice-client down — the desktop boots cleanly but the user
# can't talk to JARVIS. Idempotent: systemctl start on an active unit
# is a no-op, so this is safe to re-run on every launcher invocation.
# Kicked off before proxy/bridge so the voice-agent's ~10s plugin
# preload runs in parallel with the bun startups.
for unit in jarvis-voice-agent.service jarvis-voice-client.service; do
  if systemctl --user list-unit-files "$unit" --quiet 2>/dev/null; then
    if ! systemctl --user is-active --quiet "$unit" 2>/dev/null; then
      if systemctl --user start "$unit" 2>/dev/null; then
        echo "[jarvis] started $unit"
      else
        echo "[jarvis] WARN: failed to start $unit (see journalctl --user -u $unit)" >&2
      fi
    fi
  fi
done

# ── Start proxy (4000) with auto-respawn ─────────────────────────────
# Background supervisor — keeps the proxy alive across crashes. Live
# failure 2026-05-29: proxy died silently mid-session; the bridge kept
# routing chat queries to :4000, every reply came back as Bun's
# "Unable to connect. Is the computer able to access the url?" string
# (visible verbatim in the JARVIS chat panel). Supervisor respawns
# with a 2s back-off, capped at 5 restarts per 30s window — past that
# the proxy is assumed broken (env missing, port conflict, syntax
# error) and we stop pegging the CPU.
# Output: the supervisor's notices AND the child's stdout/stderr both
# append to /tmp/jarvis-proxy.log. Truncate once at script start so
# each launcher invocation starts with a fresh log.
: > /tmp/jarvis-proxy.log
(
  # The parent script runs under `set -euo pipefail`, which propagates
  # into this subshell. Disable -e here: `wait $PROXY_CHILD` returns
  # the child's exit code, and a non-zero crash would otherwise abort
  # the supervisor on the very first failure — the opposite of what we
  # want. The supervisor's whole job is to loop on non-zero exits.
  set +e
  PROXY_RESTARTS=0
  PROXY_WINDOW_START=$(date +%s)
  while true; do
    "$BUN" "$ROOT/src/proxy/server.ts" >>/tmp/jarvis-proxy.log 2>&1 &
    PROXY_CHILD=$!
    # Forward SIGTERM/SIGINT from the parent's EXIT trap to the bun
    # child so the tree dies cleanly on Quit. `wait` is signal-
    # interruptible only when a trap is registered.
    trap "kill $PROXY_CHILD 2>/dev/null; exit 0" TERM INT
    wait $PROXY_CHILD
    EC=$?
    NOW=$(date +%s)
    if (( NOW - PROXY_WINDOW_START > 30 )); then
      PROXY_WINDOW_START=$NOW
      PROXY_RESTARTS=0
    fi
    PROXY_RESTARTS=$((PROXY_RESTARTS + 1))
    if (( PROXY_RESTARTS > 5 )); then
      echo "[jarvis-proxy-sup] proxy crashed $PROXY_RESTARTS times in <30s; giving up (check env / port conflict)" \
        >>/tmp/jarvis-proxy.log
      break
    fi
    echo "[jarvis-proxy-sup] proxy exited (code=$EC); respawn #$PROXY_RESTARTS in 2s" \
      >>/tmp/jarvis-proxy.log
    sleep 2
  done
) &
PROXY_SUP_PID=$!

for i in $(seq 1 15); do
  curl -s http://localhost:4000/health >/dev/null 2>&1 && break
  sleep 1
done
echo "[jarvis] proxy up on :4000 (provider: $JARVIS_PROVIDER, supervised pid=$PROXY_SUP_PID)"

# ── Start bridge (8765) ───────────────────────────────────────────────
"$BUN" "$ROOT/src/bridge/server.ts" &>/tmp/jarvis-bridge.log &
BRIDGE_PID=$!

for i in $(seq 1 15); do
  curl -s http://localhost:8765/health >/dev/null 2>&1 && break
  sleep 1
done
echo "[jarvis] bridge up on :8765"

# ── Wait for the voice path to be READY (not just the services "active") ──
# `systemctl is-active` (above) only confirms the process is running; the
# voice-agent and voice-client both signal systemd READY=1 BEFORE the worker
# has joined the LiveKit room and the SFU connection is up. Launching the
# desktop into a not-yet-ready voice path is a big chunk of the "JARVIS works
# after a relaunch sometimes, not others" intermittency. Poll the voice-client
# /status until BOTH connected AND agent_present are true (the agent has joined
# the room and can hear/speak), bounded by a timeout so a genuinely-broken
# voice path never blocks the desktop forever — it launches anyway with a warn.
# Override the timeout via JARVIS_VOICE_READY_TIMEOUT (seconds).
VOICE_READY_TIMEOUT="${JARVIS_VOICE_READY_TIMEOUT:-30}"
echo "[jarvis] waiting for voice path (connected + agent present, <=${VOICE_READY_TIMEOUT}s)..."
voice_ready=0
for i in $(seq 1 "$VOICE_READY_TIMEOUT"); do
  vstatus=$(curl -s --max-time 1 http://127.0.0.1:8767/status 2>/dev/null)
  if printf '%s' "$vstatus" | grep -q '"connected": *true' \
     && printf '%s' "$vstatus" | grep -q '"agent_present": *true'; then
    voice_ready=1
    echo "[jarvis] voice path ready after ~${i}s"
    break
  fi
  sleep 1
done
if [ "$voice_ready" != 1 ]; then
  echo "[jarvis] WARN: voice path not ready after ${VOICE_READY_TIMEOUT}s — launching desktop anyway (voice may still come up; check: tail -f ~/.local/share/jarvis/logs/voice-agent.log)" >&2
fi

# ── Launch desktop ────────────────────────────────────────────────────
if [ ! -x "$DESKTOP_BIN" ]; then
  echo "[jarvis] desktop binary not found at $DESKTOP_BIN"
  echo "[jarvis] build it with: cd $PROJECT_ROOT/src/desktop-tauri && npm run tauri build"
  kill $PROXY_SUP_PID $BRIDGE_PID 2>/dev/null || true
  exit 1
fi

echo "[jarvis] launching desktop..."
# EXIT trap: TERM the proxy supervisor (its own TERM trap reaps the
# bun child), then TERM the bridge. pkill -P catches any straggler
# child of the supervisor if its trap raced the parent's kill.
trap "kill $PROXY_SUP_PID $BRIDGE_PID 2>/dev/null; pkill -P $PROXY_SUP_PID 2>/dev/null" EXIT
# WebKit rendering for tauri:// custom protocol on Linux:
# - Hardware-accelerated compositing is LEFT ON: we deliberately do NOT
#   set WEBKIT_DISABLE_COMPOSITING_MODE. The kiosk's WebGL aura-ring
#   visualizer needs the GPU to render smoothly; with compositing
#   disabled it fell back to the CPU path and stuttered badly. Re-enabled
#   2026-05-29 at the user's request (kiosk-ring lag fix).
#   TRADE-OFF: WEBKIT_DISABLE_COMPOSITING_MODE=1 was the documented fix
#   (tauri#10566/#12800/#13157) for "after-image" ghosting on the
#   TRANSPARENT overlay when the ChatPanel scrolled/remounted. The other
#   two parts of that fix remain and carry the overlay now: the <html>
#   rgba(0,0,0,0.01) baseline (index.html) and moving the ChatPanel into
#   its OWN opaque WebviewWindow. If overlay ghosting returns, either
#   re-add `WEBKIT_DISABLE_COMPOSITING_MODE=1 \` below (the kiosk aura
#   will lag again) or switch the kiosk to a non-WebGL visualizer.
# - WEBKIT_DISABLE_DMABUF_RENDERER was previously set here to "fix
#   blank/error pages" but per tauri#14924 it is itself a known *cause*
#   of transparent-window ghosting on some Mesa/Nvidia stacks. Removed
#   2026-05-29 after research; add back only if blank pages reappear.
DISPLAY=${DISPLAY:-:0} \
  "$DESKTOP_BIN"

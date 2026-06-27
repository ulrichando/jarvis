#!/usr/bin/env bash
# Jarvis launcher — run as `jarvis` or `bash scripts/start.sh groq`
# Switch models inside the session with /model

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."
BUN="$SCRIPT_DIR/bunw.sh"

# Strip env vars that nested Claude Code sessions leak (VSCode extension
# or Claude Desktop). If present, the inherited CLI detects "nested
# session" and silently bypasses ANTHROPIC_BASE_URL, hitting
# api.anthropic.com directly — which hangs for minutes when Ulrich's
# real Anthropic quota is missing. Wiping them here keeps the proxy
# route intact.
unset CLAUDE_CODE_EXECPATH CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING CLAUDECODE
for _v in $(env | awk -F= '/^CLAUDE_CODE_/{print $1}'); do unset "$_v"; done
for _v in $(env | awk -F= '/^CLAUDE_DESKTOP_/{print $1}'); do unset "$_v"; done

# Silence non-essential outbound calls to Anthropic / Statsig / Sentry.
# Main LLM traffic still flows through ANTHROPIC_BASE_URL (proxy).
# These only touch: update checks, telemetry, error reporting, cost
# warnings, feature-flag polling, /bug command. Fine to lose all of
# that in a self-hosted routing setup.
export DISABLE_TELEMETRY=1
export DISABLE_ERROR_REPORTING=1
export DISABLE_BUG_COMMAND=1
export DISABLE_NON_ESSENTIAL_MODEL_CALLS=1
export DISABLE_AUTOUPDATER=1
export DISABLE_COST_WARNINGS=1

# Load API keys. Order matters (bash `KEY=value` source semantics =
# last-source wins on collision):
#   1) repo-root .env  — centralized LLM provider keys
#                        (consolidated 2026-05-15)
#   2) .env.local      — per-machine overlay (JARVIS_PROVIDER,
#                        auth flags, anything subproject-specific)
# .env.providers was deleted 2026-05-15; its values were placeholder
# strings duplicated from .env.local / root .env, no information lost.
for envfile in "$ROOT/../../.env" "$ROOT/.env.local"; do
  if [ -f "$envfile" ]; then
    set -a
    source "$envfile"
    set +a
  fi
done
# Also load ~/.jarvis/keys.env (user-local secret store, gitignored).
# Mirrors the voice-agent's `_load_user_keys_env()` pattern and
# start-desktop.sh's sourcing order. Values here OVERRIDE .env / .env.local
# on collision (last-source-wins), so a rotated key placed in keys.env takes
# effect without editing the repo's .env files.
if [ -f "$HOME/.jarvis/keys.env" ]; then
  set -a
  source "$HOME/.jarvis/keys.env"
  set +a
fi

# ── Internal wiring (users never set these) ───────────────────────────────
# Resolve the active CLI model. Precedence:
#   1) explicit argv[1] (e.g. `jarvis groq`)
#   2) JARVIS_PROVIDER from .env.local
#   3) ~/.jarvis/cli-model — written by the desktop tray's "CLI Model"
#      submenu (see jarvis_voice_client.py /cli-model). Stores a model
#      ID like "deepseek-chat" or "qwen/qwen3-32b"; we map it to the
#      provider name start.sh expects.
case "${1:-}" in
  deepseek|groq|openai|gemini|ollama)
    SELECTED_PROVIDER="$1"
    SELECTED_MODEL=""
    shift
    ;;
  *)
    SELECTED_PROVIDER=""
    SELECTED_MODEL=""
    # Evolution build-model override: bin/jarvis-automod-impl exports
    # JARVIS_AUTOMOD_BUILD_MODEL (from ~/.jarvis/auto-mods/build-model) so
    # autonomous evolution builds can run on a DIFFERENT model than the
    # interactive CLI / tray pick. Takes precedence over cli-model below.
    if [ -n "${JARVIS_AUTOMOD_BUILD_MODEL:-}" ]; then
      case "$JARVIS_AUTOMOD_BUILD_MODEL" in
        deepseek-chat|deepseek-reasoner|deepseek-v4-flash|deepseek-v4-pro)
          SELECTED_PROVIDER="deepseek"
          SELECTED_MODEL="$JARVIS_AUTOMOD_BUILD_MODEL"
          ;;
        claude-opus-4-8|claude-sonnet-4-6|claude-haiku-4-5)
          SELECTED_PROVIDER="anthropic"
          SELECTED_MODEL="$JARVIS_AUTOMOD_BUILD_MODEL"
          ;;
        kimi-k2.7-code|kimi-k2.7-code-highspeed|kimi-k2.6|kimi-k2.6-instant|kimi-k2.6-thinking|kimi-k2.6-agent|kimi-k2.6-swarm)
          SELECTED_PROVIDER="kimi"
          SELECTED_MODEL="$JARVIS_AUTOMOD_BUILD_MODEL"
          ;;
        qwen/qwen3-32b|llama-3.3-70b-versatile|meta-llama/llama-4-scout-17b-16e-instruct|openai/gpt-oss-120b)
          SELECTED_PROVIDER="groq"
          SELECTED_MODEL="$JARVIS_AUTOMOD_BUILD_MODEL"
          ;;
      esac
    fi
    # Tray pick wins over .env.local's JARVIS_PROVIDER so the desktop
    # menu is the source of truth for "what model is JARVIS using".
    if [ -z "$SELECTED_MODEL" ] && [ -r "$HOME/.jarvis/cli-model" ]; then
      _cli_model="$(tr -d '[:space:]' < "$HOME/.jarvis/cli-model")"
      case "$_cli_model" in
        deepseek-chat|deepseek-reasoner|deepseek-v4-flash|deepseek-v4-pro)
          SELECTED_PROVIDER="deepseek"
          SELECTED_MODEL="$_cli_model"
          ;;
        claude-opus-4-8|claude-sonnet-4-6|claude-haiku-4-5)
          SELECTED_PROVIDER="anthropic"
          SELECTED_MODEL="$_cli_model"
          ;;
        kimi-k2.7-code|kimi-k2.7-code-highspeed|kimi-k2.6|kimi-k2.6-instant|kimi-k2.6-thinking|kimi-k2.6-agent|kimi-k2.6-swarm)
          SELECTED_PROVIDER="kimi"
          SELECTED_MODEL="$_cli_model"
          ;;
        qwen/qwen3-32b|llama-3.3-70b-versatile|meta-llama/llama-4-scout-17b-16e-instruct|openai/gpt-oss-120b)
          SELECTED_PROVIDER="groq"
          SELECTED_MODEL="$_cli_model"
          ;;
      esac
    fi
    SELECTED_PROVIDER="${SELECTED_PROVIDER:-${JARVIS_PROVIDER:-deepseek}}"
    ;;
esac

JARVIS_PERMISSION_MODE="${JARVIS_PERMISSION_MODE:-bypassPermissions}"
JARVIS_SANDBOX_ENABLED="${JARVIS_SANDBOX_ENABLED:-0}"

# Force bash for the CLI's Bash tool (and any other shell-out). If
# SHELL=zsh is inherited, unquoted URLs with "?" or "&" crash with
# "no matches found" because zsh's NOMATCH glob is enabled by default.
# Bash treats unmatched globs as literals, which is what URLs need.
export SHELL=/bin/bash

export ANTHROPIC_BASE_URL=http://localhost:4000
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-jarvis-proxy}"
# JARVIS proxy credential ("OAuth via login"): when `jarvis auth login` has
# provisioned a proxy token (sourced from ~/.jarvis/keys.env above), hand it to
# the Anthropic SDK as the auth token so requests go out as
# `Authorization: Bearer <token>` — which the proxy verifies when
# JARVIS_PROXY_AUTH_REQUIRED=1. Mirrors run-cli.mjs's mapping: start.sh launches
# cli.tsx DIRECTLY (not via run-cli.mjs), so without this the interactive
# `jarvis` path attaches no Bearer and an auth-required proxy 401s every request.
# Inert when the token is unset, so pre-login / fresh sessions are unchanged.
if [ -z "${ANTHROPIC_AUTH_TOKEN:-}" ] && [ -n "${JARVIS_PROXY_TOKEN:-}" ]; then
  export ANTHROPIC_AUTH_TOKEN="$JARVIS_PROXY_TOKEN"
fi
export JARVIS_PROVIDER="$SELECTED_PROVIDER"   # proxy default — /model overrides per-request
# When the cli-model file pinned a specific upstream model, surface
# it so the proxy uses that exact model rather than the provider's
# default. JARVIS_MODEL is empty when the user passed `jarvis groq`
# without a cli-model preference, in which case the registry default
# applies.
[ -n "$SELECTED_MODEL" ] && export JARVIS_MODEL="$SELECTED_MODEL"
export JARVIS_MODEL_REGISTRY_ENABLED=1
export JARVIS_DISABLE_AUTH="${JARVIS_DISABLE_AUTH:-1}"
export CLAUDE_CODE_MAX_OUTPUT_TOKENS=8000
export ENABLE_TOOL_SEARCH=true
# Non-Claude backends (Groq, DeepSeek) don't know the ToolSearch protocol
# and fail to call deferred tools (WebFetch, etc.) — ship every schema up front.
export JARVIS_DISABLE_TOOL_DEFERRAL="${JARVIS_DISABLE_TOOL_DEFERRAL:-1}"
export IS_DEMO=1
export DISABLE_INSTALLATION_CHECKS=1
# Enable the agent-teams / swarm feature for the (external-build) JARVIS user.
# isAgentSwarmsEnabled() (utils/agentSwarmsEnabled.ts) is always-on for USER_TYPE=ant
# but external builds must opt in via this env var. Unlocks the /swarm command,
# the TeamCreate/TeamDelete tools, and the footer 'teams' menu. The swarm backends
# (tmux / in-process) are intact and cross-platform.
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1

# /ultraplan against jarvis-web (Phase B). The teleport/ultraplan client is wired
# to talk to the local jarvis-web CCR-compat backend (src/web/.../api/v1/*) when
# JARVIS_CCR_BASE_URL is set, and /ultraplan surfaces when JARVIS_ULTRAPLAN=1.
# Left OFF by default until verified end-to-end against a running jarvis-web +
# bridge worker. To enable: run jarvis-web, then uncomment (match your web port):
#   export JARVIS_CCR_BASE_URL="http://127.0.0.1:3000/api"
#   export JARVIS_ULTRAPLAN=1
# and add the web origin to the systemd-run IPAddressAllow list below if non-local.
# Spec: docs/superpowers/specs/2026-06-27-jarvis-web-ccr-backend-design.md

if [ "$JARVIS_SANDBOX_ENABLED" = "1" ]; then
  JARVIS_FLAG_SETTINGS='{"sandbox":{"enabled":true}}'
else
  JARVIS_FLAG_SETTINGS='{"sandbox":{"enabled":false}}'
fi

# ── Built-in Jarvis model registry ────────────────────────────────────────
# Provider identities and picker entries now come from src/utils/model/jarvisModelRegistry.ts.

# ── Proxy (:4000) ─────────────────────────────────────────────────────────
# Prefer the persistent systemd --user service (jarvis-proxy.service): it's
# immortal (Restart=always) + shared, so a `jarvis` session must NOT spawn,
# supervise, or kill its own proxy — that fights the service (the EXIT pkill
# would drop the service's proxy; a 2nd proxy clashes on :4000). When the
# service is active, do nothing and let the health check below confirm it;
# otherwise fall back to a session-scoped proxy + bash respawn supervisor.
if command -v systemctl >/dev/null 2>&1 \
   && systemctl --user is-active --quiet jarvis-proxy.service 2>/dev/null; then
  echo "[jarvis] proxy: using persistent jarvis-proxy.service on :4000"
else
# Pre-flight: kill any orphaned proxy from a prior session that didn't
# clean up. Without this, the new proxy fails with EADDRINUSE, the new
# CLI silently connects to the OLD proxy (which still has the pre-rotation
# env vars), and you get phantom "invalid API key" errors after rotating
# credentials. Match by command line so we don't shoot some unrelated
# Bun process on the box.
STALE_PROXY=$(pgrep -f "$ROOT/src/proxy/server.ts" 2>/dev/null | head -1)
if [ -n "$STALE_PROXY" ] && kill -0 "$STALE_PROXY" 2>/dev/null; then
  kill -TERM "$STALE_PROXY" 2>/dev/null || true
  for _ in 1 2 3 4 5 6; do
    kill -0 "$STALE_PROXY" 2>/dev/null || break
    sleep 0.25
  done
  kill -KILL "$STALE_PROXY" 2>/dev/null || true
fi

# Start the proxy under a respawn SUPERVISOR so a mid-session crash
# self-heals instead of stranding the live CLI with "Unable to connect to
# API". start.sh historically started it once; start-desktop.sh has long
# had this supervisor (added 2026-05-29 after the proxy died silently
# mid-session) — this ports the same loop to the plain `jarvis` launcher.
# 2s back-off, capped at 5 restarts per 30s window; past that the proxy is
# assumed genuinely broken (env/port/syntax) and we stop pegging the CPU.
: > /tmp/jarvis-proxy.log
(
  # Parent runs under `set -euo pipefail`; disable -e here so a non-zero
  # proxy exit (the whole reason this loop exists) doesn't abort the
  # supervisor on the first crash.
  set +e
  PROXY_RESTARTS=0
  PROXY_WINDOW_START=$(date +%s)
  while true; do
    "$BUN" "$ROOT/src/proxy/server.ts" >>/tmp/jarvis-proxy.log 2>&1 &
    PROXY_CHILD=$!
    # Forward the parent's teardown to the bun child; `wait` is signal-
    # interruptible only while a trap is registered.
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

# Tear the supervisor (and its live proxy child) down when this launcher
# exits. The supervisor's own TERM trap kills its child; the pkill is a
# backstop for a child that outlived it. No `exec` below keeps this bash
# alive so the EXIT trap actually fires (a prior bug orphaned the proxy).
cleanup_proxy() {
  if [ -n "${PROXY_SUP_PID:-}" ] && kill -0 "$PROXY_SUP_PID" 2>/dev/null; then
    kill -TERM "$PROXY_SUP_PID" 2>/dev/null || true
    for _ in 1 2 3 4 5 6; do
      kill -0 "$PROXY_SUP_PID" 2>/dev/null || break
      sleep 0.25
    done
    kill -KILL "$PROXY_SUP_PID" 2>/dev/null || true
  fi
  pkill -f "$ROOT/src/proxy/server.ts" 2>/dev/null || true
}
trap cleanup_proxy EXIT
fi  # end: prefer systemd jarvis-proxy.service, else session-scoped supervisor

for i in $(seq 1 15); do
  if curl -s http://localhost:4000/health >/dev/null 2>&1; then break; fi
  sleep 1
done

# ── Launch CLI ────────────────────────────────────────────────────────────
# Wrap in a transient systemd --user scope with IPAddressDeny for the
# Anthropic address ranges. The CLI can still reach our proxy on
# 127.0.0.1:4000 (loopback is allowed by default) but any direct
# connection attempt to api.anthropic.com / claude.ai / console /
# bridge.claudeusercontent.com is blocked at the kernel eBPF layer.
# Only the CLI and its children are contained — doesn't affect the rest
# of the user session, including VSCode Claude Code.
CLI_CMD=( "$BUN"
  --feature=VOICE_MODE
  --feature=BRIDGE_MODE
  --feature=AGENT_TRIGGERS
  --feature=AGENT_TRIGGERS_REMOTE
  # ── Unlocked Claude-Code feature gates (see commands.ts / feature()) ──
  # These ship in the source but were dark because the external build never
  # passed their --feature= flag. Enabling the user-facing subset only; the
  # Anthropic-internal / phone-home flags (KAIROS*, *_USER_SETTINGS sync,
  # TEAMMEM, CHICAGO_MCP, telemetry/tracing) are deliberately left OFF.
  --feature=ULTRAPLAN
  --define 'MACRO.VERSION="2.1.107"'
  --define 'MACRO.BUILD_TIME=""'
  --define 'MACRO.PACKAGE_URL="@anthropic-ai/claude-code"'
  --define 'MACRO.NATIVE_PACKAGE_URL="@anthropic-ai/claude-code-native"'
  --define 'MACRO.ISSUES_EXPLAINER="report the issue at https://github.com/anthropics/claude-code/issues"'
  --define 'MACRO.FEEDBACK_CHANNEL="https://github.com/anthropics/claude-code/issues"'
  --define 'MACRO.VERSION_CHANGELOG=null'
  "$ROOT/src/entrypoints/cli.tsx"
  --settings "$JARVIS_FLAG_SETTINGS"
  --permission-mode "$JARVIS_PERMISSION_MODE"
  "$@" )

# NB: no `exec` here — see cleanup_proxy comment above. We must keep
# this bash process alive so the EXIT trap fires when the CLI exits.
if [ -z "${JARVIS_NO_SCOPE:-}" ] && command -v systemd-run >/dev/null 2>&1 && [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
  systemd-run --user --scope --quiet \
    --property=IPAddressDeny=2607:6bc0::/32 \
    --property=IPAddressDeny=160.79.104.0/22 \
    --property=IPAddressAllow=127.0.0.0/8 \
    --property=IPAddressAllow=::1/128 \
    --property=IPAddressAllow=10.0.0.0/8 \
    --property=IPAddressAllow=172.16.0.0/12 \
    --property=IPAddressAllow=192.168.0.0/16 \
    -- "${CLI_CMD[@]}"
else
  "${CLI_CMD[@]}"
fi
# Bash exits here → cleanup_proxy fires via EXIT trap.

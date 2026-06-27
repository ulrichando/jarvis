#!/usr/bin/env bash
# JARVIS shared env setup — sourced by start.sh (source run) and bin/jarvis (compiled binary)
# Resolves API keys, provider, model, proxy, feature flags.
# Must be sourced (not executed) so exports survive in the caller.

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
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1   # enable swarm/agent-teams

if [ "$JARVIS_SANDBOX_ENABLED" = "1" ]; then
  JARVIS_FLAG_SETTINGS='{"sandbox":{"enabled":true}}'
else
  JARVIS_FLAG_SETTINGS='{"sandbox":{"enabled":false}}'
fi

# ── End env setup ─────────────────────────────────────────────────────────

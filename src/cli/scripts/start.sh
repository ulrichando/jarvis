#!/usr/bin/env bash
# Jarvis launcher — run as `jarvis` or `bash scripts/start.sh groq`
# Switch models inside the session with /model

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."
BUN="$SCRIPT_DIR/bunw.sh"

# Load API keys
if [ -f "$ROOT/.env.local" ]; then
  set -a
  source "$ROOT/.env.local"
  set +a
fi

# ── Internal wiring (users never set these) ───────────────────────────────
case "${1:-}" in
  deepseek|groq|openai|gemini|ollama)
    SELECTED_PROVIDER="$1"
    shift
    ;;
  *)
    SELECTED_PROVIDER="${JARVIS_PROVIDER:-deepseek}"
    ;;
esac

JARVIS_PERMISSION_MODE="${JARVIS_PERMISSION_MODE:-bypassPermissions}"
JARVIS_SANDBOX_ENABLED="${JARVIS_SANDBOX_ENABLED:-0}"

export ANTHROPIC_BASE_URL=http://localhost:4000
export ANTHROPIC_API_KEY=jarvis-proxy
export JARVIS_PROVIDER="$SELECTED_PROVIDER"   # proxy default — /model overrides per-request
export JARVIS_MODEL_REGISTRY_ENABLED=1
export JARVIS_DISABLE_AUTH="${JARVIS_DISABLE_AUTH:-1}"
export CLAUDE_CODE_MAX_OUTPUT_TOKENS=8000
export ENABLE_TOOL_SEARCH=true
export IS_DEMO=1
export DISABLE_INSTALLATION_CHECKS=1

if [ "$JARVIS_SANDBOX_ENABLED" = "1" ]; then
  JARVIS_FLAG_SETTINGS='{"sandbox":{"enabled":true}}'
else
  JARVIS_FLAG_SETTINGS='{"sandbox":{"enabled":false}}'
fi

# ── Built-in Jarvis model registry ────────────────────────────────────────
# Provider identities and picker entries now come from src/utils/model/jarvisModelRegistry.ts.

# ── Start proxy ───────────────────────────────────────────────────────────
"$BUN" "$ROOT/src/proxy/server.ts" &>/tmp/jarvis-proxy.log &
PROXY_PID=$!
trap "kill $PROXY_PID 2>/dev/null" EXIT

for i in $(seq 1 15); do
  if curl -s http://localhost:4000/health >/dev/null 2>&1; then break; fi
  sleep 1
done

# ── Launch CLI ────────────────────────────────────────────────────────────
"$BUN" \
  --define 'MACRO.VERSION="2.1.107"' \
  --define 'MACRO.BUILD_TIME=""' \
  --define 'MACRO.PACKAGE_URL="@anthropic-ai/claude-code"' \
  --define 'MACRO.NATIVE_PACKAGE_URL="@anthropic-ai/claude-code-native"' \
  --define 'MACRO.ISSUES_EXPLAINER="report the issue at https://github.com/anthropics/claude-code/issues"' \
  --define 'MACRO.FEEDBACK_CHANNEL="https://github.com/anthropics/claude-code/issues"' \
  --define 'MACRO.VERSION_CHANGELOG=null' \
  "$ROOT/src/entrypoints/cli.tsx" \
  --settings "$JARVIS_FLAG_SETTINGS" \
  --permission-mode "$JARVIS_PERMISSION_MODE" \
  "$@"

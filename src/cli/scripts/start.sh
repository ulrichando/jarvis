#!/usr/bin/env bash
# Jarvis launcher (source run) — run as `bash scripts/start.sh groq`
# Switch models inside the session with /model
# For compiled binary, use bin/jarvis instead.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."
BUN="$SCRIPT_DIR/bunw.sh"

# ── Shared env setup (API keys, proxy, model, feature flags) ──────────────
source "$SCRIPT_DIR/start-env.sh"
source "$SCRIPT_DIR/proxy-runtime.sh"

# ── Built-in Jarvis model registry ────────────────────────────────────────
# Provider identities and picker entries now come from src/utils/model/jarvisModelRegistry.ts.

jarvis_proxy_start "$ROOT" "$BUN"
jarvis_proxy_wait_health

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
  --feature=COORDINATOR_MODE
  --feature=ULTRAPLAN
  --feature=WORKFLOW_SCRIPTS
  --feature=WEB_BROWSER_TOOL
  --feature=HISTORY_SNIP
  --feature=MONITOR_TOOL
  --feature=UDS_INBOX
  --feature=BUDDY
  --feature=FORK_SUBAGENT
  --feature=EXPERIMENTAL_SKILL_SEARCH
  --feature=TERMINAL_PANEL
  --feature=CONTEXT_COLLAPSE
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
# Bash exits here → jarvis_proxy_cleanup fires via EXIT trap when fallback proxy was used.

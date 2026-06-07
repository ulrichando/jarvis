#!/usr/bin/env bash
# Daily dependency health check for the JARVIS voice-agent venv.
#
# Three checks (two local, one network-gated):
#   1. MISSING — packages in requirements.txt not installed in the venv
#   2. SKEW    — livekit-agents version mismatch with its plugins
#   3. OUTDATED — pip list --outdated (gated by JARVIS_DEP_CHECK_FULL=1)
#
# Writes a structured JSON result to ~/.jarvis/dep-check/result.json.
# On findings, queues a voice-digest line to ~/.jarvis/cron/pending.jsonl
# so JARVIS can speak the results on the next session connect.
#
# Run via systemd user timer (jarvis-dep-check.timer) daily, OR
# manually: `bash scripts/jarvis-dep-check.sh`.
set -euo pipefail

ROOT="${HOME}/Documents/Projects/jarvis"
VA_DIR="${ROOT}/src/voice-agent"
VENV="${VA_DIR}/.venv"
VENV_PIP="${VENV}/bin/pip"
VENV_PY="${VENV}/bin/python"
REQ_FILE="${VA_DIR}/requirements.txt"
RESULT_DIR="${HOME}/.jarvis/dep-check"
RESULT_FILE="${RESULT_DIR}/result.json"
LOCK_FILE="${RESULT_DIR}/.check.lock"
IGNORE_FILE="${RESULT_DIR}/ignore.json"
PENDING_FILE="${HOME}/.jarvis/cron/pending.jsonl"

# ── pre-flight ───────────────────────────────────────────────────────

if [[ ! -d "$VENV" ]]; then
    echo "[$(date -Is)] dep-check: venv not found at $VENV — skipping"
    exit 0
fi

if [[ ! -x "$VENV_PIP" ]]; then
    echo "[$(date -Is)] dep-check: pip not executable at $VENV_PIP — skipping"
    exit 0
fi

mkdir -p "$RESULT_DIR"

# Lock to prevent concurrent runs (same flock pattern as auto-mod spawner).
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "[$(date -Is)] dep-check: lock held by another process — skipping"
    exit 0
fi

# ── helpers ──────────────────────────────────────────────────────────

now_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
status="ok"
outdated_json="[]"
skew_json="[]"
missing_json="[]"
errors_json="[]"

_append_error() {
    local msg="$1"
    errors_json=$(jq -c --arg msg "$msg" '. + [$msg]' <<<"$errors_json")
}

# Build ignore list: merge hardcoded defaults with user overrides.
# Hardcoded defaults are packages that are DELIBERATELY pinned to
# specific versions — flagging them as "outdated" is noise.
HARD_IGNORE=("setuptools" "agent-client-protocol")
declare -a IGNORE_PKGS=()
for p in "${HARD_IGNORE[@]}"; do IGNORE_PKGS+=("$p"); done
if [[ -f "$IGNORE_FILE" ]]; then
    while IFS= read -r p; do
        [[ -n "$p" ]] && IGNORE_PKGS+=("$p")
    done < <(jq -r '.[]' "$IGNORE_FILE" 2>/dev/null || true)
fi

_is_ignored() {
    local name="$1"
    for p in "${IGNORE_PKGS[@]}"; do
        [[ "$p" == "$name" ]] && return 0
    done
    return 1
}

_pip_list_json() {
    # Run pip list in the venv, return JSON array of {name, version}.
    "$VENV_PIP" list --format=json 2>/dev/null || echo "[]"
}

# ── check 1: MISSING packages ────────────────────────────────────────

if [[ -f "$REQ_FILE" ]]; then
    installed_names=$(_pip_list_json | jq -r '.[].name // empty' 2>/dev/null || true)
    # Parse requirements.txt for package names (handles extras like [silero,openai]).
    # Skips platform-conditional packages (e.g. "pywinauto; platform_system == \"Windows\"")
    # because they're expected to be missing on other platforms.
    declared=$("$VENV_PY" -c "
import re, sys
with open('$REQ_FILE') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        # Skip platform-conditional deps — expected missing on other OSes
        if ';' in line:
            continue
        # Strip version constraints and extras
        m = re.match(r'^([a-zA-Z0-9_.-]+(?:\[[a-zA-Z0-9_,.-]+\])?)', line)
        if m:
            print(m.group(1))
" 2>/dev/null || true)

    while IFS= read -r pkg; do
        [[ -z "$pkg" ]] && continue
        # Extract base name (without extras like [silero])
        base="${pkg%%\[*}"
        _is_ignored "$base" && continue
        # Check if installed (allow the extras-decorated name OR plain name)
        if ! echo "$installed_names" | grep -qxF "$pkg" && ! echo "$installed_names" | grep -qxF "$base"; then
            # Only flag livekit-plugins-* as critical — other missing deps
            # would have crashed the agent at import time.
            if [[ "$base" == livekit-plugins-* ]]; then
                status="crit"
            elif [[ "$status" != "crit" ]]; then
                status="warn"
            fi
            missing_json=$(jq -c \
                --arg name "$base" \
                --arg declared "$REQ_FILE" \
                '. + [{"name": $name, "declared_in": "requirements.txt"}]' \
                <<<"$missing_json")
        fi
    done <<<"$declared"
fi

# ── check 2: VERSION SKEW (livekit-agents vs plugins) ────────────────

livekit_version=$("$VENV_PIP" show livekit-agents 2>/dev/null | awk '/^Version:/{print $2}' || true)
if [[ -n "$livekit_version" ]]; then
    # Extract major.minor (e.g., "1.5" from "1.5.17")
    livekit_major_minor=$(echo "$livekit_version" | cut -d. -f1,2)

    plugin_list=$(_pip_list_json | jq -r '.[].name' | grep '^livekit-plugins-' || true)
    while IFS= read -r plugin; do
        [[ -z "$plugin" ]] && continue
        _is_ignored "$plugin" && continue
        plugin_version=$("$VENV_PIP" show "$plugin" 2>/dev/null | awk '/^Version:/{print $2}' || true)
        [[ -z "$plugin_version" ]] && continue
        plugin_major_minor=$(echo "$plugin_version" | cut -d. -f1,2)

        if [[ "$plugin_major_minor" != "$livekit_major_minor" ]]; then
            status="warn"
            skew_json=$(jq -c \
                --arg base "livekit-agents" \
                --arg base_ver "$livekit_version" \
                --arg plugin "$plugin" \
                --arg plugin_ver "$plugin_version" \
                '. + [{"base": $base, "base_version": $base_ver, "plugin": $plugin, "plugin_version": $plugin_ver}]' \
                <<<"$skew_json")
        fi
    done <<<"$plugin_list"
fi

# ── check 3: OUTDATED (network-gated) ─────────────────────────────────

if [[ "${JARVIS_DEP_CHECK_FULL:-0}" == "1" ]]; then
    outdated_raw=$("$VENV_PIP" list --outdated --format=json 2>/dev/null || echo "[]")
    # Filter out ignored packages and packages not in the skew/missing
    # lists that are just noise.
    outdated_json=$(
        echo "$outdated_raw" | jq -c --argjson ignore "$(printf '%s\n' "${IGNORE_PKGS[@]}" | jq -R . | jq -s .)" '
            [.[] | select(.name as $n | $ignore | index($n) | not)]
        ' 2>/dev/null || echo "[]"
    )
    outdated_count=$(echo "$outdated_json" | jq 'length' 2>/dev/null || echo 0)
    if [[ "$outdated_count" -gt 0 && "$status" == "ok" ]]; then
        status="warn"
    fi
else
    # Without network, capture only installed versions (no "latest" field).
    # We still include the installed map so the reader can see what's present.
    :
fi

# ── build installed map ──────────────────────────────────────────────

# Capture installed versions for key top-level packages.
# from_entries needs {key, value} objects; pip list emits {name, version}.
installed_json=$(_pip_list_json | jq -c '[.[] | select(.name | test("^(livekit|groq|openai|anthropic|deepgram|edge-tts|langchain|langgraph|playwright|redis|mcp|sdnotify|insightface|onnxruntime|honcho|psutil|google-genai)")) | {key: .name, value: .version}] | from_entries' 2>/dev/null || echo "{}")

# ── write result ─────────────────────────────────────────────────────

result=$(jq -nc \
    --arg check_ts "$now_utc" \
    --arg status "$status" \
    --arg venv_path "$VENV" \
    --argjson installed "$installed_json" \
    --argjson outdated "$outdated_json" \
    --argjson skew "$skew_json" \
    --argjson missing "$missing_json" \
    --argjson ignored "$(printf '%s\n' "${IGNORE_PKGS[@]}" | jq -R . | jq -s .)" \
    --argjson errors "$errors_json" \
    '{
        check_ts: $check_ts,
        check_version: 1,
        status: $status,
        venv_path: $venv_path,
        installed: $installed,
        outdated: $outdated,
        skew: $skew,
        missing: $missing,
        ignored: $ignored,
        errors: $errors
    }')

echo "$result" > "$RESULT_FILE"

# ── queue voice digest ───────────────────────────────────────────────

if [[ "$status" != "ok" ]]; then
    missing_count=$(echo "$missing_json" | jq 'length' 2>/dev/null || echo 0)
    skew_count=$(echo "$skew_json" | jq 'length' 2>/dev/null || echo 0)
    outdated_count=$(echo "$outdated_json" | jq 'length' 2>/dev/null || echo 0)

    parts=()
    [[ "$missing_count" -gt 0 ]] && parts+=("$missing_count package(s) MISSING")
    [[ "$skew_count" -gt 0 ]] && parts+=("$skew_count plugin(s) VERSION SKEW")
    [[ "$outdated_count" -gt 0 ]] && parts+=("$outdated_count package(s) outdated")

    summary="JARVIS dependency check: $(IFS=', '; echo "${parts[*]}")."
    detail=""
    if [[ "$missing_count" -gt 0 ]]; then
        detail+="Missing: $(echo "$missing_json" | jq -r '.[].name' | tr '\n' ' '). "
    fi
    if [[ "$skew_count" -gt 0 ]]; then
        detail+="Skew: $(echo "$skew_json" | jq -r '.[] | "\(.plugin)@\(.plugin_version) vs \(.base)@\(.base_version)"' | tr '\n' '; '). "
    fi

    pending_entry=$(jq -nc \
        --arg ts "$now_utc" \
        --arg title "Dependency Check" \
        --arg body "${summary} ${detail}" \
        --arg source "dep-check" \
        '{
            ts: $ts,
            title: $title,
            body: $body,
            source: $source,
            kind: "dep_check"
        }')
    echo "$pending_entry" >> "$PENDING_FILE" 2>/dev/null || true
fi

# ── log summary ──────────────────────────────────────────────────────

echo "[$(date -Is)] dep-check: status=${status} missing=$(echo "$missing_json" | jq 'length') skew=$(echo "$skew_json" | jq 'length') outdated=$(echo "$outdated_json" | jq 'length')"

# Release lock
exec 9>&-

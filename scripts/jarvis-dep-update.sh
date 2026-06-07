#!/usr/bin/env bash
# Auto-update outdated voice-agent dependencies.
#
# Gated behind JARVIS_DEP_AUTO_UPDATE=1 — exits silently otherwise.
# Respects ~= pins from requirements.txt (never upgrades past the declared
# constraint). Runs pip check after the update and only restarts
# jarvis-voice-agent.service if:
#   - pip check passes (no conflicts)
#   - no active session (<60s since last turn in turn_telemetry.db)
#
# Dry-run mode (JARVIS_DEP_AUTO_UPDATE_DRY_RUN=1) reports what it would
# update without touching the venv.
#
# Run via systemd user timer (jarvis-dep-update.timer) weekly, OR
# manually: `JARVIS_DEP_AUTO_UPDATE=1 bash scripts/jarvis-dep-update.sh`.
set -euo pipefail

ROOT="${HOME}/Documents/Projects/jarvis"
VA_DIR="${ROOT}/src/voice-agent"
VENV="${VA_DIR}/.venv"
VENV_PIP="${VENV}/bin/pip"
VENV_PY="${VENV}/bin/python"
REQ_FILE="${VA_DIR}/requirements.txt"
RESULT_FILE="${HOME}/.jarvis/dep-check/result.json"
LOG_FILE="${HOME}/.local/share/jarvis/logs/dep-update.log"
TELEMETRY_DB="${HOME}/.local/share/jarvis/turn_telemetry.db"

# ── gates ────────────────────────────────────────────────────────────

if [[ "${JARVIS_DEP_AUTO_UPDATE:-0}" != "1" ]]; then
    exit 0
fi

DRY_RUN="${JARVIS_DEP_AUTO_UPDATE_DRY_RUN:-0}"

# ── helpers ──────────────────────────────────────────────────────────

log() {
    echo "[$(date -Is)] $*" | tee -a "$LOG_FILE"
}

_parse_pin_for_package() {
    # Given a package base name, return the constraint line from requirements.txt
    # (e.g., "livekit-agents~=1.5") or empty string if not found.
    local name="$1"
    "$VENV_PY" -c "
import re, sys
name = '${name}'
with open('${REQ_FILE}') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        # Check if line starts with this package name (handles extras)
        m = re.match(r'^(' + re.escape(name) + r'(?:\[[^\]]*\])?\s*[~=>!<]+.*)', line)
        if m:
            print(m.group(1).strip())
            sys.exit(0)
# Fallback: print just the name (no constraint → pip install --upgrade <name>)
print(name)
" 2>/dev/null || echo "$name"
}

_check_active_session() {
    # Return 0 if a session is active (last turn <60s ago), 1 if safe to restart.
    if [[ ! -f "$TELEMETRY_DB" ]]; then
        return 1  # no DB = no sessions = safe
    fi
    local last_ts
    last_ts=$(sqlite3 "$TELEMETRY_DB" \
        "SELECT ts_utc FROM turns ORDER BY ts_utc DESC LIMIT 1;" 2>/dev/null || echo "")
    if [[ -z "$last_ts" ]]; then
        return 1  # no turns = safe
    fi
    local last_epoch now_epoch
    last_epoch=$(date -d "$last_ts" +%s 2>/dev/null || echo 0)
    now_epoch=$(date +%s)
    if [[ $((now_epoch - last_epoch)) -lt 60 ]]; then
        return 0  # active
    fi
    return 1  # stale
}

# ── main ─────────────────────────────────────────────────────────────

mkdir -p "$(dirname "$LOG_FILE")"

# Read the last check result to find what needs updating.
if [[ ! -f "$RESULT_FILE" ]]; then
    log "no dep-check result at $RESULT_FILE — run jarvis-dep-check check first"
    exit 0
fi

status=$(jq -r '.status // "ok"' "$RESULT_FILE")
if [[ "$status" == "ok" ]]; then
    log "dep-check status is ok — nothing to update"
    exit 0
fi

# Collect packages to update: missing + skewed + outdated.
to_update=()

# Missing packages — install with constraint from requirements.txt
while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    constraint=$(_parse_pin_for_package "$name")
    to_update+=("$constraint")
    log "queued MISSING: $constraint"
done < <(jq -r '.missing[]?.name // empty' "$RESULT_FILE")

# Skewed plugins — upgrade to match livekit-agents
while IFS= read -r plugin; do
    [[ -z "$plugin" ]] && continue
    constraint=$(_parse_pin_for_package "$plugin")
    to_update+=("$constraint")
    log "queued SKEW: $constraint"
done < <(jq -r '.skew[]?.plugin // empty' "$RESULT_FILE")

# Outdated packages
while IFS= read -r pkg; do
    [[ -z "$pkg" ]] && continue
    constraint=$(_parse_pin_for_package "$pkg")
    to_update+=("$constraint")
    log "queued OUTDATED: $constraint"
done < <(jq -r '.outdated[]?.name // empty' "$RESULT_FILE")

if [[ ${#to_update[@]} -eq 0 ]]; then
    log "no packages to update"
    exit 0
fi

# Deduplicate
mapfile -t to_update < <(printf '%s\n' "${to_update[@]}" | sort -u)

log "packages to update (${#to_update[@]}): ${to_update[*]}"

if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY RUN — would update these packages:"
    for p in "${to_update[@]}"; do
        log "  $p"
    done
    log "DRY RUN — no changes made. Set JARVIS_DEP_AUTO_UPDATE_DRY_RUN=0 to apply."
    exit 0
fi

# ── apply updates ────────────────────────────────────────────────────

failed=()
for pkg_spec in "${to_update[@]}"; do
    log "upgrading: $pkg_spec"
    if "$VENV_PIP" install --upgrade "$pkg_spec" >> "$LOG_FILE" 2>&1; then
        log "  OK: $pkg_spec"
    else
        log "  FAILED: $pkg_spec"
        failed+=("$pkg_spec")
    fi
done

# ── post-update validation ───────────────────────────────────────────

if "$VENV_PIP" check >> "$LOG_FILE" 2>&1; then
    log "pip check: PASSED"
else
    log "pip check: FAILED — dependencies are inconsistent, NOT restarting"
    log "Manual intervention needed. Run: cd src/voice-agent && .venv/bin/pip check"
    exit 1
fi

# ── safe restart ─────────────────────────────────────────────────────

if _check_active_session; then
    log "active session detected — NOT restarting voice-agent (will take effect next restart)"
    exit 0
fi

log "restarting jarvis-voice-agent.service"
if systemctl --user restart jarvis-voice-agent.service >> "$LOG_FILE" 2>&1; then
    log "restart OK"
else
    log "restart FAILED — check journalctl --user -u jarvis-voice-agent.service"
    exit 1
fi

log "update complete — ${#to_update[@]} package(s) updated, ${#failed[@]} failed"

#!/usr/bin/env bash
# JARVIS voice-agent container entrypoint.
#
# Bootstraps the bind-mounted data dirs and seeds keys.env on first boot,
# then exec's the voice-agent worker (or whatever CMD was overridden).

set -euo pipefail

JARVIS_DATA="${JARVIS_HOME:-/opt/jarvis-data}"
JARVIS_STATE="${XDG_DATA_HOME:-/opt/jarvis-state}"
INSTALL_DIR="/opt/jarvis"

# ── Data dirs the agent expects ───────────────────────────────────────
mkdir -p "$JARVIS_DATA"/{plugins,skills}
mkdir -p "$JARVIS_STATE/logs"

# ── Seed keys.env on first boot ──────────────────────────────────────
# Bind-mounted ~/.jarvis is empty on a fresh install; copy the template
# so the user has something to fill in. Subsequent restarts skip this
# (the file exists; never clobber a real keys.env).
if [ ! -f "$JARVIS_DATA/keys.env" ] && [ -f "$INSTALL_DIR/src/voice-agent/keys.env.example" ]; then
    cp "$INSTALL_DIR/src/voice-agent/keys.env.example" "$JARVIS_DATA/keys.env"
    chmod 600 "$JARVIS_DATA/keys.env" 2>/dev/null || true
    echo "Seeded keys.env from template at $JARVIS_DATA/keys.env — edit and restart."
fi

# Export it as an env source for the voice-agent (matches the host-side
# _load_user_keys_env() which reads ~/.jarvis/keys.env).
if [ -f "$JARVIS_DATA/keys.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$JARVIS_DATA/keys.env"
    set +a
fi

# ── Final exec ────────────────────────────────────────────────────────
# CMD from Dockerfile (or `docker compose run` override) lands here.
exec "$@"

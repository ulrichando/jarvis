#!/usr/bin/env bash
# setup-honcho.sh — provision self-hosted honcho for JARVIS cross-session memory.
#
# Honcho (plastic-labs/honcho) is the cross-session "deep recall" backend behind
# JARVIS's `recall` tool: it auto-syncs every message, builds a user model, and
# serves semantic recall. It is OPTIONAL and HEAVY — a Docker stack (honcho API +
# deriver + pgvector Postgres + redis) plus ongoing OpenAI cost (its deriver +
# embeddings). The file-backed memory (USER/MEMORY/PROCEDURES) works WITHOUT it.
#
# Idempotent: safe to re-run. Pins the honcho server to the tag matching the
# honcho-ai client in the voice venv (server v3.0.9 <-> client 2.1.2, per honcho's
# compatibility matrix). Run standalone, or via install.sh with JARVIS_INSTALL_HONCHO=1.
#
# Env overrides: JARVIS_REPO, HONCHO_DIR, HONCHO_REF, HONCHO_DB_HOST_PORT,
#                HONCHO_REDIS_HOST_PORT, HONCHO_API_PORT, HONCHO_LLM_KEY_VAR.
set -uo pipefail

REPO="${JARVIS_REPO:-$HOME/Documents/Projects/jarvis}"
HONCHO_DIR="${HONCHO_DIR:-$HOME/honcho}"
HONCHO_REF="${HONCHO_REF:-v3.0.9}"            # matches honcho-ai 2.1.2 client
DB_HOST_PORT="${HONCHO_DB_HOST_PORT:-5433}"   # remapped: system Postgres owns 5432
REDIS_HOST_PORT="${HONCHO_REDIS_HOST_PORT:-6380}"  # remapped: system redis owns 6379
API_PORT="${HONCHO_API_PORT:-8000}"
LLM_KEY_VAR="${HONCHO_LLM_KEY_VAR:-OPENAI_API_KEY}"  # repo .env var to feed the deriver
VA_ENV="$REPO/src/voice-agent/.env"

log()  { echo "[honcho-setup] $*"; }
fail() { echo "[honcho-setup] ERROR: $*" >&2; exit 1; }

# 1. Prerequisites
command -v docker >/dev/null 2>&1 || fail "docker not installed"
docker compose version >/dev/null 2>&1 || fail "docker compose v2 not available"
if ! "$REPO/src/voice-agent/.venv/bin/python" -c "import honcho" >/dev/null 2>&1; then
  log "WARN: honcho-ai client missing in voice venv — install it:"
  log "      $REPO/src/voice-agent/.venv/bin/pip install honcho-ai"
fi

# 2. Clone (pinned) or keep an existing checkout untouched
if [ -d "$HONCHO_DIR/.git" ]; then
  log "honcho server already at $HONCHO_DIR (left as-is; 'git -C $HONCHO_DIR checkout $HONCHO_REF' to repin)"
else
  log "cloning honcho server ($HONCHO_REF) -> $HONCHO_DIR"
  git clone --depth 1 --branch "$HONCHO_REF" https://github.com/plastic-labs/honcho.git "$HONCHO_DIR" \
    || git clone https://github.com/plastic-labs/honcho.git "$HONCHO_DIR" \
    || fail "git clone failed"
fi
cd "$HONCHO_DIR" || fail "cannot cd $HONCHO_DIR"

# 3. Compose: copy template once, remap the conflicting host ports (idempotent —
#    the internal docker network still uses 5432/6379, only host bindings move)
[ -f docker-compose.yml ] || cp docker-compose.yml.example docker-compose.yml
sed -i "s|127.0.0.1:5432:5432|127.0.0.1:${DB_HOST_PORT}:5432|; \
        s|127.0.0.1:6379:6379|127.0.0.1:${REDIS_HOST_PORT}:6379|" docker-compose.yml

# 4. .env: LLM key (from the repo .env) + auth off + internal DB URI (write once)
if [ ! -f .env ]; then
  KEY=$(grep -m1 "^${LLM_KEY_VAR}=" "$REPO/.env" 2>/dev/null | cut -d= -f2-)
  [ -z "$KEY" ] && fail "no ${LLM_KEY_VAR} in $REPO/.env (honcho's deriver needs an LLM key)"
  umask 077
  cat > .env <<EOF
LOG_LEVEL=INFO
AUTH_USE_AUTH=false
DB_CONNECTION_URI=postgresql+psycopg://postgres:postgres@database:5432/postgres
LLM_OPENAI_API_KEY=${KEY}
EOF
  log ".env written ($HONCHO_DIR/.env, chmod 600)"
else
  log ".env already present — left as-is"
fi

# 5. Bring the stack up. If it is ALREADY healthy, skip the (re)build — that
#    keeps re-runs idempotent and robust against transient registry/network
#    failures (a forced --build re-pulls base images from ghcr.io). Only the
#    first install builds.
already_healthy() {
  [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${API_PORT}/health" 2>/dev/null || echo 000)" = "200" ]
}
if already_healthy; then
  log "honcho already running + healthy on :${API_PORT} — ensuring up (no rebuild)"
  docker compose up -d >/dev/null 2>&1 || true
else
  log "building + starting honcho (first run pulls images + compiles — a few minutes)..."
  docker compose up -d --build || fail "docker compose up failed (check: docker compose logs)"
  # 6. Wait for the API to report healthy
  for _ in $(seq 1 60); do already_healthy && break; sleep 2; done
  already_healthy || fail "honcho API never became healthy on :${API_PORT} (check: docker compose logs api)"
  log "honcho API healthy on :${API_PORT}"
fi

# 7. Wire the voice agent (idempotent) — selects honcho as the active provider
if grep -q '^JARVIS_MEMORY_PROVIDER=honcho' "$VA_ENV" 2>/dev/null; then
  log "voice-agent .env already wired for honcho"
else
  cat >> "$VA_ENV" <<EOF

# Cross-session memory via self-hosted honcho (setup/honcho/setup-honcho.sh)
JARVIS_MEMORY_PROVIDER=honcho
HONCHO_BASE_URL=http://127.0.0.1:${API_PORT}
HONCHO_API_KEY=local
EOF
  log "wired honcho into $VA_ENV"
fi

echo
log "DONE. Activate: systemctl --user restart jarvis-voice-agent.service"
log "Pause honcho later: cd $HONCHO_DIR && docker compose down   (recall goes inert; file memory still works)"

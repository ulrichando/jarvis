#!/usr/bin/env bash
# setup-honcho-vps.sh — provision self-hosted honcho ON THE VPS for cloud
# semantic recall (Phase 2 of cloud voice memory).
#
# Architecture: the web app is the ONLY thing that talks to honcho. The
# LiveKit voice worker (/opt/jarvis/voice-agent-lk) calls the web app's
# POST /api/recall (service proxy JWT, sub="voice-agent"); the web app calls
# honcho's v3 REST over the private `jarvis-honcho` docker network. Honcho is
# NEVER exposed: every host port binds 127.0.0.1, and the cross-stack link is
# an internal:true network created by src/web/docker-compose.yml.
#
# This is the VPS sibling of setup-honcho.sh (the local/desktop installer).
# Differences: no voice-venv wiring (the voice worker talks only to the web
# app), the deriver key comes from src/web/.env.production, and the api
# container is joined to jarvis-honcho via docker-compose.vps.yml.
#
# Idempotent: safe to re-run. Pins the honcho server to v3.0.9 — the tag the
# /api/recall wire shapes were verified against (MessageBatchCreate wrapper,
# GET /context query params, /v3 route prefix). Repin deliberately, then
# re-verify those shapes (see docs/runbook/honcho-cloud-recall.md).
#
# Prereq ORDER: the web stack must be up first — its compose declares +
# creates the jarvis-honcho network this script attaches to.
#
# Env overrides: JARVIS_REPO, JARVIS_WEB_ENV, HONCHO_DIR, HONCHO_REF,
#                HONCHO_DB_HOST_PORT, HONCHO_REDIS_HOST_PORT, HONCHO_API_PORT,
#                HONCHO_LLM_KEY_VAR.
set -uo pipefail

REPO="${JARVIS_REPO:-/opt/jarvis}"
HONCHO_DIR="${HONCHO_DIR:-/opt/honcho}"
HONCHO_REF="${HONCHO_REF:-v3.0.9}"            # verified wire-shape pin (client 2.1.2 era)
DB_HOST_PORT="${HONCHO_DB_HOST_PORT:-5433}"   # loopback-only; remapped clear of a system PG
REDIS_HOST_PORT="${HONCHO_REDIS_HOST_PORT:-6380}"  # loopback-only; remapped clear of a system redis
API_PORT="${HONCHO_API_PORT:-8000}"
LLM_KEY_VAR="${HONCHO_LLM_KEY_VAR:-OPENAI_API_KEY}"  # deriver + dialectic LLM key source var
WEB_ENV="${JARVIS_WEB_ENV:-$REPO/src/web/.env.production}"
OVERRIDE_SRC="$REPO/setup/honcho/docker-compose.vps.yml"
NET_NAME="jarvis-honcho"

log()  { echo "[honcho-vps] $*"; }
fail() { echo "[honcho-vps] ERROR: $*" >&2; exit 1; }

# 1. Prerequisites
command -v docker >/dev/null 2>&1 || fail "docker not installed"
docker compose version >/dev/null 2>&1 || fail "docker compose v2 not available"
[ -f "$OVERRIDE_SRC" ] || fail "override template missing: $OVERRIDE_SRC (run from a full jarvis checkout)"
[ -f "$WEB_ENV" ] || fail "$WEB_ENV missing — deploy the web stack first (docs/runbook/deploy-online.md)"

# 2. The cross-stack network must exist (the web compose creates it — pinned
#    name, internal:true). Creating it here instead would fight compose's
#    ownership labels, so we require the web stack up first.
docker network inspect "$NET_NAME" >/dev/null 2>&1 \
  || fail "docker network '$NET_NAME' not found — bring the web stack up first:
       cd $REPO/src/web && docker compose --env-file .env.production up -d
     (its compose declares + creates $NET_NAME)"

# 3. Clone (pinned) or keep an existing checkout untouched
if [ -d "$HONCHO_DIR/.git" ]; then
  log "honcho server already at $HONCHO_DIR (left as-is; 'git -C $HONCHO_DIR checkout $HONCHO_REF' to repin)"
else
  log "cloning honcho server ($HONCHO_REF) -> $HONCHO_DIR"
  git clone --depth 1 --branch "$HONCHO_REF" https://github.com/plastic-labs/honcho.git "$HONCHO_DIR" \
    || { git clone https://github.com/plastic-labs/honcho.git "$HONCHO_DIR" \
         && git -C "$HONCHO_DIR" checkout "$HONCHO_REF"; } \
    || fail "git clone failed"
fi
cd "$HONCHO_DIR" || fail "cannot cd $HONCHO_DIR"

# 4. Compose: copy template once, remap the loopback host ports (idempotent —
#    the internal docker network still uses 5432/6379, only host bindings move)
[ -f docker-compose.yml ] || cp docker-compose.yml.example docker-compose.yml
sed -i "s|127.0.0.1:5432:5432|127.0.0.1:${DB_HOST_PORT}:5432|; \
        s|127.0.0.1:6379:6379|127.0.0.1:${REDIS_HOST_PORT}:6379|; \
        s|127.0.0.1:8000:8000|127.0.0.1:${API_PORT}:8000|" docker-compose.yml

# 5. Override: join api to jarvis-honcho (alias `honcho`). Repo-owned —
#    refreshed on every run so it tracks the repo copy.
cp -f "$OVERRIDE_SRC" docker-compose.override.yml
log "override installed (repo-owned; re-copied on every run): docker-compose.override.yml"

# 6. .env: deriver/dialectic LLM key (from the web prod env) + auth off +
#    internal DB URI (write once). AUTH_USE_AUTH=false is safe HERE ONLY
#    because honcho is loopback + private-network only — never expose it.
if [ ! -f .env ]; then
  KEY=$(grep -m1 "^${LLM_KEY_VAR}=" "$WEB_ENV" 2>/dev/null | cut -d= -f2-)
  [ -z "$KEY" ] && fail "no ${LLM_KEY_VAR} in $WEB_ENV (honcho's deriver needs an LLM key)"
  umask 077
  cat > .env <<EOF
LOG_LEVEL=INFO
AUTH_USE_AUTH=false
DB_CONNECTION_URI=postgresql+psycopg://postgres:postgres@database:5432/postgres
LLM_OPENAI_API_KEY=${KEY}
# v3.0.9's deriver won't claim a representation work unit until >=1024 unprocessed
# tokens accumulate, and v3.0.9 has NO age-based flush (added post-v3.0.9, #826) —
# so short voice turns ingest but never derive, and recall stays empty (issue #494).
# Flushing forces the deriver to process partial batches. REQUIRED at this pin.
DERIVER_FLUSH_ENABLED=true
EOF
  log ".env written ($HONCHO_DIR/.env, chmod 600)"
else
  log ".env already present — left as-is"
fi

# 7. Bring the stack up. If it is ALREADY healthy, skip the (re)build — keeps
#    re-runs idempotent and robust against transient registry/network failures
#    (a forced --build re-pulls base images). Only the first install builds.
already_healthy() {
  [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${API_PORT}/health" 2>/dev/null || echo 000)" = "200" ]
}
if already_healthy; then
  log "honcho already running + healthy on :${API_PORT} — ensuring up (no rebuild)"
  docker compose up -d >/dev/null 2>&1 || true
else
  log "building + starting honcho (first run pulls images + compiles — a few minutes)..."
  docker compose up -d --build || fail "docker compose up failed (check: docker compose logs)"
  for _ in $(seq 1 60); do already_healthy && break; sleep 2; done
  already_healthy || fail "honcho API never became healthy on :${API_PORT} (check: docker compose logs api)"
  log "honcho API healthy on :${API_PORT}"
fi

# 8. Wire the web app (idempotent) — /api/recall reads HONCHO_BASE_URL and
#    fails SOFT while it is absent, so this is the flip that arms recall.
if grep -q '^HONCHO_BASE_URL=' "$WEB_ENV" 2>/dev/null; then
  log "web .env.production already wired for honcho"
else
  cat >> "$WEB_ENV" <<'EOF'

# Honcho semantic recall (setup/honcho/setup-honcho-vps.sh) — /api/recall
# backend, reached over the private jarvis-honcho network (alias `honcho`).
HONCHO_BASE_URL=http://honcho:8000
HONCHO_API_KEY=local
EOF
  log "wired honcho into $WEB_ENV"
  log "RECREATE the web container to pick it up:"
  log "    cd $REPO/src/web && docker compose --env-file .env.production up -d web"
fi

echo
log "DONE. Verify end-to-end per docs/runbook/honcho-cloud-recall.md"
log "Pause honcho later: cd $HONCHO_DIR && docker compose down   (recall fails soft; turns + curated memory unaffected)"

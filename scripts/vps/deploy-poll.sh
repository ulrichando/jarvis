#!/usr/bin/env bash
# jarvis-deploy-poll — VPS continuous deploy.
#
# Polls origin/master; when it moves: ff-only pull → rebuild the src/web compose
# stack (only when src/web or src/cli changed) → health gate → automatic rollback
# to the previous SHA on failure. Runs from the repo checkout at /opt/jarvis so
# the script self-updates with master; the systemd units are one-time copies
# (scripts/vps/jarvis-deploy-poll.{service,timer}).
# Runbook: docs/runbook/deploy-online.md ("Continuous deploy").
set -euo pipefail

REPO=/opt/jarvis
WEB="$REPO/src/web"
LOG=/var/log/jarvis-deploy.log
STATE_DIR=/var/lib/jarvis-deploy
COMPOSE=(docker compose --env-file .env.production)

mkdir -p "$STATE_DIR"
exec 9>/var/lock/jarvis-deploy.lock
flock -n 9 || exit 0

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }

notify() {
  log "$*"
  # Optional off-box alert (ntfy-style POST). Set JARVIS_DEPLOY_NOTIFY_URL in
  # /etc/jarvis-deploy.env; silent when unset.
  if [ -n "${JARVIS_DEPLOY_NOTIFY_URL:-}" ]; then
    curl -sf -m 10 -d "$*" "$JARVIS_DEPLOY_NOTIFY_URL" >/dev/null 2>&1 || true
  fi
}

healthy() {
  # hub answers on the host loopback (cloudflared's target for proxy.0wlan.com)…
  curl -sf -m 10 http://127.0.0.1:4000/health >/dev/null || return 1
  # …the Next app serves HTTP (any status <500 proves the process is up —
  # /api/health may 401 behind the bearer gate, which still proves liveness)…
  (cd "$WEB" && "${COMPOSE[@]}" exec -T web node -e \
    'fetch("http://127.0.0.1:3000/api/health").then(r=>process.exit(r.status<500?0:1)).catch(()=>process.exit(1))') \
    || return 1
  # …and nothing in the stack is exited/restarting.
  if (cd "$WEB" && "${COMPOSE[@]}" ps --format '{{.Name}} {{.State}}' | grep -v ' running$' | grep -q .); then
    return 1
  fi
  return 0
}

cd "$REPO"
if ! git fetch -q origin master; then
  log "fetch failed (network?) — will retry next tick"
  exit 0
fi
OLD=$(git rev-parse HEAD)
NEW=$(git rev-parse origin/master)
if [ "$OLD" = "$NEW" ]; then exit 0; fi

# ponytail: single retry-latch, not a backoff ladder — a failed SHA is skipped
# until a new push moves master (prevents a 5-min build-storm on a broken master).
if [ -f "$STATE_DIR/failed-sha" ] && [ "$(cat "$STATE_DIR/failed-sha")" = "$NEW" ]; then
  exit 0
fi

# Schema changes are deliberately MANUAL (drizzle is push-managed; db:migrate can
# hang — see docs/runbook/deploy-online.md). Skip + alert; a human applies the
# schema, then clears the latch.
if git -C "$REPO" diff --name-only "$OLD..$NEW" -- src/web/drizzle/ | grep -q .; then
  echo "$NEW" >"$STATE_DIR/failed-sha"
  notify "jarvis-deploy: SKIPPED $NEW — migration files changed; apply schema manually (deploy-online.md), then rm $STATE_DIR/failed-sha"
  exit 1
fi

log "deploying $OLD -> $NEW"
if ! git merge --ff-only "$NEW" >>"$LOG" 2>&1; then
  echo "$NEW" >"$STATE_DIR/failed-sha"
  notify "jarvis-deploy: FAILED — ff-only merge refused (dirty/diverged tree at $REPO); reconcile manually. Box-local tweaks belong in docker-compose.override.yml / .env.production, never tracked files."
  exit 1
fi

rollback() {
  # compose build overwrote the image tags, so a true rollback rebuilds the old
  # code (fast: layers are still cached — prune only runs on success).
  git -C "$REPO" reset --hard "$OLD" >>"$LOG" 2>&1 || true
  (cd "$WEB" && "${COMPOSE[@]}" build >>"$LOG" 2>&1 && "${COMPOSE[@]}" up -d >>"$LOG" 2>&1) || true
  echo "$NEW" >"$STATE_DIR/failed-sha"
  notify "jarvis-deploy: FAILED $NEW — rolled back to $OLD (see $LOG)"
  exit 1
}

if git -C "$REPO" diff --name-only "$OLD..$NEW" -- src/web src/cli | grep -q .; then
  cd "$WEB"
  "${COMPOSE[@]}" build >>"$LOG" 2>&1 || rollback
  "${COMPOSE[@]}" up -d >>"$LOG" 2>&1 || rollback
  if git -C "$REPO" diff --name-only "$OLD..$NEW" -- src/web/Caddyfile | grep -q .; then
    # Caddyfile is bind-mounted :ro — content changes need an explicit restart.
    "${COMPOSE[@]}" restart caddy >>"$LOG" 2>&1 || rollback
  fi
  ok=0
  for _ in 1 2 3; do
    sleep 15
    if healthy; then ok=1; break; fi
  done
  [ "$ok" = 1 ] || rollback
  docker image prune -f >/dev/null 2>&1 || true
fi

rm -f "$STATE_DIR/failed-sha"
notify "jarvis-deploy: OK $NEW ($(git -C "$REPO" log -1 --format=%s))"

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

# Self-update hazard: the ff-only merge below rewrites THIS script mid-run, and
# bash reads a script by byte offset as it executes — a size change past the
# merge point makes it resume at a wrong offset and run garbage. Re-exec once
# from an immutable snapshot so the running bytes never change under us. Only the
# deploy that actually ships a deploy-poll.sh change is exposed; every other
# deploy leaves this file untouched. The next run purges the previous snapshot
# (unlinking a still-open snapshot is safe on Linux, so a stuck run is unharmed).
if [ -z "${JARVIS_DEPLOY_REEXEC:-}" ]; then
  rm -f /tmp/.jarvis-deploy-poll.*.sh 2>/dev/null || true
  _snap=$(mktemp /tmp/.jarvis-deploy-poll.XXXXXX.sh)
  cp -- "$0" "$_snap"
  JARVIS_DEPLOY_REEXEC=1 exec bash "$_snap"
fi

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

# Set by healthy() to the specific failing check so a rollback records WHY the
# gate failed, instead of the old blind `>/dev/null` (which left the 2026-07-09
# spurious rollback of #166 un-diagnosable). Read by gate() before rollback.
HEALTH_REASON=""
healthy() {
  HEALTH_REASON=""
  # hub answers on the host loopback (cloudflared's target for proxy.0wlan.com)…
  if ! curl -sf -m 10 http://127.0.0.1:4000/health >/dev/null; then
    HEALTH_REASON="hub :4000/health unreachable"
    return 1
  fi
  # …the Next app serves HTTP (any status <500 proves the process is up —
  # /api/health may 401 behind the bearer gate, which still proves liveness)…
  local _st _rc
  _st=$( (cd "$WEB" && "${COMPOSE[@]}" exec -T web node -e \
    'fetch("http://127.0.0.1:3000/api/health").then(r=>{console.log(r.status);process.exit(r.status<500?0:1)}).catch(e=>{console.log("fetch-error:"+e.message);process.exit(1)})') 2>&1 ) && _rc=0 || _rc=$?
  if [ "$_rc" -ne 0 ]; then
    HEALTH_REASON="web /api/health not <500 (exec rc=$_rc, out=${_st//$'\n'/ })"
    return 1
  fi
  # …and nothing in the stack is exited/restarting.
  local _bad
  _bad=$( (cd "$WEB" && "${COMPOSE[@]}" ps --format '{{.Name}} {{.State}}' | grep -v ' running$') || true )
  if [ -n "$_bad" ]; then
    HEALTH_REASON="container(s) not running: ${_bad//$'\n'/, }"
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

# Poll healthy() until it passes or the budget runs out, then roll back with the
# captured reason. Both build paths (full stack / computer-use-only) share this
# so the retry budget and the failure diagnostics stay identical. 6×15s ≈ 90s of
# settle: a genuinely broken deploy still fails every attempt and rolls back; the
# wider budget only buys a slow/racy multi-container recreate more time to become
# ready (the old 3×15s budget spuriously rolled back a healthy #166 on 2026-07-09
# — web was ✓Ready in 192ms yet the gate still failed, un-logged).
gate() {
  for _ in 1 2 3 4 5 6; do
    sleep 15
    if healthy; then return 0; fi
  done
  log "health gate failed after 6 attempts — ${HEALTH_REASON:-unknown}"
  rollback
}

if git -C "$REPO" diff --name-only "$OLD..$NEW" -- src/web src/cli src/gh-app | grep -q .; then
  cd "$WEB"
  "${COMPOSE[@]}" build >>"$LOG" 2>&1 || rollback
  "${COMPOSE[@]}" up -d >>"$LOG" 2>&1 || rollback
  if git -C "$REPO" diff --name-only "$OLD..$NEW" -- src/web/Caddyfile | grep -q .; then
    # Caddyfile is bind-mounted :ro — content changes need an explicit restart.
    "${COMPOSE[@]}" restart caddy >>"$LOG" 2>&1 || rollback
  fi
  if git -C "$REPO" diff --name-only "$OLD..$NEW" -- src/web/searxng | grep -q .; then
    # searxng settings.yml is bind-mounted — content changes need a restart
    # (up -d won't recreate on a mounted-file change alone).
    "${COMPOSE[@]}" restart searxng >>"$LOG" 2>&1 || rollback
  fi
  gate
  docker image prune -f >/dev/null 2>&1 || true
elif git -C "$REPO" diff --name-only "$OLD..$NEW" -- src/voice-agent | grep -q .; then
  # computer-use's build context is ../voice-agent (blind spot found 2026-07-06:
  # PR #116 changed only voice-agent → deploy said OK without rebuilding the
  # sidecar). Rebuild JUST that service — a voice-agent-only change must not
  # pay the full web/hub build. When src/web|cli|gh-app also changed, the full
  # branch above already rebuilds every service, computer-use included.
  cd "$WEB"
  "${COMPOSE[@]}" build computer-use >>"$LOG" 2>&1 || rollback
  "${COMPOSE[@]}" up -d computer-use >>"$LOG" 2>&1 || rollback
  gate
  docker image prune -f >/dev/null 2>&1 || true
fi

rm -f "$STATE_DIR/failed-sha"
notify "jarvis-deploy: OK $NEW ($(git -C "$REPO" log -1 --format=%s))"

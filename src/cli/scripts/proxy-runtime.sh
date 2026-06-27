#!/usr/bin/env bash
# Shared proxy startup for source and compiled Jarvis CLI launchers.
# Must be sourced by a bash launcher after start-env.sh.

JARVIS_PROXY_STARTED_SESSION=0
JARVIS_PROXY_ROOT=""
JARVIS_PROXY_BUN=""
PROXY_SUP_PID=""

jarvis_proxy_start() {
  local root="${1:?missing cli root}"
  local bun="${2:?missing bun launcher}"

  JARVIS_PROXY_ROOT="$root"
  JARVIS_PROXY_BUN="$bun"

  # Prefer the persistent systemd --user service when available. In restricted
  # environments the user bus may be inaccessible; that should degrade to the
  # same session-scoped proxy fallback, not prevent the CLI from opening.
  if command -v systemctl >/dev/null 2>&1 \
     && systemctl --user is-active --quiet jarvis-proxy.service 2>/dev/null; then
    echo "[jarvis] proxy: using persistent jarvis-proxy.service on :4000"
    JARVIS_PROXY_STARTED_SESSION=0
    return 0
  fi

  echo "[jarvis] proxy: service unavailable; starting session proxy on :4000"

  # Pre-flight: kill any orphaned session proxy from a prior launcher that did
  # not clean up. Match by command line so unrelated Bun processes survive.
  local stale_proxy
  stale_proxy="$(pgrep -f "$root/src/proxy/server.ts" 2>/dev/null | head -1 || true)"
  if [ -n "$stale_proxy" ] && kill -0 "$stale_proxy" 2>/dev/null; then
    kill -TERM "$stale_proxy" 2>/dev/null || true
    for _ in 1 2 3 4 5 6; do
      kill -0 "$stale_proxy" 2>/dev/null || break
      sleep 0.25
    done
    kill -KILL "$stale_proxy" 2>/dev/null || true
  fi

  : > /tmp/jarvis-proxy.log
  (
    set +e
    PROXY_RESTARTS=0
    PROXY_WINDOW_START=$(date +%s)
    while true; do
      "$bun" "$root/src/proxy/server.ts" >>/tmp/jarvis-proxy.log 2>&1 &
      PROXY_CHILD=$!
      trap "kill $PROXY_CHILD 2>/dev/null; exit 0" TERM INT
      wait $PROXY_CHILD
      EC=$?
      NOW=$(date +%s)
      if (( NOW - PROXY_WINDOW_START > 30 )); then
        PROXY_WINDOW_START=$NOW
        PROXY_RESTARTS=0
      fi
      PROXY_RESTARTS=$((PROXY_RESTARTS + 1))
      if (( PROXY_RESTARTS > 5 )); then
        echo "[jarvis-proxy-sup] proxy crashed $PROXY_RESTARTS times in <30s; giving up (check env / port conflict)" \
          >>/tmp/jarvis-proxy.log
        break
      fi
      echo "[jarvis-proxy-sup] proxy exited (code=$EC); respawn #$PROXY_RESTARTS in 2s" \
        >>/tmp/jarvis-proxy.log
      sleep 2
    done
  ) &
  PROXY_SUP_PID=$!
  JARVIS_PROXY_STARTED_SESSION=1
  trap jarvis_proxy_cleanup EXIT
}

jarvis_proxy_wait_health() {
  for _ in $(seq 1 15); do
    if curl -s http://127.0.0.1:4000/health >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  echo "[jarvis] proxy health check failed on 127.0.0.1:4000" >&2
  return 1
}

jarvis_proxy_cleanup() {
  if [ "${JARVIS_PROXY_STARTED_SESSION:-0}" != "1" ]; then
    return 0
  fi

  if [ -n "${PROXY_SUP_PID:-}" ] && kill -0 "$PROXY_SUP_PID" 2>/dev/null; then
    kill -TERM "$PROXY_SUP_PID" 2>/dev/null || true
    for _ in 1 2 3 4 5 6; do
      kill -0 "$PROXY_SUP_PID" 2>/dev/null || break
      sleep 0.25
    done
    kill -KILL "$PROXY_SUP_PID" 2>/dev/null || true
  fi

  if [ -n "${JARVIS_PROXY_ROOT:-}" ]; then
    pkill -f "$JARVIS_PROXY_ROOT/src/proxy/server.ts" 2>/dev/null || true
  fi
  JARVIS_PROXY_STARTED_SESSION=0
}

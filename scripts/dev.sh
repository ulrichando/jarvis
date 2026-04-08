#!/bin/bash
# JARVIS Dev Mode — hot reload for Python backend + optional Vite HMR for frontend
#
# Usage:
#   ./scripts/dev.sh              # backend hot reload only (prod build served)
#   ./scripts/dev.sh --vite       # backend hot reload + Vite dev server (HMR)
#   ./scripts/dev.sh --frontend   # backend hot reload + auto-rebuild on frontend changes
#
# Backend:  any .py change in src/ → os.execv() restart + auto page reload
# Frontend: --vite mode uses Vite HMR on :5173 (fastest)
#           --frontend mode watches src/server/frontend/src/ and rebuilds for prod
#
# Requires: pip install watchdog   (for backend hot reload)

set -e
JARVIS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$JARVIS_ROOT"

MODE_VITE=0
MODE_FRONTEND_WATCH=0
for arg in "$@"; do
  case "$arg" in
    --vite)     MODE_VITE=1 ;;
    --frontend) MODE_FRONTEND_WATCH=1 ;;
  esac
done

# Ensure watchdog is installed
if ! python3 -c "import watchdog" 2>/dev/null; then
  echo "[dev] Installing watchdog..."
  pip install watchdog -q
fi

export JARVIS_NO_SANDBOX=1
export JARVIS_OWNER=ulrich
export JARVIS_HOT_RELOAD=1
export PYTHONUNBUFFERED=1

cleanup() {
  echo ""
  echo "[dev] Stopping..."
  [ -n "$VITE_PID" ]    && kill "$VITE_PID"    2>/dev/null || true
  [ -n "$SERVER_PID" ]  && kill "$SERVER_PID"  2>/dev/null || true
  wait 2>/dev/null
}
trap cleanup EXIT INT TERM

# ── Vite dev server (optional) ──────────────────────────────────────────────
if [ "$MODE_VITE" = "1" ]; then
  FRONTEND_DIR="$JARVIS_ROOT/src/server/frontend"
  if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    echo "[dev] Installing frontend deps..."
    (cd "$FRONTEND_DIR" && npm install)
  fi
  echo "[dev] Starting Vite dev server on :5173 (proxying /api + /ws to :8765)"
  (cd "$FRONTEND_DIR" && npm run dev) &
  VITE_PID=$!
  echo "[dev] Vite PID $VITE_PID — open http://localhost:5173"
fi

# ── JARVIS_HOT_RELOAD controls what the backend watcher does ────────────────
# --frontend: also watch frontend/src for prod builds
if [ "$MODE_FRONTEND_WATCH" = "1" ]; then
  export JARVIS_HOT_RELOAD_FRONTEND=1
fi

# ── Web server ───────────────────────────────────────────────────────────────
echo "[dev] Starting JARVIS web server with hot reload (JARVIS_HOT_RELOAD=1)"
echo "[dev] Any change in src/**/*.py will trigger an automatic restart."
[ "$MODE_VITE" = "1" ]    && echo "[dev] Frontend: Vite HMR on http://localhost:5173"
[ "$MODE_FRONTEND_WATCH" = "1" ] && echo "[dev] Frontend: watching src/server/frontend/src/ (prod rebuild)"
echo ""

python3 -m src.server.web_server &
SERVER_PID=$!
wait "$SERVER_PID"

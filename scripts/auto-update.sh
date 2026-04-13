#!/usr/bin/env bash
# JARVIS Auto-Updater — checks for new commits, pulls and restarts if needed
# Run via systemd timer every 5 minutes

set -euo pipefail

JARVIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_PREFIX="[JARVIS UPDATE]"

cd "$JARVIS_DIR"

# Fetch remote silently
git fetch origin master --quiet 2>/dev/null || {
  echo "$LOG_PREFIX fetch failed (offline?)"
  exit 0
}

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/master)

# Also check if the server has a newer version deployed (even if local is current)
SERVER_COMMIT=$(curl -sf --max-time 5 "https://jarvis.0wlan.com/api/version" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('commit',''))" 2>/dev/null || echo "")
KNOWN_SERVER=$(cat /tmp/.jarvis_server_commit 2>/dev/null || echo "")

SERVER_UPDATED=false
if [ -n "$SERVER_COMMIT" ] && [ "$SERVER_COMMIT" != "$KNOWN_SERVER" ] && [ "$SERVER_COMMIT" != "unknown" ]; then
  echo "$LOG_PREFIX Server updated: $KNOWN_SERVER → $SERVER_COMMIT"
  echo "$SERVER_COMMIT" > /tmp/.jarvis_server_commit
  SERVER_UPDATED=true
fi

if [ "$LOCAL" = "$REMOTE" ]; then
  exit 0  # Nothing changed locally — don't restart for remote-only cloud deploys
fi

echo "$LOG_PREFIX New version detected: $LOCAL → $REMOTE"
git pull origin master --quiet

# Reinstall if dependencies changed (pyproject.toml or requirements)
if git diff "$LOCAL" HEAD -- pyproject.toml setup.py requirements*.txt &>/dev/null | grep -q .; then
  echo "$LOG_PREFIX Dependencies changed — reinstalling..."
  pip install -e . --quiet
fi

# Rebuild frontend if source changed
if git diff "$LOCAL" HEAD -- src/server/frontend/src/ &>/dev/null | grep -q .; then
  echo "$LOG_PREFIX Frontend changed — rebuilding..."
  cd "$JARVIS_DIR/src/server/frontend"
  npm ci --prefer-offline --quiet
  npm run build --quiet
  cd "$JARVIS_DIR"
fi

# Notify desktop (send a desktop notification)
notify-send "JARVIS Updated" "New version pulled: $(git rev-parse --short HEAD)" \
  --icon=dialog-information --expire-time=5000 2>/dev/null || true

# Restart web server via systemd (Restart=always means it comes back automatically)
if systemctl --user is-active --quiet jarvis 2>/dev/null; then
  echo "$LOG_PREFIX Restarting web server via systemd..."
  systemctl --user restart jarvis
  echo "$LOG_PREFIX Web server restarted"
elif pgrep -f "src.server.web_server" &>/dev/null; then
  echo "$LOG_PREFIX Restarting web server (not managed by systemd)..."
  pkill -f "src.server.web_server" 2>/dev/null || true
  sleep 2
  nohup python3 -m src.server.web_server > /tmp/jarvis-web.log 2>&1 &
  echo "$LOG_PREFIX Web server restarted (pid $!)"
fi

# Restart Tauri desktop if running
TAURI_BIN="$(dirname "$0")/../src/desktop-tauri/src-tauri/target/release/jarvis-desktop"
if pgrep -f "jarvis-desktop" &>/dev/null; then
  echo "$LOG_PREFIX Restarting desktop overlay (Tauri)..."
  pkill -f "jarvis-desktop" 2>/dev/null || true
  sleep 1
  if [ -f "$TAURI_BIN" ]; then
    nohup "$TAURI_BIN" > /tmp/jarvis-desktop.log 2>&1 &
    echo "$LOG_PREFIX Desktop restarted"
  else
    echo "$LOG_PREFIX Tauri binary not found — skipping desktop restart"
  fi
elif [ "$SERVER_UPDATED" = "true" ]; then
  echo "$LOG_PREFIX Server updated but desktop not running — skipping restart"
fi

echo "$LOG_PREFIX Update complete → $(git rev-parse --short HEAD)"

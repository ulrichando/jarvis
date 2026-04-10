#!/usr/bin/env bash
# Install JARVIS as a systemd user service (auto-starts on login, restarts on crash/update)
# Usage: ./scripts/install-service.sh

set -euo pipefail

JARVIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_DIR="$HOME/.config/systemd/user"

mkdir -p "$SYSTEMD_DIR"

# Install service and timer files
cp "$JARVIS_DIR/scripts/systemd/jarvis.service"              "$SYSTEMD_DIR/"
cp "$JARVIS_DIR/scripts/systemd/jarvis-autoupdate.service"   "$SYSTEMD_DIR/"
cp "$JARVIS_DIR/scripts/systemd/jarvis-autoupdate.timer"     "$SYSTEMD_DIR/"

systemctl --user daemon-reload

# Enable and start JARVIS web server
systemctl --user enable --now jarvis

# Enable auto-update timer (checks for new commits every 5 min)
systemctl --user enable --now jarvis-autoupdate.timer

echo ""
echo "JARVIS service installed."
echo "  Status:  systemctl --user status jarvis"
echo "  Logs:    journalctl --user -u jarvis -f"
echo "  Updates: systemctl --user list-timers jarvis-autoupdate.timer"

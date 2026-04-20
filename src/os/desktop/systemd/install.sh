#!/usr/bin/env bash
# Install misty-core as a systemd --user service. Must be run as the target user
# (not root — systemd user units live in ~/.config/systemd/user/).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
UNIT_SRC="$SCRIPT_DIR/misty-core.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_DST="$UNIT_DIR/misty-core.service"

[[ $EUID -ne 0 ]] || { echo "[misty] run as a regular user, not root" >&2; exit 1; }
[[ -r "$UNIT_SRC" ]] || { echo "[misty] missing $UNIT_SRC" >&2; exit 1; }

echo "[misty] installing user systemd unit to $UNIT_DST"
mkdir -p "$UNIT_DIR"
cp "$UNIT_SRC" "$UNIT_DST"

echo "[misty] reloading systemd user daemon"
systemctl --user daemon-reload

echo "[misty] enabling + starting misty-core"
systemctl --user enable --now misty-core.service

# Enable linger so the user service starts on boot even before login.
if command -v loginctl >/dev/null 2>&1; then
  if ! loginctl show-user "$(id -un)" 2>/dev/null | grep -q 'Linger=yes'; then
    echo "[misty] enabling user linger (sudo required once)"
    sudo loginctl enable-linger "$(id -un)" || echo "[misty] warn: enable-linger failed; service will only run after login"
  fi
fi

echo
echo "[misty] status:"
systemctl --user status misty-core.service --no-pager 2>&1 | head -15 || true

echo
echo "[misty] done. Verify with:"
echo "  systemctl --user status misty-core"
echo "  journalctl --user -u misty-core -n 20 -f"
echo "  curl http://127.0.0.1:8765/health"

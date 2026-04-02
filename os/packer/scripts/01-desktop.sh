#!/bin/bash
# JARVIS OS — Desktop environment (Sway + Waybar + Chromium)
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "[JARVIS OS] Installing desktop environment..."

apt-get install -y \
    sway swaybg swaylock swayidle waybar \
    foot \
    xwayland wl-clipboard \
    chromium \
    fonts-noto fonts-noto-color-emoji fonts-firacode \
    grim slurp \
    mako-notifier \
    libgtk-3-0 libnotify-bin

# Create XDG runtime directory for jarvis user
mkdir -p /run/user/1000
chown jarvis:jarvis /run/user/1000

echo "[JARVIS OS] Desktop environment installed."

#!/bin/bash
# JARVIS OS — Cleanup and shrink image
set -euo pipefail

echo "[JARVIS OS] Cleaning up..."

# Remove build dependencies (keep runtime)
apt-get autoremove -y
apt-get clean

# Clear caches
rm -rf /var/cache/apt/archives/*
rm -rf /var/lib/apt/lists/*
rm -rf /tmp/jarvis-src /tmp/cogscript-src
rm -rf /tmp/jarvis-systemd /tmp/jarvis-sway /tmp/jarvis-waybar
rm -rf /tmp/jarvis-plymouth /tmp/jarvis-grub /tmp/jarvis-assets
rm -rf /root/.cache /home/jarvis/.cache/pip

# Clear pip cache in venv
rm -rf /opt/jarvis/.venv/pip-cache 2>/dev/null || true

# Zero free space for better compression
dd if=/dev/zero of=/EMPTY bs=1M 2>/dev/null || true
rm -f /EMPTY

# Clear logs
journalctl --vacuum-time=1d 2>/dev/null || true
find /var/log -type f -name "*.log" -exec truncate -s 0 {} \;

echo "[JARVIS OS] Cleanup complete. Image ready."

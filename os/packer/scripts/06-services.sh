#!/bin/bash
# JARVIS OS — Install and enable systemd services
set -euo pipefail

echo "[JARVIS OS] Installing systemd services..."

# Copy all service unit files
if [ -d /tmp/jarvis-systemd ]; then
    cp /tmp/jarvis-systemd/*.service /etc/systemd/system/
    cp /tmp/jarvis-systemd/*.target /etc/systemd/system/
    echo "[JARVIS OS] Service units installed."
else
    echo "[JARVIS OS] WARNING: No systemd units at /tmp/jarvis-systemd"
    exit 1
fi

# Reload systemd
systemctl daemon-reload

# Enable the JARVIS target (pulls in all services)
systemctl enable jarvis.target

# Enable individual services
systemctl enable jarvis-memory.service
systemctl enable jarvis-brain.service
systemctl enable jarvis-web.service
systemctl enable jarvis-speech.service
systemctl enable jarvis-vision.service
systemctl enable jarvis-evolution.service
systemctl enable jarvis-plugins.service
systemctl enable jarvis-greeting.service

# Enable jarvis-grpc only if the binary exists
if [ -f /opt/jarvis/bin/jarvis-core ]; then
    systemctl enable jarvis-grpc.service
    echo "[JARVIS OS] gRPC service enabled."
fi

# Set default target to graphical
systemctl set-default graphical.target

echo "[JARVIS OS] Services installed and enabled."

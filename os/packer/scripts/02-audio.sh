#!/bin/bash
# JARVIS OS — Audio subsystem (PipeWire)
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "[JARVIS OS] Installing audio subsystem..."

apt-get install -y \
    pipewire pipewire-audio pipewire-pulse wireplumber \
    libspa-0.2-bluetooth \
    pavucontrol

# Ensure jarvis user is in the audio group
usermod -aG audio jarvis

# Enable PipeWire user services for jarvis
mkdir -p /home/jarvis/.config/systemd/user
ln -sf /usr/lib/systemd/user/pipewire.service /home/jarvis/.config/systemd/user/default.target.wants/pipewire.service 2>/dev/null || true
ln -sf /usr/lib/systemd/user/pipewire-pulse.service /home/jarvis/.config/systemd/user/default.target.wants/pipewire-pulse.service 2>/dev/null || true
ln -sf /usr/lib/systemd/user/wireplumber.service /home/jarvis/.config/systemd/user/default.target.wants/wireplumber.service 2>/dev/null || true

chown -R jarvis:jarvis /home/jarvis/.config

echo "[JARVIS OS] Audio subsystem installed."

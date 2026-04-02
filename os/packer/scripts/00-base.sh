#!/bin/bash
# JARVIS OS — Base system packages
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "[JARVIS OS] Installing base packages..."

apt-get update
apt-get upgrade -y

apt-get install -y \
    curl wget git build-essential pkg-config \
    sudo systemd-sysv dbus dbus-user-session \
    ffmpeg espeak-ng \
    sqlite3 libsqlite3-dev \
    protobuf-compiler libprotobuf-dev \
    ca-certificates gnupg lsb-release \
    unzip htop neofetch \
    libffi-dev libssl-dev \
    libasound2-dev portaudio19-dev \
    v4l-utils libv4l-dev \
    jq socat

echo "[JARVIS OS] Base packages installed."

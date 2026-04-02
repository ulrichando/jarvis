#!/bin/bash
# JARVIS OS — Python environment and dependencies
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "[JARVIS OS] Installing Python environment..."

apt-get install -y python3 python3-venv python3-dev python3-pip

# Create application directory
mkdir -p /opt/jarvis

# Create virtual environment
python3 -m venv /opt/jarvis/.venv

# Install all JARVIS Python dependencies
/opt/jarvis/.venv/bin/pip install --upgrade pip setuptools wheel

/opt/jarvis/.venv/bin/pip install \
    aiohttp \
    beautifulsoup4 \
    duckduckgo-search \
    edge-tts \
    faster-whisper \
    grpcio \
    grpcio-tools \
    msgpack \
    numpy \
    opencv-python-headless \
    piper-tts \
    requests \
    rich \
    sounddevice

# Pre-download the Whisper model so first boot is fast
echo "[JARVIS OS] Pre-downloading Whisper STT model..."
/opt/jarvis/.venv/bin/python3 -c "
from faster_whisper import WhisperModel
model = WhisperModel('small.en', device='cpu', compute_type='int8')
print('Whisper model cached.')
" || echo "[JARVIS OS] Whisper model pre-download failed (will download on first use)"

chown -R jarvis:jarvis /opt/jarvis

echo "[JARVIS OS] Python environment ready."

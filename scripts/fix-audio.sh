#!/bin/bash
# JARVIS Audio Fix — run on boot or when audio breaks
# Restarts PipeWire and sets correct default devices
# Uses PipeWire WebRTC echo cancellation when available

echo "[JARVIS] Fixing audio..."

# Restart PipeWire
systemctl --user restart pipewire pipewire-pulse wireplumber 2>/dev/null
sleep 3

# Set defaults — find speaker and mic dynamically via wpctl
# Prefer echo-cancelled source if available (configured by install.sh)
AEC_SOURCE=$(wpctl status 2>/dev/null | grep "Echo Cancellation Source" | grep -oP '^\s+\K\d+')
if [ -n "$AEC_SOURCE" ]; then
    wpctl set-default "$AEC_SOURCE"
    echo "[JARVIS] Echo-cancelled mic set: device $AEC_SOURCE"
else
    # Fall back to first available mic
    MIC_ID=$(wpctl status 2>/dev/null | grep -i "microphone\|capture\|input" | head -1 | grep -oP '^\s+\K\d+')
    if [ -n "$MIC_ID" ]; then
        wpctl set-default "$MIC_ID"
        echo "[JARVIS] Mic set: device $MIC_ID"
    else
        echo "[JARVIS] No microphone found"
    fi
fi

# Find speaker output
SPEAKER_ID=$(wpctl status 2>/dev/null | grep -i "speaker\|headphone\|analog output" | head -1 | grep -oP '^\s+\K\d+')
if [ -n "$SPEAKER_ID" ]; then
    wpctl set-default "$SPEAKER_ID"
    echo "[JARVIS] Speaker set: device $SPEAKER_ID"
fi

# Unmute and set levels
amixer set Master unmute 2>/dev/null
amixer set Master 70% 2>/dev/null
amixer set Capture unmute 2>/dev/null
amixer set Capture 100% 2>/dev/null

echo "[JARVIS] Audio fixed."

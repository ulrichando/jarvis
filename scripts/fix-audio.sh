#!/bin/bash
# JARVIS Audio Fix — run on boot or when audio breaks
# Restarts PipeWire and sets correct default devices

echo "[JARVIS] Fixing audio..."

# Restart PipeWire
systemctl --user restart pipewire pipewire-pulse wireplumber 2>/dev/null
sleep 3

# Set defaults — Speaker output, SoundWire mic input
# Find the right device IDs dynamically
SPEAKER_ID=$(wpctl status 2>/dev/null | grep "Comet Lake.*Speaker" | grep -oP '^\s+\K\d+')
MIC_ID=$(wpctl status 2>/dev/null | grep "SoundWire microphones" | grep -oP '^\s+\K\d+')

if [ -n "$SPEAKER_ID" ]; then
    wpctl set-default "$SPEAKER_ID"
    echo "[JARVIS] Speaker set: device $SPEAKER_ID"
fi

if [ -n "$MIC_ID" ]; then
    wpctl set-default "$MIC_ID"
    echo "[JARVIS] Mic set: device $MIC_ID"
fi

# Unmute and set levels
amixer set Master unmute 2>/dev/null
amixer set Master 70% 2>/dev/null
amixer set Capture unmute 2>/dev/null
amixer set Capture 100% 2>/dev/null

echo "[JARVIS] Audio fixed."

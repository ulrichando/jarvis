#!/bin/bash
# JARVIS OS — Waybar status module
# Shows compact status of all JARVIS subsystems

declare -A SERVICES=(
    [MEM]="jarvis-memory"
    [BRN]="jarvis-brain"
    [WEB]="jarvis-web"
    [SPK]="jarvis-speech"
    [VIS]="jarvis-vision"
    [EVO]="jarvis-evolution"
)

status=""
all_ok=true

for label in MEM BRN WEB SPK VIS EVO; do
    svc="${SERVICES[$label]}"
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        status+=" $label"
    else
        status+=" $label"
        all_ok=false
    fi
done

if $all_ok; then
    echo "ALL SYSTEMS NOMINAL${status}"
else
    echo "DEGRADED${status}"
fi

#!/bin/sh
# JARVIS — recover voice services after laptop wake.
#
# Install:  sudo install -m 0755 scripts/jarvis-on-resume.sh \
#               /usr/lib/systemd/system-sleep/jarvis-on-resume
#
# systemd-sleep invokes us with $1 = pre|post and $2 = suspend|suspend-then-hibernate|...
#
# On resume (post), the running voice-agent/voice-client processes have stale
# state — the loopback LiveKit room survives suspend so `connected`/`agent_present`
# stay true (so the ReconnectLadder + WatchdogSec never fire), but the PortAudio
# devices and the worker↔job IPC are stale, so JARVIS comes back "connected but
# deaf/mute". Hand off to jarvis-voice-recover, which waits for the SFU + audio
# stack to settle, restarts the two stateful services in order, and verifies
# recovery via /status — a blind `sleep 3` + restart raced PipeWire
# re-enumeration and rebound the wrong device.
#
# Spec: docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md

set -e

RECOVER="/home/ulrich/Documents/Projects/jarvis/bin/jarvis-voice-recover"

case "$1" in
    post)
        if [ -x "$RECOVER" ]; then
            "$RECOVER" resume || true
        fi
        ;;
    pre)
        # No-op on suspend — letting systemd freeze the processes is fine;
        # we recover fresh on resume.
        :
        ;;
esac

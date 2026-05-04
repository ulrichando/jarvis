#!/bin/sh
# JARVIS — restart voice services after laptop wake.
#
# Install:  sudo install -m 0755 scripts/jarvis-on-resume.sh \
#               /usr/lib/systemd/system-sleep/jarvis-on-resume
#
# systemd-sleep invokes us with $1 = pre|post and $2 = suspend|...
#
# On resume (post-suspend), the running voice-agent/voice-client
# processes have stale LiveKit connections (server-side timed out
# while we were frozen) but their asyncio loops aren't wedged (so
# the watchdog doesn't fire) and no `disconnected` event arrives
# (so the ReconnectLadder doesn't fire either). The agent just sits
# there with a stale room handle.
#
# Cleanest fix: restart them. Today's resilience layer (track_guard
# + breakers + reconnect ladder + dnsmasq DNS cache) handles the
# rest — they come up cleanly within seconds.
#
# Spec: docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md

set -e

USER_NAME="ulrich"
USER_UID="$(id -u "$USER_NAME" 2>/dev/null || echo 1000)"
RUNTIME_DIR="/run/user/$USER_UID"

case "$1" in
    post)
        # Wait briefly for network + audio stack to settle. Without
        # this, voice-client may try to bind PipeWire devices that
        # are mid-re-enumeration and pick the wrong default sink.
        sleep 3
        # Restart only the WebRTC-stateful services. Hub/bridge use
        # Redis + SQLite + WebSockets that survive suspend cleanly.
        for svc in jarvis-voice-agent.service jarvis-voice-client.service; do
            sudo -u "$USER_NAME" \
                XDG_RUNTIME_DIR="$RUNTIME_DIR" \
                DBUS_SESSION_BUS_ADDRESS="unix:path=$RUNTIME_DIR/bus" \
                systemctl --user restart "$svc" || true
            logger -t jarvis-on-resume "restarted $svc"
        done
        ;;
    pre)
        # No-op on suspend — letting systemd freeze the processes
        # is fine; we'll restart fresh on resume.
        :
        ;;
esac

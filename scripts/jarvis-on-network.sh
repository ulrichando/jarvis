#!/bin/sh
# JARVIS — recover the voice stack when the network comes back.
#
# Install: sudo install -m 0755 scripts/jarvis-on-network.sh \
#              /etc/NetworkManager/dispatcher.d/50-jarvis-voice-recover
#
# NetworkManager runs dispatcher scripts as ROOT with:
#   $1 = interface   $2 = action  (up | down | connectivity-change | vpn-up | ...)
#
# The JARVIS LiveKit SFU is local (127.0.0.1), so a wifi drop/reconnect never
# drops the loopback room → no LiveKit `disconnected` event → the voice
# client's ReconnectLadder never fires. But the agent's EXTERNAL STT WebSocket
# (Deepgram) dies on the blip and JARVIS goes deaf with nothing to recover it.
# On a real connectivity GAIN, hand off to jarvis-voice-recover (debounced,
# readiness-gated, verified). Must return fast — detach with setsid so NM's
# dispatcher timeout doesn't kill the recovery mid-flight.

RECOVER="/home/ulrich/Documents/Projects/jarvis/bin/jarvis-voice-recover"
[ -x "$RECOVER" ] || exit 0

case "$2" in
    up | connectivity-change)
        # Only act on FULL connectivity — ignore none/portal/limited and the
        # down edge. (jarvis-voice-recover's own debounce collapses the burst
        # of up/connectivity-change events a single reconnect emits.)
        conn="$(nmcli -t -f CONNECTIVITY general 2>/dev/null || echo unknown)"
        [ "$conn" = "full" ] || exit 0
        setsid "$RECOVER" network >/dev/null 2>&1 < /dev/null &
        ;;
esac
exit 0

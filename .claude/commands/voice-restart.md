---
description: Safely restart jarvis-voice-agent.service (checks for in-flight session first)
---

Check the latest turn timestamp in `~/.local/share/jarvis/turn_telemetry.db`. If the most recent turn is within 60 seconds of now, STOP and ask the user whether they want to interrupt an in-flight voice session before restarting. Otherwise:

1. `systemctl --user restart jarvis-voice-agent.service`
2. `sleep 3`
3. `systemctl --user is-active jarvis-voice-agent.service` — confirm `active`
4. `tail -10 /tmp/jarvis-voice-agent.log` — check for startup errors
5. Report: service status + any warnings/errors in the last 10 log lines.

Never use `kill -9` or `pkill` — always go through systemd.

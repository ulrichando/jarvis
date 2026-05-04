# JARVIS voice — operator runbook

**Last updated:** 2026-05-04
**Audience:** you, future-you, anyone debugging a silent JARVIS at 2am.

## Architecture at a glance

```
┌─────────────────┐      ┌──────────────────┐
│  Tauri webview  │◀────▶│ jarvis-bridge    │ :8765 (127.0.0.1)
│  + status pill  │      │  (Bun, REST+WS)  │
└─────────────────┘      └──────────────────┘
        │                         │
        │                         ▼
        │                 ┌──────────────────┐
        │                 │ jarvis-proxy     │ :4000 (127.0.0.1)
        │                 │  (Bun, LLM mux)  │ → Groq/DeepSeek/…
        │                 └──────────────────┘
        ▼
┌─────────────────┐  WebRTC  ┌────────────────────┐  WebSocket  ┌──────────────────┐
│ jarvis-voice-   │◀────────▶│  livekit-server    │◀───────────▶│ jarvis-voice-    │
│ client (PipeWire│   :7880  │  (SFU, 127.0.0.1)  │             │ agent (LiveKit   │
│  mic+speaker)   │          └────────────────────┘             │  worker, Python) │
│  status :8767   │                                              └──────────────────┘
└─────────────────┘                                                       │
                                                                          ▼
                                                                  STT (Groq Whisper)
                                                                  LLM (router → Groq/DeepSeek)
                                                                  TTS (Groq Orpheus → edge_tts)
```

## Quick health check

```bash
# 1. Voice client + agent presence
curl -sS http://127.0.0.1:8767/status | jq '{connected, agent_present, listening, speaking}'

# 2. Bridge alive
curl -sS http://127.0.0.1:8765/health

# 3. All services up?
systemctl --user is-active livekit-server.service jarvis-voice-agent.service jarvis-voice-client.service jarvis-bridge.service jarvis-proxy.service jarvis-hub.service

# 4. Recent telemetry (last hour)
src/voice-agent/.venv/bin/python src/voice-agent/turn_telemetry.py --report --days 1

# 5. Soak rescore for axis bumps
bin/jarvis-soak-rescore.sh 6
```

Healthy reading: bridge `{"status":"ok"}`, voice-client `connected:true, agent_present:true`, all services `active`.

## Common failures

### "JARVIS doesn't respond"

| Symptom | Cause | Fix |
|---|---|---|
| Status shows `connected:false` | LiveKit dropped | `systemctl --user restart livekit-server jarvis-voice-client` |
| `connected:true, agent_present:false` for >30s | Worker process wedged | `systemctl --user restart jarvis-voice-agent` |
| Agent present, no audio out | TTS quota exhausted | Check `/tmp/jarvis-voice-agent.log` for `429`/`401`; FallbackAdapter should have switched to edge_tts. If silent, restart agent. |
| `[stt-gate] dropped` in agent log | STT noise filter ate the turn | Expected — turn was below confidence threshold. Speak louder/clearer. |
| Agent restart loops every 10s | systemd watchdog firing | Check why listener loop is wedged. `journalctl --user -u jarvis-voice-agent.service --since "5 minutes ago" | tail -50` |
| All Groq calls fail simultaneously | DNS blip or Groq outage | Phase 13 circuit breakers should fire OPEN within 8s; agent speaks "one second, sir" via cached WAV (if rendered) or stays silent until breaker recovers. Check `[breaker:STT/TTS/LLM]` log lines. |
| Specialist (desktop/browser/planner) never returns | DeepSeek `reasoning_content` round-trip fail | Check `[deepseek_roundtrip]` log lines. If absent, the patch didn't load — restart agent. |
| Tool name validation error in logs | Groq malformed tool call | `tool_name_sanitizer` should auto-recover. Look for `[sanitizer] recovered` log line. If absent, the turn was lost; ask user to repeat. |

### "Voice sounds choppy / cuts off mid-sentence"

| Symptom | Likely cause |
|---|---|
| First word always missing | TTFW → `_BANTER_FAST_PATH_RE` not matching this turn shape; inspect turn_telemetry route column |
| Mid-sentence cuts | Interrupt overlay too aggressive for this emotion. Check `compute_interrupt_tuning()` per-emotion adjustments. |
| Silent ~5s then "JARVIS is ready" | LiveKit room reconnect; ReconnectLadder firing tier-1. Normal during DNS blip. |

### "JARVIS hallucinates that an app opened"

Should be impossible since Phase 9.4 — every `launch_app()` does pre-flight `shutil.which()` and post-spawn `pgrep`. If you see this, check:

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db \
  "SELECT binary, outcome, COUNT(*) FROM launch_attempts WHERE ts_utc > datetime('now', '-1 day') GROUP BY binary, outcome;"
```

If a binary shows `outcome=OK` but no window opened, the app forked-and-died after `pgrep`'s 600ms window. File a bug; bump the post-spawn delay.

## Restarting the voice stack

```bash
# Surgical: just the agent (preserves room state)
systemctl --user restart jarvis-voice-agent

# Full voice stack restart (drops the LiveKit room briefly)
systemctl --user restart livekit-server.service jarvis-voice-agent.service jarvis-voice-client.service

# Nuclear: bridge too (only if /api endpoints are also broken)
systemctl --user restart jarvis-bridge.service jarvis-proxy.service \
  livekit-server.service jarvis-voice-agent.service jarvis-voice-client.service
```

The Phase 13 ReconnectLadder handles ICE-restart and full-reconnect transparently — you should never need to manually clear LiveKit room state.

## Logs

| Service | Log path |
|---|---|
| jarvis-voice-agent | `/tmp/jarvis-voice-agent.log` (append-mode across restarts) |
| jarvis-voice-client | `journalctl --user -u jarvis-voice-client.service` |
| jarvis-bridge | `/tmp/jarvis-bridge.log` |
| jarvis-proxy | `journalctl --user -u jarvis-proxy.service` |
| jarvis-hub | `journalctl --user -u jarvis-hub.service` |
| livekit-server | `journalctl --user -u livekit-server.service` |
| Telemetry pruner | `/tmp/jarvis-retention.log` |
| Backup script | `journalctl --user -u jarvis-backup.service` |

## Key files

| What | Where |
|---|---|
| Hub state (memories, conversations) | `~/.jarvis/hub/state.db` |
| Per-turn telemetry | `~/.local/share/jarvis/turn_telemetry.db` |
| Hourly snapshots | `~/.jarvis/snapshots/{state,turn_telemetry}-*.db` |
| LiveKit keys | `~/.jarvis/livekit-keys.yaml` (chmod 600) |
| Bridge bearer token | `~/.jarvis/local-api-token.env` (chmod 600) |
| LLM provider keys | `.env` + `src/voice-agent/.env` (gitignored) |
| Learned rules | `~/.jarvis/learned_rules.md` (hot-reloaded) |

## Voice intelligence rubric

`docs/superpowers/specs/2026-04-30-voice-intelligence-rubric.md` is the live tracker. Current: 97/100 vs Claude AI voice mode parity. Don't bump scores without re-running `bin/jarvis-soak-rescore.sh` and updating the rubric in the same PR.

## Related runbooks

- `credential-rotation.md` — provider key rotation checklist
- `git-history-scrub.md` — wipe leaked secrets from git history
- `encryption-at-rest.md` — LUKS / fscrypt / SQLCipher decision

## Escalation

If JARVIS won't recover:

1. `systemctl --user stop jarvis-voice-agent jarvis-voice-client livekit-server jarvis-bridge`
2. `cp ~/.jarvis/snapshots/state-latest.db ~/.jarvis/hub/state.db.recovered` (don't overwrite live state.db until you've inspected)
3. `sqlite3 ~/.jarvis/hub/state.db "PRAGMA integrity_check;"` — if not "ok", restore from snapshot
4. `systemctl --user start livekit-server jarvis-voice-agent jarvis-voice-client jarvis-bridge`
5. If still broken: file the issue at GitHub with `journalctl --user -u jarvis-voice-agent.service --since "10 minutes ago" --no-pager` attached.

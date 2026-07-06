# JARVIS voice — operator runbook

**Last updated:** 2026-06-11 (fact pass: removed hub/proxy units, fixed log
paths, STT/LLM chain, snapshot + escalation targets)
**Audience:** you, future-you, anyone debugging a silent JARVIS at 2am.

## Architecture at a glance

```
┌─────────────────┐      ┌──────────────────┐
│  Tauri webview  │◀────▶│ jarvis-bridge    │ :8765 (127.0.0.1)
│  + status pill  │      │  (Bun, REST+WS)  │ started by start-desktop.sh
└─────────────────┘      └──────────────────┘ (no systemd unit)
        │                         │
        │                         ▼
        │                 ┌──────────────────┐
        │                 │ jarvis-proxy     │ :4000 (127.0.0.1)
        │                 │  (Bun, LLM mux)  │ started by start-desktop.sh
        │                 └──────────────────┘ (no systemd unit)
        ▼
┌─────────────────┐  WebRTC  ┌────────────────────┐  WebSocket  ┌──────────────────┐
│ jarvis-voice-   │◀────────▶│  livekit-server    │◀───────────▶│ jarvis-voice-    │
│ client (PipeWire│   :7880  │  (SFU, 127.0.0.1)  │             │ agent (LiveKit   │
│  mic+speaker)   │          └────────────────────┘             │  worker, Python) │
│  status :8767   │                                              └──────────────────┘
└─────────────────┘                                                       │
                                                                          ▼
                                                          STT (on-device faster-whisper,
                                                               large-v3-turbo on GPU)
                                                          LLM (pinned deepseek-v4-flash
                                                               → kimi-k2.6-instant fallback)
                                                          TTS (local Kokoro → edge_tts)
```

## Quick health check

```bash
# 1. Voice client + agent presence
curl -sS http://127.0.0.1:8767/status | jq '{connected, agent_present, listening, speaking}'

# 2. Bridge alive (only runs while the desktop app is up)
curl -sS http://127.0.0.1:8765/health
pgrep -fa 'bridge/server.ts'   # process-level check

# 3. Systemd services up?
systemctl --user is-active livekit-server.service jarvis-voice-agent.service jarvis-voice-client.service

# 4. Recent telemetry (last hour of turns)
sqlite3 ~/.local/share/jarvis/turn_telemetry.db \
  "SELECT COUNT(*), MAX(ts_utc) FROM turns WHERE ts_utc > datetime('now','-1 hour');"

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
| Agent present, no audio out | TTS quota exhausted | Check `~/.local/share/jarvis/logs/voice-agent.log` for `429`/`401`; FallbackAdapter should have switched to edge_tts. If silent, restart agent. |
| Silent after ~18h of uptime, RSS high | Per-session job memory bloat | The nightly `jarvis-voice-recycle.timer` (~04:00) prevents this; for an immediate fix restart the agent. Watch `rss_mb` in turn_telemetry. |
| `[stt-gate] dropped` in agent log | STT noise filter ate the turn | Expected — turn was below confidence threshold. Speak louder/clearer. |
| Agent restart loops every 10s | systemd watchdog firing | Check why listener loop is wedged. `journalctl --user -u jarvis-voice-agent.service --since "5 minutes ago" \| tail -50` |
| All cloud LLM calls fail simultaneously | DNS blip or provider outage | Circuit breakers fire OPEN within ~8s and the cascade falls through to the next provider; if every provider is down the agent stays silent until a breaker recovers. Check `[breaker:STT/TTS/LLM]` log lines. |
| Supervisor turn dies on the DeepSeek fallback rung | DeepSeek `reasoning_content` round-trip fail | Check `[deepseek_roundtrip]` log lines. If absent, the patch didn't load — restart agent. |
| Tool name validation error in logs | Provider emitted a malformed tool call | `tool_name_sanitizer` should auto-recover. Look for `[sanitizer] recovered` log line. If absent, the turn was lost; ask user to repeat. |

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
# Surgical: just the agent (preserves room state).
# FIRST check for an active session — restarting mid-conversation kills
# in-flight tool calls (CLAUDE.md rule: if the latest turn_telemetry
# ts_utc is <60s old, ask before restarting).
systemctl --user restart jarvis-voice-agent

# Full voice stack restart (drops the LiveKit room briefly)
systemctl --user restart livekit-server.service jarvis-voice-agent.service jarvis-voice-client.service

# Bridge + proxy have no units — they live and die with the desktop app.
# Relaunch the desktop (or src/cli/scripts/start-desktop.sh) to restart
# them; verify with: pgrep -fa 'bridge/server.ts'
```

The ReconnectLadder handles ICE-restart and full-reconnect transparently — you should never need to manually clear LiveKit room state.

## Logs

| Service | Log path |
|---|---|
| jarvis-voice-agent | `~/.local/share/jarvis/logs/voice-agent.log` (JSON lines; rotated daily by `jarvis-log-rotate.timer`, archives kept 14 days) |
| jarvis-voice-client | `journalctl --user -u jarvis-voice-client.service` |
| jarvis-bridge | `/tmp/jarvis-bridge.log` (truncated on each desktop launch) |
| jarvis-proxy | `/tmp/jarvis-proxy.log` (truncated on each desktop launch) |
| livekit-server | `journalctl --user -u livekit-server.service` |
| Nightly recycle | `~/.local/share/jarvis/logs/voice-recycle.log` |
| Telemetry pruner | `journalctl --user -u jarvis-retention-prune.service` |
| Backup script | `journalctl --user -u jarvis-backup-local.service` |

## Key files

| What | Where |
|---|---|
| Per-turn telemetry | `~/.local/share/jarvis/turn_telemetry.db` |
| Conversation transcripts | `~/.jarvis/conversations.db` |
| Curated memory stores | `~/.jarvis/memories/{USER,MEMORY,PROCEDURES}.md` |
| Hourly snapshots (telemetry + conversations + memories) | `~/.jarvis/snapshots/` |
| LiveKit keys | `~/.jarvis/livekit-keys.yaml` (chmod 600) |
| Bridge bearer token | `~/.jarvis/local-api-token.env` (chmod 600) |
| LLM provider keys | `.env` + `src/voice-agent/.env` (gitignored, chmod 600) |

## Voice intelligence rubric

`docs/superpowers/specs/2026-04-30-voice-intelligence-rubric.md` defines the
10 axes; the running score lives in the memory-dir tracker
(`project_voice_intelligence_rubric.md`). Don't bump scores without
re-running `bin/jarvis-soak-rescore.sh` and updating the tracker in the
same change.

## Related runbooks

- `credential-rotation.md` — provider key rotation checklist
- `git-history-scrub.md` — wipe leaked secrets from git history
- `encryption-at-rest.md` — LUKS / fscrypt / SQLCipher decision

## Escalation

If JARVIS won't recover:

1. `systemctl --user stop jarvis-voice-agent jarvis-voice-client livekit-server`
2. `sqlite3 ~/.local/share/jarvis/turn_telemetry.db "PRAGMA integrity_check;"` and the same for `~/.jarvis/conversations.db` — if either is not "ok", restore the newest matching snapshot from `~/.jarvis/snapshots/` (inspect before overwriting the live file).
3. `systemctl --user start livekit-server jarvis-voice-agent jarvis-voice-client`
4. If still broken: file the issue at GitHub with `journalctl --user -u jarvis-voice-agent.service --since "10 minutes ago" --no-pager` and the last 100 lines of `~/.local/share/jarvis/logs/voice-agent.log` attached.

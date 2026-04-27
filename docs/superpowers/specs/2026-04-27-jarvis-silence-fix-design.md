# JARVIS Permanent Silence Fix — Design Spec

**Date:** 2026-04-27  
**Status:** Approved  
**File:** `src/voice-agent/jarvis_agent.py`

---

## Problem Statement

JARVIS periodically goes completely silent during normal daytime use and requires manual restart to recover. Two distinct root causes identified from log analysis:

### Root Cause 1 — AgentSession crash on STT network failure (primary)

When Groq STT has a transient network hiccup, the livekit-agents framework retries 3 times within ~2 seconds. If all retries fail, it emits:

```
AgentSession is closing due to unrecoverable error: Connection error.
```

The agent worker process stays alive. The voice client stays connected. But the AgentSession — the object that processes audio, runs the LLM, and drives TTS — is dead. JARVIS goes completely silent with no automatic recovery. The user has no feedback; the tray may still show green. Recovery requires manual `systemctl restart jarvis-voice-agent` or `jarvis-voice-client`.

**Evidence:** Observed at 2026-04-27T10:47:47 UTC. STT timeout → 3 connection errors in 4s → session killed → silence for rest of session.

### Root Cause 2 — Quiet-hours gate too wide (secondary)

The quiet-hours gate (`QUIET_HOURS_START=23`, `QUIET_HOURS_END=7`) blocks turns without a "Jarvis" vocative between 11pm and 7am. The protected window is wider than necessary:

- **11pm–1am**: User is awake and actively using JARVIS. Gate causes friction — requires "Jarvis" every 5 minutes.
- **6am–7am**: User is awake. Gate still active.
- **Follow-up window (5 min)**: Too short. Natural conversation pauses exceed 5 minutes; follow-up turns get silently dropped, appearing as JARVIS going silent mid-conversation.

---

## Design

### Fix 1 — Session crash auto-recovery

**Mechanism:** Add an `on_close` callback inside `entrypoint()` that fires when `AgentSession` ends. If the close was caused by an unrecoverable error (not a clean shutdown), schedule a restart of `jarvis-voice-client` after a 3-second debounce.

The voice client's existing `_agent_presence_watchdog` already handles recovery: it calls `api.room.delete_room()` then reconnects, which forces LiveKit to dispatch a fresh job to a new `AgentSession`. We only need to trigger that restart.

```python
# In entrypoint(), after session.start():
@session.on("close")
def _on_session_close(ev) -> None:
    error = getattr(ev, "error", None)
    if error is None:
        return  # clean shutdown — launcher or model switch, don't restart
    logger.error(f"[session-watchdog] AgentSession died: {error}. Scheduling voice-client restart.")
    async def _restart_after_debounce():
        await asyncio.sleep(3)
        subprocess.Popen(
            ["systemctl", "--user", "restart", "jarvis-voice-client"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    asyncio.create_task(_restart_after_debounce())
```

**Recovery flow:**
1. Groq STT fails → framework retries → AgentSession closes with error
2. `_on_session_close` fires, detects non-None error, schedules restart
3. After 3s: `systemctl --user restart jarvis-voice-client`
4. Voice client deletes room, reconnects → LiveKit dispatches fresh job
5. New `AgentSession` starts → JARVIS is live again (~5–8 seconds total)

**Safety constraints:**
- Only triggers on non-None error. Clean shutdowns (model switch, tray quit) do not restart.
- 3-second debounce prevents restart storms on cascading errors.
- `subprocess.Popen` (non-blocking) — does not block the event loop.
- If the voice-client restart fails, systemd will log it and the user can recover manually — same as before, but now the auto-path exists.

### Fix 2 — Quiet-hours gate tightening

Three constant changes in `jarvis_agent.py`:

| Constant | Before | After | Rationale |
|---|---|---|---|
| `QUIET_HOURS_START` | `23` (11pm) | `1` (1am) | 11pm–1am is evening, not sleep |
| `QUIET_HOURS_END` | `7` (7am) | `6` (6am) | 6am–7am is morning, not sleep |
| `QUIET_HOURS_WINDOW_SEC` | `300` (5 min) | `1200` (20 min) | Natural conversation pauses |

All three remain env-configurable (`JARVIS_QUIET_START`, `JARVIS_QUIET_END`, `JARVIS_QUIET_WINDOW_SEC` — new) so they can be overridden without code changes.

**Net effect:**
- 1am–6am: gate ON — "Jarvis" vocative required to start; follow-up turns flow for 20 min
- All other hours: gate OFF — JARVIS responds normally based on LLM judgment

---

## Files Changed

| File | Change |
|---|---|
| `src/voice-agent/jarvis_agent.py` | Add `_on_session_close` handler in `entrypoint()`; update 3 gate constants; make `QUIET_HOURS_WINDOW_SEC` env-configurable |
| No other files | Voice client recovery is triggered via systemctl, not code changes |

---

## What This Does Not Change

- Mute/wake phrase detection
- Silent mode behavior
- The LLM's own ambient-noise judgment (JARVIS_INSTRUCTIONS)
- TTS fallback chain
- The agent presence watchdog in the voice client
- Learned-rules system

---

## Success Criteria

1. When Groq STT has a network failure mid-session, JARVIS recovers within 10 seconds without manual intervention.
2. JARVIS responds normally to voice between 11pm–1am without requiring "Jarvis" on every turn.
3. Follow-up turns within 20 minutes of a prior exchange pass through the gate without vocative.
4. The 1am–6am window still blocks ambient noise from triggering tool calls.

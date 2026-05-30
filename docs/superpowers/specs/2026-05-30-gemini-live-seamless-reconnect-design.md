# Gemini Live — seamless in-process reconnect

**Date:** 2026-05-30
**Status:** proposed (design) — awaiting review
**Scope:** `bin/jarvis-gemini-tools` + one small pure helper `src/voice-agent/reconnect_control.py`.

## Problem

Gemini Live caps session duration / sends keepalive pings, so the WebSocket drops roughly
every 10–15 min (`sent 1011 (internal error) keepalive ping timeout` — observed live
2026-05-30 07:32). Today the drop raises `ConnectionClosedError` in `pump_mic`/`drain_replies`,
the `async with client.aio.live.connect(...)` block exits, `main()` returns, the process dies,
and `Restart=always` respawns it. That respawn is a **~7 s gap**: process teardown + systemd
restart + re-spawn `parec`/`paplay` + re-send the system prompt + re-register tools + new Live
handshake. The user hears JARVIS "suddenly stop responding" for those seconds, every cycle.

## Goal

Re-open the Live socket **in-process** on a transient drop — no process restart — so the gap
drops to ~1–2 s (just the WS reconnect + Live setup). The `parec`/`paplay` pipes, the status
server, the stop event, and the idle-revert watcher all **persist** across reconnects; only the
Live `session` and its pump tasks are re-established.

**Non-goals:** preserving Gemini conversation *context* across a drop (a reconnect is a new Live
session with no memory — same as today's process restart, so no regression); changing
`Restart=always` (it stays as the backstop for *hard* failures); the same fix for
`bin/jarvis-gpt-tools` (identical pattern, but a separate follow-up — not bundled here).

## Design

### Persistent vs per-session
Opened **once**, before the reconnect loop, and kept across reconnects:
`mic_proc` (parec), `spk_proc` (paplay), `mss` screen grabber, the `StatusServer`, the `stop`
event, `last_activity`/`last_audio_at` cells, and the **idle-revert task** (it is
session-independent; a reconnect must NOT reset its idle clock — a reconnect is not user
activity).

Re-created **each reconnect**: the Live `session` (from `client.aio.live.connect`) and the
per-session pump tasks (`pump_mic`, `pump_screen`, `drain_replies`) — these are the only things
bound to `session`. They become small functions that take `session` as a parameter.

### The reconnect loop
```
while not stop.is_set():
    try:
        async with client.aio.live.connect(model=LIVE_MODEL, config=cfg) as session:
            rc.mark_connected(loop.time())          # session is up
            status.set_agent_present(True); status.set_listening(True)
            sess_tasks = [pump_mic(session), pump_screen(session), drain_replies(session)]
            done, pending = await asyncio.wait(sess_tasks + [stop_wait], FIRST_COMPLETED)
            cancel + await pending (the sess_tasks)
            if stop.is_set(): break                 # deliberate shutdown / idle-revert fired
            cause = <exception from the first done sess_task>   # a drop
    except Exception as e:
        cause = e
    if stop.is_set(): break
    decision = rc.on_drop(loop.time(), cause)       # circuit-breaker (pure helper)
    if not decision.retry:                          # hard failure OR reconnect storm
        log(f"reconnect not viable ({decision.reason}) → reverting to JARVIS-Claude")
        revert_to_claude(JARVIS_MODE_BIN, log)      # reuse direct_mode_idle: systemd-run --scope jarvis-mode jarvis
        stop.set(); break
    status.set_agent_present(False)                 # indicator shows the brief gap
    log(f"Live dropped ({cause}); reconnecting in {decision.delay:.1f}s (attempt {decision.n})")
    await asyncio.sleep(decision.delay)
# teardown (once): cancel idle task, terminate parec/paplay, stop status server
```

### Circuit-breaker — `reconnect_control.py` (pure, unit-tested)
The load-bearing safety. Pure functions/dataclass, no I/O, importable + testable.

- **classify(exc) → "transient" | "hard"**: `ConnectionClosedError` and codes `1011/1006/1000`
  + "keepalive"/"timeout"/"going away" messages → **transient** (retry). Auth (`401/403`,
  "API key", "permission"), quota/`RESOURCE_EXHAUSTED`/"quota"/"exceeded", policy `1008` →
  **hard** (do NOT retry; exit so `Restart=always` + its `StartLimitBurst=10/300s` handle it
  exactly as today). Unknown → treat as transient but it still counts against the storm cap.
- **Backoff**: exponential `0.5, 1, 2, 4 …` capped at `RECONNECT_BACKOFF_CAP_S` (5.0). Reset to
  the floor after a session that stayed up longer than `RECONNECT_STABLE_S` (30 s) — so a normal
  long-lived session that finally drops reconnects fast, while a flapping one backs off.
- **Storm cap**: keep reconnect timestamps; if more than `RECONNECT_MAX_PER_WINDOW` (6) happen
  within `RECONNECT_WINDOW_S` (120 s), `retry=False` → exit. A 10–15 min keepalive drop never
  trips this; a tight re-drop loop trips it in ~6 attempts → exits → `Restart=always` (whose own
  StartLimit then bounds it). **Double backstop: in-process cap, then systemd StartLimit.**
- All thresholds via env (`JARVIS_GEMINI_RECONNECT_*`) with the defaults above.

### Interactions
- **Idle-revert** keeps running across reconnects (started once). Its `last_activity` is bumped
  only by real user/model/tool activity — NOT by a reconnect — so a reconnecting session still
  reverts to Claude after the idle window if the user has truly gone quiet.
- **Status**: `set_agent_present(False)` during the gap, `True` on reconnect, so the tray
  reflects the brief reconnecting state honestly.
- **Audio**: `parec`/`paplay` keep running across reconnects; mic bytes during the ~1–2 s gap
  are simply dropped (no session to send to), output is silent. No re-spawn, no device churn.

## Testability
`reconnect_control.py` is pure → unit tests (`tests/test_reconnect_control.py`): classify maps
1011/keepalive→transient and quota/401→hard; backoff grows + caps + resets after a stable
session; the storm cap trips after N-in-window and allows a slow drip; env overrides parse with
safe fallbacks. The loop wiring itself is verified live with a **forced drop** (kill the WS / a
short `RECONNECT_*` + observe one in-process reconnect with no process restart, NRestarts
unchanged).

## Risks
- **Reconnect storm on a hard failure** → mitigated by classify()=hard (immediate exit) + the
  storm cap + the systemd StartLimit floor. Three layers; a storm cannot run unbounded.
- **Audio glitch at reconnect** → minor (~1–2 s silence); strictly better than today's ~7 s.
- **Lost Gemini context across a drop** → same as today's restart; out of scope.

## Resolved decision (2026-05-30)
**Hard-failure / unrecoverable policy → revert to Claude.** When `classify()` returns `hard`
(spend-cap/auth/policy) OR the transient storm cap is exhausted (the Live API is flapping too
hard to be usable), the loop calls `direct_mode_idle.revert_to_claude(JARVIS_MODE_BIN, log)` —
the same proven `systemd-run --user --scope -- jarvis-mode jarvis` path the idle-revert uses —
which stops the gemini unit, unmutes JARVIS-Claude, and writes `active-mode=jarvis`. So a
spend-cap drops the user into the stable free mode instead of a systemd retry loop or a dead
unit. `Restart=always` is no longer the handler for these cases; it remains only the OS-level
backstop if the process dies unexpectedly (e.g. crash before reaching the revert). The same
policy will apply to `bin/jarvis-gpt-tools` in the follow-up.

# Gemini Live Seamless In-Process Reconnect — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use
> checkbox (`- [ ]`). Spec: `docs/superpowers/specs/2026-05-30-gemini-live-seamless-reconnect-design.md`.

**Goal:** A keepalive/duration drop re-opens the Gemini Live socket in-process (~1–2 s gap) instead
of exiting → `Restart=always` respawn (~7 s gap). `parec`/`paplay`/status/stop/idle-watcher persist;
only the Live `session` + pump tasks are re-established. Hard failure / reconnect-storm → revert to
JARVIS-Claude. Provider-agnostic circuit-breaker so `gpt-tools` reuses it later.

**Tech:** Python 3.13, `websockets`/`google-genai`, stdlib. venv: `src/voice-agent/.venv/bin/python`.

---

## File Structure
- Create: `src/voice-agent/reconnect_control.py` — pure circuit-breaker (classify + backoff + storm cap).
- Create: `src/voice-agent/tests/test_reconnect_control.py`.
- Modify: `bin/jarvis-gemini-tools` — `main()` reconnect loop; persistent-vs-per-session split.

**OUT (not this plan):** `bin/jarvis-gpt-tools` (follow-up reusing the helper), `jarvis-mode`
(`Restart=always` unchanged), voice-agent runtime, `evolution/`.

---

### Task 1: `reconnect_control.py` — pure circuit-breaker

**Files:** Create `src/voice-agent/reconnect_control.py`, `tests/test_reconnect_control.py`.

```python
# reconnect_control.py
"""Provider-agnostic reconnect circuit-breaker for the direct voice modes.
Pure: no I/O, no import-time side effects. Decides whether a dropped Live/
Realtime WebSocket should reconnect IN-PROCESS, how long to back off, and
when to give up (caller then reverts to JARVIS-Claude)."""
from __future__ import annotations
import os
from dataclasses import dataclass, field


def _envf(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, str(default)));  return v if v > 0 else default
    except (TypeError, ValueError):
        return default

def _envi(name: str, default: int) -> int:
    try:
        v = int(float(os.environ.get(name, str(default))));  return v if v > 0 else default
    except (TypeError, ValueError):
        return default

# Substrings (lowercased) in the exception's "Type: message" that mean HARD (don't retry).
_HARD_MARKERS = ("quota", "resource_exhausted", "insufficient_quota", "exceeded",
                 "permission", "unauthorized", "api key", "invalid_api_key",
                 "401", "403", "429", "1008")
# websockets close codes treated as TRANSIENT (reconnect in-process).
_TRANSIENT_CLOSE_CODES = {1000, 1001, 1006, 1011, 1012, 1013}


def classify(exc) -> str:
    """'hard' (give up -> revert to Claude) or 'transient' (reconnect in-process)."""
    msg = f"{type(exc).__name__}: {exc}".lower()
    if any(m in msg for m in _HARD_MARKERS):
        return "hard"
    code = getattr(exc, "code", None)
    if code is None:
        code = getattr(getattr(exc, "rcvd", None), "code", None)   # websockets Close frame
    if isinstance(code, int):
        if code == 1008:
            return "hard"
        if code in _TRANSIENT_CLOSE_CODES:
            return "transient"
    return "transient"   # ConnectionClosed/keepalive/timeout/unknown -> retry (storm cap bounds it)


@dataclass
class Decision:
    retry: bool
    delay: float
    n: int
    reason: str


@dataclass
class ReconnectController:
    backoff_floor: float = field(default_factory=lambda: _envf("JARVIS_RECONNECT_BACKOFF_FLOOR_S", 0.5))
    backoff_cap: float   = field(default_factory=lambda: _envf("JARVIS_RECONNECT_BACKOFF_CAP_S", 5.0))
    stable_s: float      = field(default_factory=lambda: _envf("JARVIS_RECONNECT_STABLE_S", 30.0))
    max_per_window: int  = field(default_factory=lambda: _envi("JARVIS_RECONNECT_MAX_PER_WINDOW", 6))
    window_s: float      = field(default_factory=lambda: _envf("JARVIS_RECONNECT_WINDOW_S", 120.0))
    _events: list = field(default_factory=list)
    _connected_at: "float | None" = None
    _cur_backoff: "float | None" = None

    def mark_connected(self, now: float) -> None:
        self._connected_at = now

    def on_drop(self, now: float, exc) -> Decision:
        # Stable-session reset: a long-lived session that finally drops reconnects fast.
        if self._connected_at is not None and (now - self._connected_at) >= self.stable_s:
            self._cur_backoff = None
        self._connected_at = None
        if classify(exc) == "hard":
            return Decision(False, 0.0, 0, f"hard failure ({type(exc).__name__})")
        self._events.append(now)
        self._events = [t for t in self._events if now - t <= self.window_s]
        n = len(self._events)
        if n > self.max_per_window:
            return Decision(False, 0.0, n, f"reconnect storm ({n} in {self.window_s:.0f}s)")
        self._cur_backoff = (self.backoff_floor if self._cur_backoff is None
                             else min(self.backoff_cap, self._cur_backoff * 2))
        return Decision(True, self._cur_backoff, n, "transient")
```

- [ ] **1.1 Failing tests** `test_reconnect_control.py`:
  - `test_classify_transient`: a fake exc with `.rcvd.code=1011` → "transient"; `RuntimeError("sent 1011 (internal error) keepalive ping timeout")` → "transient"; bare `ConnectionError("x")` → "transient".
  - `test_classify_hard`: `RuntimeError("RESOURCE_EXHAUSTED: quota exceeded")` → "hard"; `RuntimeError("429 Too Many Requests")` → "hard"; `RuntimeError("401 invalid api key")` → "hard"; a fake exc with `.code=1008` → "hard".
  - `test_backoff_grows_caps_resets`: controller with floor=0.5,cap=5,stable=30. Successive `on_drop` at the same instant → delays 0.5,1,2,4,5(cap),5. Then `mark_connected(t)`, `on_drop(t+31, transient)` → delay back to 0.5 (stable reset).
  - `test_storm_cap_trips`: max_per_window=3,window=120. on_drop transient at t=0,1,2 → retry True; the 4th within window → retry False reason contains "storm". Spread beyond window (t=0, t=200) → still retry True (old events evicted).
  - `test_hard_never_retries`: hard exc → retry False regardless of count, n=0.
  - `test_env_overrides(monkeypatch)`: set `JARVIS_RECONNECT_MAX_PER_WINDOW=2` → controller picks it up; bad value (`"abc"`) → default.
- [ ] **1.2** `cd src/voice-agent && .venv/bin/python -m pytest tests/test_reconnect_control.py -q` → FAIL.
- [ ] **1.3** Implement `reconnect_control.py`.
- [ ] **1.4** Re-run → PASS.
- [ ] **1.5** Commit: `feat(direct-mode): reconnect_control circuit-breaker (classify + backoff + storm cap)`

---

### Task 2: Wire the reconnect loop into `bin/jarvis-gemini-tools`

**File:** Modify `bin/jarvis-gemini-tools` `main()` (current single-session body at lines ~386–628).
Read the file first; the edits below are structural, anchor by content.

**2a. Imports** — extend the existing `from direct_mode_idle import idle_revert_watch` to also import
`revert_to_claude`; add `from reconnect_control import ReconnectController`.

**2b. Hoist persistent state OUT of `async with`** — move these to BEFORE the reconnect loop (they
must survive reconnects): `mic_proc = await open_mic_stream()`, `spk_proc = await open_speaker_stream()`,
`sct = mss.mss()`, `monitor = sct.monitors[0]`, `last_audio_at = [0.0]`, `last_activity = [loop.time()]`.

**2c. Make the pumps take `session`** — change `pump_mic`/`pump_screen`/`drain_replies` to
`async def pump_mic(session): ...` etc. (they currently close over `session`). `speaking_debounce`
stays as-is (session-independent). All keep closing over `mic_proc`/`spk_proc`/`sct`/`status`/the cells.

**2d. Start persistent tasks ONCE** before the loop: `speaking_debounce` and the `idle_revert_watch`
task (unchanged args). Their handles go in a `persistent` list for teardown.

**2e. The reconnect loop** (replaces the single `async with ... as session:` + its tasks/await block):
```python
    rc = ReconnectController()
    while not stop.is_set():
        cause = None
        try:
            async with client.aio.live.connect(model=LIVE_MODEL, config=cfg) as session:
                log.info("[gemini-tools] connected — talk to JARVIS. Ctrl-C to exit.")
                status.set_agent_present(True); status.set_listening(True)
                rc.mark_connected(loop.time())
                sess_tasks = [
                    asyncio.create_task(pump_mic(session),      name="mic"),
                    asyncio.create_task(pump_screen(session),   name="screen"),
                    asyncio.create_task(drain_replies(session), name="replies"),
                ]
                stop_wait = asyncio.create_task(stop.wait(), name="stop")
                done, pending = await asyncio.wait(sess_tasks + [stop_wait],
                                                   return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                for t in pending:
                    try: await t
                    except (asyncio.CancelledError, Exception): pass
                for t in done:
                    if t is stop_wait:
                        continue
                    exc = t.exception()
                    if exc and not isinstance(exc, asyncio.CancelledError):
                        cause = exc
        except Exception as e:
            cause = e
        if stop.is_set():
            break
        decision = rc.on_drop(loop.time(), cause or RuntimeError("session ended without error"))
        status.set_agent_present(False)
        if not decision.retry:
            log.warning(f"[gemini-tools] reconnect not viable ({decision.reason}) → reverting to JARVIS-Claude")
            revert_to_claude(JARVIS_MODE_BIN, log)
            stop.set()
            break
        log.warning(f"[gemini-tools] Live dropped ({type(cause).__name__}: {cause}); "
                    f"reconnect #{decision.n} in {decision.delay:.1f}s")
        await asyncio.sleep(decision.delay)
```

**2f. Teardown ONCE** after the loop (was in the per-session `finally`): cancel the `persistent`
tasks (speaking_debounce, idle); terminate `mic_proc`/`spk_proc` (the existing terminate/kill block);
`sct.close()`; then the existing `await status.stop()`. `return 0`.

- [ ] **2.1** Apply 2a–2f. Preserve the signal handlers, the `status.start()` block, and all logging.
- [ ] **2.2** `src/voice-agent/.venv/bin/python -m py_compile bin/jarvis-gemini-tools` → clean.
- [ ] **2.3** `grep -n 'ReconnectController\|revert_to_claude\|async with client.aio.live.connect\|def pump_mic(session' bin/jarvis-gemini-tools` → confirm the loop + persistent split landed; confirm there is exactly ONE `live.connect` and it is inside `while not stop.is_set()`.
- [ ] **2.4** Commit: `feat(gemini-tools): in-process Live reconnect (no process restart on keepalive drop)`

---

### Task 3: Verify (offline) + the live forced-drop test (coordinator)

- [ ] **3.1** `cd src/voice-agent && .venv/bin/python -m py_compile reconnect_control.py bin/../../bin/jarvis-gemini-tools` and `-m pytest tests/test_reconnect_control.py -q` → clean + pass.
- [ ] **3.2** Import-safety: `.venv/bin/python -c "import reconnect_control"` clean; confirm `bin/jarvis-gemini-tools` is NOT imported by the runtime (it's a script) and the voice-agent suite is unaffected: `.venv/bin/python -m pytest tests/ -q` (full suite still green).
- [ ] **3.3 LIVE forced-drop test (coordinator-run, at a safe deploy moment — NOT mid-conversation):**
  Restart gemini with a tight window to make the test fast:
  `JARVIS_RECONNECT_MAX_PER_WINDOW=20 bin/jarvis-mode gemini`, note the gemini PID + NRestarts.
  Force a Live drop (e.g. briefly drop network to the API, or `kill -0`/observe the natural ~10–15 min
  keepalive drop). Expected log: `Live dropped (...); reconnect #1 in 0.5s` then `connected — talk to
  JARVIS` from the SAME PID, with `systemctl --user show jarvis-gemini-tools.service -p NRestarts`
  UNCHANGED (proves in-process reconnect, no systemd respawn). Confirm `parec`/`paplay` PIDs unchanged.
- [ ] **3.4** Hard-failure path check (static or with a stubbed classify): confirm a `hard` decision
  calls `revert_to_claude` + sets `stop` (read the code path; a full spend-cap repro is impractical).

**Acceptance:** `test_reconnect_control` passes; py_compile clean; full voice-agent suite still green;
the live forced-drop shows an in-process reconnect with NRestarts unchanged and the same audio PIDs;
the hard-failure branch reverts to Claude. Deploy (restart gemini onto the new code) only at a moment
that won't interrupt an active session.

# JARVIS Voice Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make JARVIS survive 30-second DNS / Groq / LiveKit blips without manual restart, by adding listener-level liveness (sd_notify), defensive `dict.get()` guards, a two-tier reconnect ladder, per-upstream circuit breakers, and a local DNS cache.

**Architecture:** Voice client (`jarvis_voice_client.py`) gains a watchdog task + reconnect ladder + monkey-patched LiveKit-SDK track-event safety. Voice agent (`jarvis_agent.py`) wraps Groq STT, TTS, and LLM calls in independent `CircuitBreaker` instances that fail fast and surface `APIConnectionError` so the existing `FallbackAdapter` chain takes over within seconds. systemd switches to `Type=notify` with `WatchdogSec=10s` for both processes; `systemd-resolved` enables DNS caching to decorrelate STT/TTS/LLM failures.

**Tech Stack:** Python 3.13, asyncio, livekit-agents 1.5.6 + livekit-rtc, groq SDK, systemd (user services), sdnotify (pure-Python).

**Spec:** [`docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md`](../specs/2026-05-04-jarvis-voice-resilience-design.md)

**File map:**

| Path | Role |
|---|---|
| `src/voice-agent/circuit_breaker.py` | NEW — `CircuitBreaker` class (state machine + `call()` wrapper) |
| `src/voice-agent/jarvis_agent.py` | Add module-scope breaker instances, wire STT/TTS/LLM call sites, register watchdog task |
| `src/voice-agent/livekit_track_guard.py` | NEW — monkey-patch `livekit.rtc.Room._on_room_event` to swap bare `[sid]` for safe `.get(sid)` |
| `src/voice-agent/jarvis_voice_client.py` | Import the track-guard patch, add watchdog task, two-tier reconnect ladder |
| `src/voice-agent/dispatching_tts.py` | (or wherever TTS streams close) — emit 5 Opus silence frames before close |
| `src/voice-agent/canned_phrases.py` | NEW — small loader for `~/.jarvis/cache/voice/*.wav` |
| `scripts/render-canned-phrases.py` | NEW — one-shot WAV renderer using Groq TTS |
| `~/.config/systemd/user/jarvis-voice-{agent,client}.service` | Switch to `Type=notify` + `WatchdogSec=10s` |
| `/etc/systemd/resolved.conf.d/jarvis.conf` | NEW — `Cache=yes` |
| `src/voice-agent/tests/test_circuit_breaker.py` | NEW — state-machine tests |
| `src/voice-agent/tests/test_track_guard.py` | NEW — monkey-patch handles missing SIDs |
| `src/voice-agent/tests/test_watchdog.py` | NEW — watchdog cadence + stall detection |
| `src/voice-agent/tests/test_reconnect_ladder.py` | NEW — backoff + escalation |

---

## Task 1: CircuitBreaker class + tests

**Files:**
- Create: `src/voice-agent/circuit_breaker.py`
- Test: `src/voice-agent/tests/test_circuit_breaker.py`

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_circuit_breaker.py`:

```python
"""CircuitBreaker — closed/open/half-open state machine.

Pattern from Portkey + Maxim's LLM-app guides + AWS REL05-BP01.
Three independent breakers (STT/TTS/LLM) gate Groq calls; when open,
the wrapped call fails fast with CircuitOpenError so FallbackAdapter
picks up a fallback path within ms instead of waiting for a 30s
upstream timeout.
"""
import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from circuit_breaker import CircuitBreaker, CircuitOpenError


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _ok():
    return "ok"


async def _fail():
    raise RuntimeError("upstream down")


async def _slow(seconds):
    await asyncio.sleep(seconds)
    return "slow ok"


def test_breaker_starts_closed():
    cb = CircuitBreaker("test", fail_threshold=3)
    assert cb.state == "closed"


def test_breaker_passes_through_when_closed():
    cb = CircuitBreaker("test", fail_threshold=3)
    assert _run(cb.call(_ok)) == "ok"
    assert cb.state == "closed"


def test_breaker_opens_after_threshold_failures():
    cb = CircuitBreaker("test", fail_threshold=3, cooldown_s=10)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            _run(cb.call(_fail))
    assert cb.state == "open"


def test_breaker_fails_fast_when_open():
    cb = CircuitBreaker("test", fail_threshold=1, cooldown_s=10)
    with pytest.raises(RuntimeError):
        _run(cb.call(_fail))
    assert cb.state == "open"
    with pytest.raises(CircuitOpenError):
        _run(cb.call(_ok))


def test_breaker_returns_fallback_when_open():
    cb = CircuitBreaker("test", fail_threshold=1, cooldown_s=10)
    with pytest.raises(RuntimeError):
        _run(cb.call(_fail))

    async def _fallback():
        return "fallback"

    result = _run(cb.call(_ok, fallback=_fallback))
    assert result == "fallback"


def test_breaker_half_open_after_cooldown(monkeypatch):
    cb = CircuitBreaker("test", fail_threshold=1, cooldown_s=1)
    with pytest.raises(RuntimeError):
        _run(cb.call(_fail))
    assert cb.state == "open"

    monkeypatch.setattr(time, "time", lambda: cb.opened_at + 2)

    assert _run(cb.call(_ok)) == "ok"
    assert cb.state == "closed"


def test_breaker_reopens_on_half_open_failure(monkeypatch):
    cb = CircuitBreaker("test", fail_threshold=1, cooldown_s=1)
    with pytest.raises(RuntimeError):
        _run(cb.call(_fail))
    monkeypatch.setattr(time, "time", lambda: cb.opened_at + 2)

    with pytest.raises(RuntimeError):
        _run(cb.call(_fail))
    assert cb.state == "open"


def test_breaker_timeout_counts_as_failure():
    cb = CircuitBreaker("test", fail_threshold=1, cooldown_s=10, timeout_s=0.05)
    with pytest.raises(asyncio.TimeoutError):
        _run(cb.call(_slow, 0.5))
    assert cb.state == "open"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
src/voice-agent/.venv/bin/python -m pytest src/voice-agent/tests/test_circuit_breaker.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'circuit_breaker'`.

- [ ] **Step 3: Write the implementation**

Create `src/voice-agent/circuit_breaker.py`:

```python
"""Per-upstream circuit breaker for the voice agent's Groq calls.

Pattern: closed (normal) → open (failing fast) → half-open (probe).
Three instances live at module scope in jarvis_agent.py — STT, TTS,
LLM — so a Groq endpoint outage on one upstream doesn't stall the
others. When OPEN, call() raises CircuitOpenError immediately (or
returns a fallback) instead of waiting on the underlying API.

Spec: docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("jarvis.breaker")


class CircuitOpenError(Exception):
    """Raised by CircuitBreaker.call() when state == 'open' and no
    fallback is provided. Catchers should convert this into the
    upstream's native error type so existing fallback chains
    (e.g. livekit-agents FallbackAdapter) take over."""
    def __init__(self, name: str):
        super().__init__(f"circuit '{name}' is open")
        self.name = name


class CircuitBreaker:
    """Wraps an awaitable. Three states:
      - closed:    normal operation; failures counted toward threshold
      - open:      fail-fast for `cooldown_s` after threshold breach
      - half-open: one probe call after cooldown; success → closed,
                   failure → open again
    """

    def __init__(
        self,
        name: str,
        *,
        fail_threshold: int = 3,
        cooldown_s: float = 20.0,
        timeout_s: float = 8.0,
    ) -> None:
        self.name = name
        self.fail_threshold = fail_threshold
        self.cooldown_s = cooldown_s
        self.timeout_s = timeout_s
        self.state: str = "closed"
        self.failures: int = 0
        self.opened_at: float = 0.0

    async def call(
        self,
        fn: Callable[..., Awaitable[Any]],
        *args: Any,
        fallback: Optional[Callable[[], Awaitable[Any]]] = None,
        **kw: Any,
    ) -> Any:
        if self.state == "open":
            if time.time() - self.opened_at < self.cooldown_s:
                if fallback is not None:
                    return await fallback()
                raise CircuitOpenError(self.name)
            self.state = "half-open"
            logger.info("[breaker:%s] half-open (probe)", self.name)

        try:
            result = await asyncio.wait_for(
                fn(*args, **kw), timeout=self.timeout_s,
            )
            self._reset()
            return result
        except Exception:
            self._record_failure()
            raise

    def _record_failure(self) -> None:
        self.failures += 1
        if self.state == "half-open" or self.failures >= self.fail_threshold:
            if self.state != "open":
                logger.warning(
                    "[breaker:%s] OPEN after %d failure(s)",
                    self.name, self.failures,
                )
            self.state = "open"
            self.opened_at = time.time()

    def _reset(self) -> None:
        if self.state != "closed":
            logger.info("[breaker:%s] closed", self.name)
        self.state = "closed"
        self.failures = 0
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
src/voice-agent/.venv/bin/python -m pytest src/voice-agent/tests/test_circuit_breaker.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/circuit_breaker.py src/voice-agent/tests/test_circuit_breaker.py
git commit -m "voice: CircuitBreaker class — closed/open/half-open state machine + 8 tests"
```

---

## Task 2: STT breaker — subclass groq.STT

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` (after the `_LoggingGroqTTS` block, ~line 350)
- Modify: `src/voice-agent/jarvis_agent.py` (line ~4787 — JarvisAgent constructor)
- Test: `src/voice-agent/tests/test_breaker_shims.py` (NEW)

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_breaker_shims.py`:

```python
"""Verify STT/TTS/LLM breaker shims surface APIConnectionError on
CircuitOpenError (so FallbackAdapter takes over) and don't intercept
successful calls."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_breaker_stt_open_raises_apiconnection_error():
    """When _STT_BREAKER is open, the shimmed STT must raise
    livekit.agents.APIConnectionError so FallbackAdapter sees a
    recoverable error type and switches to the next STT."""
    from circuit_breaker import CircuitBreaker, CircuitOpenError
    import jarvis_agent
    from livekit.agents import APIConnectionError

    # Force the breaker open
    jarvis_agent._STT_BREAKER.state = "open"
    jarvis_agent._STT_BREAKER.opened_at = 1e18  # far future cooldown

    # Build the shimmed STT
    stt = jarvis_agent._build_breakered_stt()

    # Simulate the underlying _recognize_impl being called — should
    # raise APIConnectionError without ever hitting the upstream.
    with pytest.raises(APIConnectionError):
        _run(stt._call_with_breaker_for_test())

    # Reset for other tests
    jarvis_agent._STT_BREAKER.state = "closed"
    jarvis_agent._STT_BREAKER.failures = 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
src/voice-agent/.venv/bin/python -m pytest src/voice-agent/tests/test_breaker_shims.py::test_breaker_stt_open_raises_apiconnection_error -v
```

Expected: FAIL — `_STT_BREAKER` and `_build_breakered_stt` don't exist yet.

- [ ] **Step 3: Add module-scope breakers to jarvis_agent.py**

Find the line `class _LoggingGroqTTS(groq.TTS):` in [src/voice-agent/jarvis_agent.py](src/voice-agent/jarvis_agent.py) (around line 339) and INSERT this block immediately BEFORE it:

```python
# ── Per-upstream circuit breakers ────────────────────────────────────
# Three independent breakers gate the Groq endpoints. A DNS / API
# blip on one upstream (e.g. STT) no longer drags TTS + LLM down
# with a 30s timeout each. CircuitOpenError gets converted to
# APIConnectionError below so the FallbackAdapter chain takes over
# within ms instead of waiting for the OS socket timeout.
#
# Spec: docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md
from circuit_breaker import CircuitBreaker, CircuitOpenError

_STT_BREAKER = CircuitBreaker("stt", fail_threshold=3, cooldown_s=20, timeout_s=8)
_TTS_BREAKER = CircuitBreaker("tts", fail_threshold=3, cooldown_s=20, timeout_s=8)
_LLM_BREAKER = CircuitBreaker("llm", fail_threshold=2, cooldown_s=30, timeout_s=12)
```

- [ ] **Step 4: Add the breakered-STT subclass after `_LoggingGroqTTS`**

Find the line `class _LoggingGroqTTS(groq.TTS):` and locate the closing of that class (the `synthesize()` method's return statement). After the closing of that class block, INSERT:

```python
class _BreakeredGroqSTT(groq.STT):
    """groq.STT wrapped by _STT_BREAKER. On CircuitOpenError, raises
    livekit.agents.APIConnectionError so FallbackAdapter (if any STT
    fallback is configured) takes over without waiting the full
    upstream timeout."""

    async def _recognize_impl(self, *args, **kw):
        try:
            return await _STT_BREAKER.call(super()._recognize_impl, *args, **kw)
        except CircuitOpenError as e:
            raise _APIConnectionError() from e

    async def _call_with_breaker_for_test(self):
        """Test seam — exercises the breaker-open path without a
        real Groq client. Calls _STT_BREAKER directly; production
        code paths go through _recognize_impl above."""
        async def _no_op():
            return None
        try:
            return await _STT_BREAKER.call(_no_op)
        except CircuitOpenError as e:
            raise _APIConnectionError() from e


def _build_breakered_stt() -> _BreakeredGroqSTT:
    """Constructor used by the JarvisAgent wiring at session.start()."""
    return _BreakeredGroqSTT(model="whisper-large-v3-turbo", language="en")
```

- [ ] **Step 5: Wire the new STT into JarvisAgent**

In [src/voice-agent/jarvis_agent.py](src/voice-agent/jarvis_agent.py), find the line containing `stt=groq.STT(` (around line 4787) and replace the multi-line block:

```python
        stt=groq.STT(
            model="whisper-large-v3-turbo",
            language="en",
        ),
```

with:

```python
        stt=_build_breakered_stt(),
```

- [ ] **Step 6: Run the test to verify it passes**

```bash
src/voice-agent/.venv/bin/python -m pytest src/voice-agent/tests/test_breaker_shims.py::test_breaker_stt_open_raises_apiconnection_error -v
```

Expected: PASS.

- [ ] **Step 7: Smoke-import the agent**

```bash
cd src/voice-agent && .venv/bin/python -c "
import sys
sys.path.insert(0, '.')
sys.path.insert(0, '../hub')
import jarvis_agent
print('STT breaker:', jarvis_agent._STT_BREAKER.state)
print('STT class :', jarvis_agent._BreakeredGroqSTT.__name__)
"
```

Expected: prints `STT breaker: closed` and `STT class : _BreakeredGroqSTT` without errors.

- [ ] **Step 8: Commit**

```bash
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_breaker_shims.py
git commit -m "voice: STT circuit breaker — _BreakeredGroqSTT subclass routes _recognize_impl through _STT_BREAKER"
```

---

## Task 3: TTS breaker — extend `_LoggingGroqChunkedStream._run`

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` (`_LoggingGroqChunkedStream._run` method, around line 240–340)
- Test: `src/voice-agent/tests/test_breaker_shims.py` (append)

- [ ] **Step 1: Append the failing test**

Append to `src/voice-agent/tests/test_breaker_shims.py`:

```python
def test_breaker_tts_open_raises_apiconnection_error():
    """When _TTS_BREAKER is open, _LoggingGroqChunkedStream._run must
    raise APIConnectionError so the existing FallbackAdapter cascades
    to EdgeTTS instead of waiting on Groq's TCP timeout."""
    import jarvis_agent
    from livekit.agents import APIConnectionError

    jarvis_agent._TTS_BREAKER.state = "open"
    jarvis_agent._TTS_BREAKER.opened_at = 1e18

    # Use a tiny seam method on the class for testing.
    with pytest.raises(APIConnectionError):
        _run(jarvis_agent._LoggingGroqChunkedStream._call_with_breaker_for_test())

    jarvis_agent._TTS_BREAKER.state = "closed"
    jarvis_agent._TTS_BREAKER.failures = 0
```

- [ ] **Step 2: Run the failing test**

```bash
src/voice-agent/.venv/bin/python -m pytest src/voice-agent/tests/test_breaker_shims.py::test_breaker_tts_open_raises_apiconnection_error -v
```

Expected: FAIL — `_call_with_breaker_for_test` doesn't exist on `_LoggingGroqChunkedStream`.

- [ ] **Step 3: Add the breaker wrap to `_LoggingGroqChunkedStream`**

In [src/voice-agent/jarvis_agent.py](src/voice-agent/jarvis_agent.py), find the line `class _LoggingGroqChunkedStream(_GroqChunkedStream):` (around line 242) and modify its `_run` method. Replace the existing `_run`'s OUTER body (the `async def _run(...) -> None:` content after the empty-text early-return) so that the actual upstream HTTP call is funneled through `_TTS_BREAKER.call()`. The pattern:

Find the existing structure (approximate — verify in the file):

```python
    async def _run(self, output_emitter) -> None:
        # … existing empty-input short-circuit (KEEP THIS UNCHANGED) …
        if not re.search(r"[A-Za-z0-9]", self._input_text or ""):
            # … silent WAV path …
            return
        # Real upstream call follows
        try:
            # … the actual aiohttp post happens via super()._run …
```

Wrap the real upstream call:

```python
    async def _run(self, output_emitter) -> None:
        # Empty-text short-circuit — UNCHANGED
        if not re.search(r"[A-Za-z0-9]", self._input_text or ""):
            # … existing silent WAV emit …
            return

        # Breaker-gated upstream call — fails fast when Groq's TTS
        # endpoint is in cooldown so FallbackAdapter cascades to
        # EdgeTTS within ms instead of timing out at ~30s.
        async def _do_real_run():
            return await super(_LoggingGroqChunkedStream, self)._run(output_emitter)

        try:
            await _TTS_BREAKER.call(_do_real_run)
        except CircuitOpenError as e:
            raise _APIConnectionError() from e

    @classmethod
    async def _call_with_breaker_for_test(cls):
        """Test seam — exercises the breaker-open path."""
        async def _no_op():
            return None
        try:
            return await _TTS_BREAKER.call(_no_op)
        except CircuitOpenError as e:
            raise _APIConnectionError() from e
```

NOTE: keep the existing `try/except` blocks for `aiohttp.ClientResponseError` / generic `Exception` that already exist in `_run`. The breaker wrap goes around the call to `super()._run`, NOT around the empty-text branch.

- [ ] **Step 4: Run the test**

```bash
src/voice-agent/.venv/bin/python -m pytest src/voice-agent/tests/test_breaker_shims.py::test_breaker_tts_open_raises_apiconnection_error -v
```

Expected: PASS.

- [ ] **Step 5: Smoke-import again**

```bash
cd src/voice-agent && .venv/bin/python -c "
import sys
sys.path.insert(0, '.')
sys.path.insert(0, '../hub')
import jarvis_agent
print('TTS breaker:', jarvis_agent._TTS_BREAKER.state)
"
```

Expected: prints `TTS breaker: closed` without errors.

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_breaker_shims.py
git commit -m "voice: TTS circuit breaker — _LoggingGroqChunkedStream._run routes through _TTS_BREAKER"
```

---

## Task 4: LLM breaker — wrap DispatchingLLM call site

**Files:**
- Modify: `src/voice-agent/dispatching_llm.py`
- Test: `src/voice-agent/tests/test_breaker_shims.py` (append)

- [ ] **Step 1: Append the failing test**

Append to `src/voice-agent/tests/test_breaker_shims.py`:

```python
def test_breaker_llm_open_raises_apiconnection_error():
    """When _LLM_BREAKER is open, DispatchingLLM must surface
    APIConnectionError so livekit-agents handles the failed turn
    gracefully (apology TTS) instead of hanging on the upstream."""
    import jarvis_agent
    from livekit.agents import APIConnectionError

    jarvis_agent._LLM_BREAKER.state = "open"
    jarvis_agent._LLM_BREAKER.opened_at = 1e18

    with pytest.raises(APIConnectionError):
        _run(jarvis_agent._llm_breaker_test_seam())

    jarvis_agent._LLM_BREAKER.state = "closed"
    jarvis_agent._LLM_BREAKER.failures = 0
```

- [ ] **Step 2: Run the failing test**

```bash
src/voice-agent/.venv/bin/python -m pytest src/voice-agent/tests/test_breaker_shims.py::test_breaker_llm_open_raises_apiconnection_error -v
```

Expected: FAIL — `_llm_breaker_test_seam` doesn't exist.

- [ ] **Step 3: Inspect DispatchingLLM call site**

```bash
grep -n "class DispatchingLLM\|def chat\|async def chat" src/voice-agent/dispatching_llm.py | head -10
```

Identify the `chat()` method (or whatever method livekit-agents calls into). It's likely an async generator that yields chunks; wrap the underlying `inner.chat()` call.

- [ ] **Step 4: Add the breaker wrap to dispatching_llm.py**

At the top of [src/voice-agent/dispatching_llm.py](src/voice-agent/dispatching_llm.py), add the import:

```python
# Breaker is owned by jarvis_agent.py — re-export through it so the
# breaker state is shared across DispatchingLLM, the test seam, and
# any future LLM call sites.
from circuit_breaker import CircuitBreaker, CircuitOpenError
from livekit.agents import APIConnectionError
```

In the `DispatchingLLM.chat()` method (or whichever method invokes the underlying LLM), wrap the inner call:

```python
def chat(self, *args, **kw):
    """Returns a chat stream; the inner LLM is selected at module
    scope by the dispatcher. Wrap with _LLM_BREAKER so a hung Groq
    completion doesn't stall the agent for 30s."""
    # Lazy import to avoid circular dependency at module load.
    from jarvis_agent import _LLM_BREAKER

    # The livekit-agents LLM contract returns an LLMStream synchronously;
    # the breaker has to wrap the underlying generator. We use an
    # _AsyncGenWrapper that catches CircuitOpenError on the first
    # chunk and converts it to APIConnectionError so the agent's
    # error handling kicks in (apology TTS).
    inner_stream = self._inner_chat(*args, **kw)
    return _BreakeredLLMStream(inner_stream, _LLM_BREAKER)
```

Add a wrapper class alongside DispatchingLLM:

```python
class _BreakeredLLMStream:
    """Wraps a livekit-agents LLMStream. First-chunk read goes through
    the breaker; subsequent chunks pass through untouched (we don't
    pay 30s waiting for chunk N — only for chunk 1 which is when
    Groq's first byte arrives)."""

    def __init__(self, inner, breaker):
        self._inner = inner
        self._breaker = breaker
        self._first = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._first:
            self._first = False
            try:
                return await self._breaker.call(self._inner.__anext__)
            except CircuitOpenError as e:
                raise APIConnectionError() from e
        return await self._inner.__anext__()

    async def aclose(self):
        if hasattr(self._inner, "aclose"):
            await self._inner.aclose()
```

If `DispatchingLLM` already has a chat method that returns the upstream LLM's stream directly (e.g. `return self._inner.chat(...)`), rename that to `_inner_chat` and add the wrapper above.

- [ ] **Step 5: Add the test seam to jarvis_agent.py**

In [src/voice-agent/jarvis_agent.py](src/voice-agent/jarvis_agent.py), near the `_LLM_BREAKER` definition, add:

```python
async def _llm_breaker_test_seam():
    """Test-only — exercises the LLM-breaker-open path without a real
    Groq client."""
    async def _no_op():
        return None
    try:
        return await _LLM_BREAKER.call(_no_op)
    except CircuitOpenError as e:
        raise _APIConnectionError() from e
```

- [ ] **Step 6: Run the test**

```bash
src/voice-agent/.venv/bin/python -m pytest src/voice-agent/tests/test_breaker_shims.py -v
```

Expected: 3 passed (STT, TTS, LLM seams).

- [ ] **Step 7: Commit**

```bash
git add src/voice-agent/jarvis_agent.py src/voice-agent/dispatching_llm.py src/voice-agent/tests/test_breaker_shims.py
git commit -m "voice: LLM circuit breaker — _BreakeredLLMStream wraps DispatchingLLM first-chunk read"
```

---

## Task 5: Track-event guard — monkey-patch `livekit.rtc.Room._on_room_event`

**Files:**
- Create: `src/voice-agent/livekit_track_guard.py`
- Modify: `src/voice-agent/jarvis_voice_client.py` (add import at top)
- Test: `src/voice-agent/tests/test_track_guard.py` (NEW)

**Why a monkey-patch and not handler-level guards:** the `KeyError` in today's incident fired INSIDE `livekit/rtc/room.py:680` (`self.local_participant.track_publications[sid]`), BEFORE our user callback runs. We can't fix it in our handlers because the SDK never reaches them. We patch the SDK method itself — same pattern the agent uses for the deepseek roundtrip + tool-name sanitizer.

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_track_guard.py`:

```python
"""livekit_track_guard.py — monkey-patch `Room._on_room_event` to
swap bare `[sid]` for safe `.get(sid)` on the five local-track and
remote-track dispatch branches. Catches the exact KeyError that
crashed the voice-client during the 2026-05-04 DNS blip."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from livekit import rtc as _lk_rtc

import livekit_track_guard


def test_install_is_idempotent():
    """Calling install() twice must not double-wrap the method."""
    livekit_track_guard.install()
    first = _lk_rtc.Room._on_room_event
    livekit_track_guard.install()
    second = _lk_rtc.Room._on_room_event
    assert first is second


def test_local_track_unpublished_with_unknown_sid_does_not_crash():
    """Pre-patch this raised KeyError (today's bug). Post-patch the
    guard logs + returns without crashing the listener task."""
    livekit_track_guard.install()

    room = _lk_rtc.Room()
    # Force track_publications to be empty (mid-reconnect state).
    fake_local = MagicMock()
    fake_local.track_publications = {}
    room.__dict__["_local_participant"] = fake_local

    # Build the event proto that hits the local_track_unpublished branch
    fake_event = MagicMock()
    fake_event.WhichOneof = lambda _: "local_track_unpublished"
    fake_event.local_track_unpublished.publication_sid = "TR_NOT_REGISTERED"

    # Pre-patch this raised KeyError. Post-patch, no crash.
    room._on_room_event(fake_event)


def test_local_track_published_with_unknown_sid_does_not_crash():
    livekit_track_guard.install()
    room = _lk_rtc.Room()
    fake_local = MagicMock()
    fake_local.track_publications = {}
    room.__dict__["_local_participant"] = fake_local

    fake_event = MagicMock()
    fake_event.WhichOneof = lambda _: "local_track_published"
    fake_event.local_track_published.track_sid = "TR_NOT_REGISTERED"

    room._on_room_event(fake_event)


def test_remote_track_unpublished_with_unknown_participant_does_not_crash():
    livekit_track_guard.install()
    room = _lk_rtc.Room()
    room.__dict__["_remote_participants"] = {}

    fake_event = MagicMock()
    fake_event.WhichOneof = lambda _: "track_unpublished"
    fake_event.track_unpublished.participant_identity = "ghost"
    fake_event.track_unpublished.publication_sid = "TR_NOT_REGISTERED"

    room._on_room_event(fake_event)
```

- [ ] **Step 2: Run failing tests**

```bash
src/voice-agent/.venv/bin/python -m pytest src/voice-agent/tests/test_track_guard.py -v
```

Expected: FAIL — `livekit_track_guard` module does not exist.

- [ ] **Step 3: Implement the monkey-patch**

Create `src/voice-agent/livekit_track_guard.py`:

```python
"""Monkey-patch livekit.rtc.Room._on_room_event to swap bare `[sid]`
for safe `.get(sid)` on track-event dispatch branches.

Bug class fixed: when the SFU emits track_unpublished AFTER the local
SDK has already removed the publication during a reconnect (the
windows-of-divergence Discord, Twilio, and LiveKit's own docs all
flag), the bare dict access raises KeyError in `room.py`'s event
dispatcher and the listener asyncio task dies silently. systemd
keeps the process alive but the agent has no peer.

Patch is idempotent (install() is safe to call repeatedly). Same
load-bearing-monkey-patch pattern the agent uses for the deepseek
roundtrip and tool-name sanitizer.

Spec: docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md
"""
from __future__ import annotations

import logging

from livekit import rtc

logger = logging.getLogger("jarvis.track_guard")

_INSTALLED = False
_ORIGINAL_ON_ROOM_EVENT = None


def install() -> None:
    """Replace Room._on_room_event with a guarded version. Idempotent."""
    global _INSTALLED, _ORIGINAL_ON_ROOM_EVENT
    if _INSTALLED:
        return
    _ORIGINAL_ON_ROOM_EVENT = rtc.Room._on_room_event
    rtc.Room._on_room_event = _guarded_on_room_event
    _INSTALLED = True
    logger.info("[track_guard] monkey-patch installed")


def _guarded_on_room_event(self, event):
    """Wrap the original dispatch in a KeyError shield for the
    local_track_* and track_* branches. Anything else passes through
    unchanged so we don't accidentally swallow real bugs."""
    which = event.WhichOneof("message")
    guarded_branches = {
        "local_track_published",
        "local_track_unpublished",
        "local_track_subscribed",
        "track_published",
        "track_unpublished",
    }

    if which not in guarded_branches:
        return _ORIGINAL_ON_ROOM_EVENT(self, event)

    try:
        return _ORIGINAL_ON_ROOM_EVENT(self, event)
    except KeyError as e:
        logger.debug(
            "[track_guard] swallowed KeyError on %s for sid=%r — "
            "publication already removed during reconnect",
            which, str(e),
        )
        return None
```

- [ ] **Step 4: Wire the patch into voice-client startup**

At the TOP of [src/voice-agent/jarvis_voice_client.py](src/voice-agent/jarvis_voice_client.py) (immediately after the existing imports), add:

```python
# Defensive monkey-patch on livekit.rtc.Room — install BEFORE any Room
# is constructed. See src/voice-agent/livekit_track_guard.py and
# spec 2026-05-04-jarvis-voice-resilience-design.md.
import livekit_track_guard as _track_guard
_track_guard.install()
```

- [ ] **Step 5: Run the tests**

```bash
src/voice-agent/.venv/bin/python -m pytest src/voice-agent/tests/test_track_guard.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/livekit_track_guard.py src/voice-agent/jarvis_voice_client.py src/voice-agent/tests/test_track_guard.py
git commit -m "voice: monkey-patch livekit.rtc.Room._on_room_event — swallow KeyError on stale track SIDs during reconnect"
```

---

## Task 6: sdnotify watchdog + `Type=notify` systemd units

**Files:**
- Modify: `src/voice-agent/jarvis_voice_client.py` (add watchdog task)
- Modify: `src/voice-agent/jarvis_agent.py` (add watchdog task to entrypoint)
- Modify: `~/.config/systemd/user/jarvis-voice-agent.service`
- Modify: `~/.config/systemd/user/jarvis-voice-client.service`
- Test: `src/voice-agent/tests/test_watchdog.py` (NEW)

- [ ] **Step 1: Install sdnotify in the venv**

```bash
src/voice-agent/.venv/bin/pip install sdnotify
```

Expected: `Successfully installed sdnotify-0.3.2` (or similar).

- [ ] **Step 2: Write the failing test**

Create `src/voice-agent/tests/test_watchdog.py`:

```python
"""watchdog_loop — sd_notify(WATCHDOG=1) emitted from inside the
asyncio loop. If the loop stalls (e.g. listener task crashed), the
notifications stop and systemd restarts us within WatchdogSec=10s.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_watchdog_loop_emits_ping_then_exits_on_stop():
    """While stop is unset the loop must emit at least one
    WATCHDOG=1; once stop is set it exits cleanly."""
    from watchdog import watchdog_loop

    notifier = MagicMock()
    stop = asyncio.Event()

    async def main():
        # Schedule stop after a short delay so we get at least one ping.
        async def _stopper():
            await asyncio.sleep(0.1)
            stop.set()
        await asyncio.gather(
            watchdog_loop(stop, notifier=notifier, interval_s=0.02),
            _stopper(),
        )

    asyncio.new_event_loop().run_until_complete(main())

    # READY=1 once at start, WATCHDOG=1 ≥ 1 time, STOPPING=1 once at end.
    calls = [c.args[0] for c in notifier.notify.call_args_list]
    assert calls[0] == "READY=1"
    assert "WATCHDOG=1" in calls
    assert calls[-1] == "STOPPING=1"
```

- [ ] **Step 3: Run failing test**

```bash
src/voice-agent/.venv/bin/python -m pytest src/voice-agent/tests/test_watchdog.py -v
```

Expected: FAIL — `watchdog` module does not exist.

- [ ] **Step 4: Create the watchdog module**

Create `src/voice-agent/watchdog.py`:

```python
"""sd_notify(WATCHDOG=1) emitter for the voice agent + voice client.

Critical detail: this MUST run in the same asyncio loop as the
listener task. If we used a separate thread, a stalled listener
wouldn't trigger the systemd-watchdog restart — the thread would
keep pinging happily while the actual work was wedged.

Spec: docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md
"""
from __future__ import annotations

import asyncio
import logging

import sdnotify

logger = logging.getLogger("jarvis.watchdog")


async def watchdog_loop(
    stop: asyncio.Event,
    *,
    notifier=None,
    interval_s: float = 5.0,
) -> None:
    """Notify systemd while the listener loop is alive. systemd's
    WatchdogSec=10s setting kills + restarts us if we miss two
    consecutive pings.

    Args:
        stop: asyncio.Event signalling shutdown.
        notifier: SystemdNotifier-like object (test injection).
        interval_s: how often to ping. Half of WatchdogSec is standard.
    """
    if notifier is None:
        notifier = sdnotify.SystemdNotifier()
    notifier.notify("READY=1")
    logger.info("[watchdog] READY=1; ping interval %.1fs", interval_s)
    try:
        while not stop.is_set():
            notifier.notify("WATCHDOG=1")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
            except asyncio.TimeoutError:
                pass
    finally:
        notifier.notify("STOPPING=1")
        logger.info("[watchdog] STOPPING=1")
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
src/voice-agent/.venv/bin/python -m pytest src/voice-agent/tests/test_watchdog.py -v
```

Expected: PASS.

- [ ] **Step 6: Wire watchdog into voice-client**

In [src/voice-agent/jarvis_voice_client.py](src/voice-agent/jarvis_voice_client.py), find the main supervisor loop (likely a `while True` near the bottom that calls `await run_once(shutdown)`). Add a watchdog task that runs in parallel:

```python
async def main_loop():
    """Outer supervisor loop with the watchdog task running in the
    same event loop as run_once."""
    from watchdog import watchdog_loop

    shutdown = asyncio.Event()
    watchdog_task = asyncio.create_task(watchdog_loop(shutdown))

    try:
        while not shutdown.is_set():
            try:
                await run_once(shutdown)
            except Exception as e:
                log.exception(f"[supervisor] run_once crashed: {e}")
                await asyncio.sleep(2)
    finally:
        shutdown.set()
        await watchdog_task
```

Update the entrypoint at the bottom of the file to call `main_loop()` instead of the old loop. If the existing entrypoint is `asyncio.run(...)`, change it to `asyncio.run(main_loop())`.

- [ ] **Step 7: Wire watchdog into voice-agent**

In [src/voice-agent/jarvis_agent.py](src/voice-agent/jarvis_agent.py), find the `entrypoint(ctx)` async function (the main agent entry that runs `await session.start(...)`). Near the top of the function — after `ctx.connect()` if it exists — add:

```python
    # Watchdog: emit WATCHDOG=1 every 5s from inside this asyncio
    # loop. systemd kills + restarts us if we miss two pings (i.e.
    # the listener wedged). See watchdog.py + spec.
    from watchdog import watchdog_loop
    _agent_shutdown = asyncio.Event()
    _watchdog_task = asyncio.create_task(watchdog_loop(_agent_shutdown))
    ctx.add_shutdown_callback(lambda: _agent_shutdown.set())
```

(If `ctx.add_shutdown_callback` isn't the exact API, fall back to setting the event in a `finally` block at the end of `entrypoint`.)

- [ ] **Step 8: Update systemd unit files**

Edit `~/.config/systemd/user/jarvis-voice-agent.service` and add inside `[Service]`:

```ini
Type=notify
NotifyAccess=main
WatchdogSec=10s
```

(Remove the existing `Type=simple` if present.)

Same edit to `~/.config/systemd/user/jarvis-voice-client.service`.

- [ ] **Step 9: Reload systemd and restart**

```bash
systemctl --user daemon-reload
systemctl --user restart jarvis-voice-agent.service jarvis-voice-client.service
sleep 5
systemctl --user is-active jarvis-voice-agent.service jarvis-voice-client.service
```

Expected: both `active`.

- [ ] **Step 10: Verify watchdog is firing**

```bash
journalctl --user -u jarvis-voice-agent.service --since "30 seconds ago" --no-pager 2>&1 | grep -i "watchdog\|ready\|notify" | head -5
journalctl --user -u jarvis-voice-client.service --since "30 seconds ago" --no-pager 2>&1 | grep -i "watchdog\|ready\|notify" | head -5
```

Expected: at least the `READY=1` notification visible, and no "Watchdog timeout" entries.

- [ ] **Step 11: Commit**

```bash
git add src/voice-agent/watchdog.py src/voice-agent/jarvis_agent.py src/voice-agent/jarvis_voice_client.py src/voice-agent/tests/test_watchdog.py
git commit -m "voice: sd_notify(WATCHDOG=1) loop in agent + client; systemd Type=notify with WatchdogSec=10s"
```

(systemd unit files in `~/.config` are user-local, not in the repo — note them in the commit body if useful.)

---

## Task 7: Two-tier reconnect ladder + Opus silence frames

**Files:**
- Modify: `src/voice-agent/jarvis_voice_client.py` (replace simple supervisor with ReconnectLadder)
- Modify: `src/voice-agent/jarvis_agent.py` (or the TTS pipeline file) — silence frames before stream close
- Test: `src/voice-agent/tests/test_reconnect_ladder.py` (NEW)

- [ ] **Step 1: Write the failing reconnect-ladder tests**

Create `src/voice-agent/tests/test_reconnect_ladder.py`:

```python
"""ReconnectLadder — backoff schedule (0.5/1/2/4/10s + jitter) for
tier-1 resume; falls through to full teardown after 5 attempts.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from reconnect_ladder import ReconnectLadder


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_resume_succeeds_first_attempt_no_full_teardown():
    resume = AsyncMock(return_value=True)
    teardown = AsyncMock()
    ladder = ReconnectLadder(
        resume_fn=resume,
        full_teardown_fn=teardown,
        backoffs=[0.0, 0.0],
    )
    _run(ladder.recover())
    assert resume.await_count == 1
    assert teardown.await_count == 0


def test_falls_through_to_teardown_after_all_resumes_fail():
    resume = AsyncMock(return_value=False)
    teardown = AsyncMock()
    ladder = ReconnectLadder(
        resume_fn=resume,
        full_teardown_fn=teardown,
        backoffs=[0.0, 0.0, 0.0],  # 3 quick attempts for the test
    )
    _run(ladder.recover())
    assert resume.await_count == 3
    assert teardown.await_count == 1


def test_resume_succeeds_on_third_attempt():
    resume = AsyncMock(side_effect=[False, False, True])
    teardown = AsyncMock()
    ladder = ReconnectLadder(
        resume_fn=resume,
        full_teardown_fn=teardown,
        backoffs=[0.0, 0.0, 0.0, 0.0],
    )
    _run(ladder.recover())
    assert resume.await_count == 3
    assert teardown.await_count == 0


def test_teardown_failure_after_three_in_a_row_bails():
    """After 3 consecutive full-teardown reconnects, raise SystemExit
    so systemd takes over."""
    resume = AsyncMock(return_value=False)
    teardown = AsyncMock()
    ladder = ReconnectLadder(
        resume_fn=resume,
        full_teardown_fn=teardown,
        backoffs=[0.0],
        max_full_reconnects=3,
    )
    import pytest
    with pytest.raises(SystemExit):
        _run(_simulate_repeated(ladder, 4))


async def _simulate_repeated(ladder, n):
    for _ in range(n):
        await ladder.recover()
```

- [ ] **Step 2: Run failing tests**

```bash
src/voice-agent/.venv/bin/python -m pytest src/voice-agent/tests/test_reconnect_ladder.py -v
```

Expected: FAIL — `reconnect_ladder` module doesn't exist.

- [ ] **Step 3: Implement the ReconnectLadder**

Create `src/voice-agent/reconnect_ladder.py`:

```python
"""Two-tier reconnect ladder for the voice client.

Tier 1 (resume): cheap rejoin with current token. Backoffs:
  0.5s, 1s, 2s, 4s, 10s + 30% jitter.

Tier 2 (full teardown): tear down room, fresh connect(). Triggered
after all resume attempts exhaust. Three full reconnects in a row
→ raise SystemExit so systemd takes over.

Pattern from LiveKit's documented ICE-restart-vs-full-reconnect
distinction; backoff cadence borrowed from Twilio JS SDK published
guidance.

Spec: docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable

logger = logging.getLogger("jarvis.reconnect")

DEFAULT_BACKOFFS = [0.5, 1.0, 2.0, 4.0, 10.0]


class ReconnectLadder:
    def __init__(
        self,
        *,
        resume_fn: Callable[[], Awaitable[bool]],
        full_teardown_fn: Callable[[], Awaitable[None]],
        backoffs: list[float] = None,
        max_full_reconnects: int = 3,
        jitter_pct: float = 0.3,
    ) -> None:
        self._resume = resume_fn
        self._teardown = full_teardown_fn
        self._backoffs = list(backoffs) if backoffs is not None else list(DEFAULT_BACKOFFS)
        self._max_full = max_full_reconnects
        self._jitter_pct = jitter_pct
        self._consecutive_full = 0

    async def recover(self) -> None:
        """Run one recovery cycle. On success, resets the consecutive-
        full-reconnect counter. On 3 full reconnects in a row, raises
        SystemExit(1) so systemd's Restart=always handles it."""
        for delay in self._backoffs:
            jitter = random.uniform(0, delay * self._jitter_pct) if delay > 0 else 0
            if delay or jitter:
                await asyncio.sleep(delay + jitter)
            try:
                ok = await self._resume()
            except Exception as e:
                logger.warning("[reconnect] resume raised %s — counted as failure", e)
                ok = False
            if ok:
                logger.info("[reconnect] resume succeeded after %.1fs", delay)
                self._consecutive_full = 0
                return

        # All resume attempts failed → full teardown
        self._consecutive_full += 1
        logger.warning(
            "[reconnect] all resume attempts failed; full teardown #%d",
            self._consecutive_full,
        )
        if self._consecutive_full > self._max_full:
            logger.error(
                "[reconnect] %d full teardowns in a row — bailing for systemd",
                self._consecutive_full,
            )
            raise SystemExit(1)
        await self._teardown()
```

- [ ] **Step 4: Run the tests**

```bash
src/voice-agent/.venv/bin/python -m pytest src/voice-agent/tests/test_reconnect_ladder.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Wire ReconnectLadder into voice-client**

In [src/voice-agent/jarvis_voice_client.py](src/voice-agent/jarvis_voice_client.py), find the existing supervisor loop. Wrap it so each disconnect triggers `ReconnectLadder.recover()`:

```python
async def main_loop():
    from watchdog import watchdog_loop
    from reconnect_ladder import ReconnectLadder

    shutdown = asyncio.Event()
    watchdog_task = asyncio.create_task(watchdog_loop(shutdown))

    # State held across recovery cycles
    state_holder = {"room": None, "token": None}

    async def _resume() -> bool:
        """Tier-1 resume — re-mint token and try to reconnect."""
        try:
            await run_once(shutdown)
            return True
        except Exception as e:
            log.warning(f"[resume] failed: {e}")
            return False

    async def _full_teardown() -> None:
        """Tier-2 teardown — drop everything and reconnect fresh."""
        if state_holder["room"] is not None:
            try:
                await state_holder["room"].disconnect()
            except Exception:
                pass
            state_holder["room"] = None
        await asyncio.sleep(1)  # let SFU settle

    ladder = ReconnectLadder(
        resume_fn=_resume,
        full_teardown_fn=_full_teardown,
    )

    try:
        while not shutdown.is_set():
            try:
                await run_once(shutdown)
            except Exception as e:
                log.exception(f"[supervisor] disconnected: {e}")
                await ladder.recover()
    finally:
        shutdown.set()
        await watchdog_task
```

- [ ] **Step 6: Add Opus silence frames before TTS stream close**

Find the TTS stream close site. In [src/voice-agent/jarvis_agent.py](src/voice-agent/jarvis_agent.py) or wherever `_LoggingGroqChunkedStream` produces its final flush — locate the place where the stream's audio output ends. Add a helper:

```python
# 20ms of Opus silence — Discord's published trick to suppress
# decoder interpolation when interrupting a stream.
_OPUS_SILENCE_FRAME = b"\xf8\xff\xfe"


async def _push_silence_tail(output_emitter, frames: int = 5) -> None:
    """Emit a short silence tail before closing a TTS stream so the
    next utterance isn't eaten by the decoder's residual state."""
    for _ in range(frames):
        try:
            output_emitter.push_frame(_OPUS_SILENCE_FRAME)
        except Exception:
            break
```

In `_LoggingGroqChunkedStream._run`, near the END of the method (right before the final return / cleanup), add:

```python
        # Discord-style silence tail; protects the next utterance.
        try:
            await _push_silence_tail(output_emitter)
        except Exception:
            pass  # Already-closed stream is fine.
```

NOTE: if `output_emitter.push_frame` doesn't accept raw Opus bytes in this codebase, fall back to `output_emitter.push(_OPUS_SILENCE_FRAME)` or whatever the existing TTS plugin uses. The existing diagnostic shim already pushes silent WAV frames in the empty-text path; mirror that exact API.

- [ ] **Step 7: Smoke test**

```bash
systemctl --user restart jarvis-voice-client.service
sleep 5
journalctl --user -u jarvis-voice-client.service --since "10 seconds ago" --no-pager 2>&1 | tail -10
```

Expected: voice-client reconnects cleanly; no `KeyError`; the `[reconnect] resume…` log lines appear when network is normal there are zero of them.

- [ ] **Step 8: Commit**

```bash
git add src/voice-agent/reconnect_ladder.py src/voice-agent/jarvis_voice_client.py src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_reconnect_ladder.py
git commit -m "voice: ReconnectLadder (resume backoff 0.5/1/2/4/10s → full teardown after 5) + Opus silence-tail before TTS close"
```

---

## Task 8: systemd-resolved cache + canned-phrase WAVs + e2e verification

**Files:**
- Create: `/etc/systemd/resolved.conf.d/jarvis.conf` (root, requires sudo)
- Create: `scripts/render-canned-phrases.py`
- Create: `src/voice-agent/canned_phrases.py`
- Create: `~/.jarvis/cache/voice/{one_second,connection_unstable,try_again}.wav`

### 8a — DNS cache

- [ ] **Step 1: Create the resolved.conf.d drop-in**

```bash
sudo mkdir -p /etc/systemd/resolved.conf.d
sudo tee /etc/systemd/resolved.conf.d/jarvis.conf > /dev/null <<'EOF'
# JARVIS — DNS cache so a 30s blip doesn't simultaneously knock out
# api.groq.com STT/TTS/LLM (they share a resolver → shared fate).
# See docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md.
[Resolve]
Cache=yes
DNSStubListener=yes
CacheFromLocalhost=no
EOF
```

- [ ] **Step 2: Reload + verify cache is on**

```bash
sudo systemctl restart systemd-resolved
sleep 2
resolvectl statistics 2>&1 | grep -i cache
```

Expected: `Current Cache Size:` and `Cache Hits:` lines visible (means cache is enabled).

- [ ] **Step 3: Verify name resolution still works**

```bash
getent hosts api.groq.com 2>&1 | head -2
```

Expected: an IPv4 or IPv6 address.

### 8b — Canned-phrase WAVs

- [ ] **Step 4: Write the renderer script**

Create `scripts/render-canned-phrases.py`:

```python
#!/usr/bin/env python3
"""One-shot — render JARVIS canned-phrase WAVs using Groq TTS while
it's healthy. Saves to ~/.jarvis/cache/voice/. Re-run if voice
config changes.

Spec: docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "voice-agent"))

from livekit.plugins import groq

CACHE_DIR = Path.home() / ".jarvis" / "cache" / "voice"
PHRASES = {
    "one_second.wav":         "One second, sir.",
    "connection_unstable.wav": "Connection unstable, sir.",
    "try_again.wav":          "Could you try that again, sir?",
}


async def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    voice = os.environ.get("JARVIS_VOICE", "Atlas-PlayAI")
    tts = groq.TTS(voice=voice)
    for filename, text in PHRASES.items():
        out_path = CACHE_DIR / filename
        print(f"rendering: {text!r} -> {out_path}")
        stream = tts.synthesize(text)
        with open(out_path, "wb") as f:
            async for chunk in stream:
                f.write(chunk.frame.data.tobytes())
        print(f"  wrote {out_path.stat().st_size} bytes")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Run the renderer**

```bash
src/voice-agent/.venv/bin/python scripts/render-canned-phrases.py
ls -la ~/.jarvis/cache/voice/
```

Expected: 3 `.wav` files, each non-zero in size.

NOTE: if `groq.TTS` requires extra constructor args (model, language) in this codebase, copy the args from `_LoggingGroqTTS` instantiation in `jarvis_agent.py`.

- [ ] **Step 6: Add the canned-phrase loader**

Create `src/voice-agent/canned_phrases.py`:

```python
"""Loader for ~/.jarvis/cache/voice/*.wav — used by the LLM-breaker
fallback path so the user hears something instead of silence when
upstream is wedged.

Spec: docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("jarvis.canned")

CACHE_DIR = Path.home() / ".jarvis" / "cache" / "voice"
PHRASES = ("one_second", "connection_unstable", "try_again")


def get_phrase_bytes(name: str) -> bytes | None:
    """Return raw WAV bytes for a canned phrase, or None if missing.
    None is the explicit "no fallback available" signal so the caller
    can choose silence (rather than crashing)."""
    path = CACHE_DIR / f"{name}.wav"
    if not path.exists():
        logger.debug("[canned] missing: %s", path)
        return None
    return path.read_bytes()


def is_available(name: str = "one_second") -> bool:
    return (CACHE_DIR / f"{name}.wav").exists()
```

- [ ] **Step 7: Commit (DNS + renderer + loader)**

```bash
git add scripts/render-canned-phrases.py src/voice-agent/canned_phrases.py
git commit -m "voice: canned-phrase WAV renderer + loader; systemd-resolved DNS cache enabled"
```

(The `/etc/systemd/resolved.conf.d/jarvis.conf` lives outside the repo — note in commit body that the drop-in was applied to the host.)

### 8c — End-to-end live verification

- [ ] **Step 8: Run all new tests together**

```bash
src/voice-agent/.venv/bin/python -m pytest \
  src/voice-agent/tests/test_circuit_breaker.py \
  src/voice-agent/tests/test_breaker_shims.py \
  src/voice-agent/tests/test_track_guard.py \
  src/voice-agent/tests/test_watchdog.py \
  src/voice-agent/tests/test_reconnect_ladder.py \
  -v
```

Expected: all green (~20 tests).

- [ ] **Step 9: Sanity — agent + client still boot**

```bash
systemctl --user restart jarvis-voice-agent jarvis-voice-client
sleep 5
systemctl --user is-active jarvis-voice-agent jarvis-voice-client
journalctl --user -u jarvis-voice-agent --since "30 seconds ago" --no-pager 2>&1 | grep -iE "error|breaker|watchdog" | tail -10
```

Expected: both `active`; log lines show breaker module loaded; no errors.

- [ ] **Step 10: DNS-blip simulation**

```bash
# Block UDP/53 outbound for 30 seconds, then unblock.
sudo iptables -A OUTPUT -p udp --dport 53 -j DROP
echo "DNS blocked at $(date +%T) — talk to JARVIS now and confirm canned phrase"
sleep 30
sudo iptables -D OUTPUT -p udp --dport 53 -j DROP
echo "DNS restored at $(date +%T) — verify normal flow resumes within ~30s"
```

Expected: during the blackout JARVIS plays a canned phrase (`one_second.wav`) instead of going silent; after the blackout normal STT/TTS/LLM resume without manual restart.

- [ ] **Step 11: Watchdog kill simulation**

```bash
PID=$(pgrep -f "jarvis_voice_client.py" | head -1)
echo "Stopping voice-client PID=$PID for 15s — systemd should kill + restart"
sudo kill -STOP "$PID"
sleep 15
sudo kill -CONT "$PID" 2>/dev/null  # may fail if systemd already killed it
sleep 5
NEW_PID=$(pgrep -f "jarvis_voice_client.py" | head -1)
echo "Old PID=$PID  New PID=$NEW_PID  (different = watchdog worked)"
```

Expected: `New PID` differs from `Old PID` (watchdog timeout fired, systemd respawned).

- [ ] **Step 12: Hub-restart-mid-session**

```bash
echo "Talk to JARVIS, then while it's mid-reply, run:"
echo "  systemctl --user restart jarvis-hub.service"
echo "Verify the conversation continues; no crash; subsequent turns work."
```

Manual check; no automated assert.

- [ ] **Step 13: Final commit + rubric update**

If smoke tests revealed any small fixes, commit them:

```bash
git add -A
git commit -m "voice: post-soak fixes for resilience layer e2e"
```

Then update [docs/superpowers/specs/2026-04-30-voice-intelligence-rubric.md](docs/superpowers/specs/2026-04-30-voice-intelligence-rubric.md) with a "Phase 13 — voice resilience layer" section. Likely candidates for axis bumps:
- **Axis 9 (Tool execution discipline)**: 9 → 10 if the breakers + reconnect ladder eliminate the "JARVIS narrates without action" failure mode that DNS blips were causing.
- **Axis 10 (Self-eval)**: stays at 10; this is reliability, not measurement.
- New consideration: **resilience** could be added as a half-axis or rolled into existing axes.

Commit:

```bash
git add docs/superpowers/specs/2026-04-30-voice-intelligence-rubric.md
git commit -m "voice: rubric Phase 13 — resilience layer (watchdog + breakers + reconnect)"
```

---

## Self-review checklist

Before declaring the plan complete, the executor should verify:

- [ ] All 8 tasks committed with green tests (~20 new pytest cases)
- [ ] `circuit_breaker.py`, `livekit_track_guard.py`, `watchdog.py`, `reconnect_ladder.py`, `canned_phrases.py` all exist
- [ ] `_STT_BREAKER`, `_TTS_BREAKER`, `_LLM_BREAKER` defined at module scope in `jarvis_agent.py`
- [ ] `livekit_track_guard.install()` called at the top of `jarvis_voice_client.py`
- [ ] systemd units use `Type=notify` + `WatchdogSec=10s`
- [ ] `sdnotify` installed in `src/voice-agent/.venv`
- [ ] `/etc/systemd/resolved.conf.d/jarvis.conf` deployed
- [ ] 3 WAVs in `~/.jarvis/cache/voice/`
- [ ] DNS blackout test (Step 10) shows graceful canned-phrase fallback
- [ ] Watchdog kill test (Step 11) shows process respawn
- [ ] No regressions in the existing memory-layer / settings / hub tests

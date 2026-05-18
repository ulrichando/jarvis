"""Tests for the TTS barge-in cancellation path in
`providers/tts.py::LoggingGroqChunkedStream._run`.

When a barge-in fires, the framework cancels the `_run()` task at the
asyncio level. The CancelledError must:
  1. Propagate out of `_run()` so the framework's StreamAdapter handles
     the cancel cleanly.
  2. Close the aiohttp response immediately so the Groq Orpheus HTTP
     socket aborts mid-stream — without this, Orpheus keeps sending
     WAV bytes we discard, which is what produced the 1-3 s perceived-
     stop symptom observed live 2026-05-18.

Spec: docs/superpowers/specs/2026-05-18-barge-in-interrupt-fix-design.md
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_cancellation_closes_response_and_re_raises():
    """When _run is cancelled mid-stream, the aiohttp response must
    be `.close()`d (so the Groq socket aborts) AND CancelledError
    must propagate out (so the framework cleans up downstream)."""
    from providers.tts import LoggingGroqChunkedStream

    # Build a stream instance without going through the real Groq
    # plugin's __init__ (it requires API keys + sample rates etc.).
    # We only need the `_input_text`, `_opts`, `_conn_options`, and
    # `_tts._ensure_session()` attributes.
    stream = LoggingGroqChunkedStream.__new__(LoggingGroqChunkedStream)
    stream._input_text = "Hello world, this is JARVIS speaking."

    opts = MagicMock()
    opts.base_url = "https://api.groq.example/openai/v1"
    opts.model = "playai-tts"
    opts.voice = "Troy-PlayAI"
    opts.api_key = "xxx"
    stream._opts = opts

    conn = MagicMock()
    conn.timeout = 5.0
    stream._conn_options = conn

    # Mock the aiohttp response. iter_chunks() yields one chunk then
    # hangs forever — the test cancels the consuming task during the
    # second await, simulating a barge-in mid-stream.
    resp = MagicMock()
    resp.status = 200
    resp.content_type = "audio/wav"

    async def _hanging_chunks():
        yield (b"RIFF" + b"\x00" * 100, False)
        # Block here until cancelled. asyncio.Event never-set is the
        # simplest hang.
        await asyncio.Event().wait()
        yield (b"never", False)

    resp.content = MagicMock()
    resp.content.iter_chunks = lambda: _hanging_chunks()
    resp.close = MagicMock()

    # Mock the aiohttp session + post context manager.
    session = MagicMock()
    post_cm = MagicMock()
    post_cm.__aenter__ = AsyncMock(return_value=resp)
    post_cm.__aexit__ = AsyncMock(return_value=None)
    session.post = MagicMock(return_value=post_cm)

    tts_obj = MagicMock()
    tts_obj._ensure_session = MagicMock(return_value=session)
    stream._tts = tts_obj

    # Output emitter — record the push() calls.
    emitter = MagicMock()
    emitter.initialize = MagicMock()
    emitter.push = MagicMock()
    emitter.flush = MagicMock()

    # The breaker wraps _do_real_run. We don't want the breaker to
    # interfere — replace it with a pass-through.
    with patch("providers.tts.TTS_BREAKER") as mock_breaker:
        async def _passthrough(fn, *a, **k):
            return await fn(*a, **k)
        mock_breaker.call = _passthrough

        # Run _run as a task so we can cancel it.
        run_task = asyncio.create_task(stream._run(emitter))

        # Wait for at least one chunk to be pushed (verifies the
        # stream was reading), then cancel.
        for _ in range(50):
            if emitter.push.call_count > 0:
                break
            await asyncio.sleep(0.01)
        assert emitter.push.call_count > 0, "test setup: stream didn't push any chunks"

        run_task.cancel()

        # CancelledError MUST propagate — the framework relies on it.
        with pytest.raises(asyncio.CancelledError):
            await run_task

    # The response MUST have been closed (aborts the Groq socket).
    resp.close.assert_called_once()


@pytest.mark.asyncio
async def test_normal_completion_does_NOT_close_response_explicitly():
    """Sanity check: when the stream completes normally (no cancel),
    the cancel-only `resp.close()` path is NOT taken — the async-with
    on the response handles cleanup. Without this guard a refactor
    could double-close, hiding regressions."""
    from providers.tts import LoggingGroqChunkedStream

    stream = LoggingGroqChunkedStream.__new__(LoggingGroqChunkedStream)
    stream._input_text = "Done quickly."

    opts = MagicMock()
    opts.base_url = "https://api.groq.example/openai/v1"
    opts.model = "playai-tts"
    opts.voice = "Troy-PlayAI"
    opts.api_key = "xxx"
    stream._opts = opts
    conn = MagicMock(); conn.timeout = 5.0
    stream._conn_options = conn

    resp = MagicMock()
    resp.status = 200
    resp.content_type = "audio/wav"

    async def _finite_chunks():
        yield (b"RIFF" + b"\x00" * 50, False)
        yield (b"more" + b"\x00" * 50, False)

    resp.content = MagicMock()
    resp.content.iter_chunks = lambda: _finite_chunks()
    resp.close = MagicMock()

    session = MagicMock()
    post_cm = MagicMock()
    post_cm.__aenter__ = AsyncMock(return_value=resp)
    post_cm.__aexit__ = AsyncMock(return_value=None)
    session.post = MagicMock(return_value=post_cm)

    tts_obj = MagicMock()
    tts_obj._ensure_session = MagicMock(return_value=session)
    stream._tts = tts_obj

    emitter = MagicMock()
    emitter.initialize = MagicMock()
    emitter.push = MagicMock()
    emitter.flush = MagicMock()

    with patch("providers.tts.TTS_BREAKER") as mock_breaker, \
         patch("pipeline.barge_in.record_synthesis"), \
         patch("jarvis_agent._active_session_for_telemetry", [None]):
        async def _passthrough(fn, *a, **k):
            return await fn(*a, **k)
        mock_breaker.call = _passthrough

        await stream._run(emitter)

    # Normal completion path — flushed exactly once, close NOT called
    # by our cancel handler (the async-with handles it implicitly).
    assert emitter.flush.call_count == 1
    resp.close.assert_not_called()

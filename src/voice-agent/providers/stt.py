"""Groq Whisper STT wrapped by `STT_BREAKER`.

Breaker behaviour on the STT path:
  * `_recognize_impl` is the only override — it routes the upstream
    call through the breaker so the open-circuit short-cut bypasses
    the underlying socket timeout (~30 s) and fails fast (~ms) into
    FallbackAdapter's next STT (none configured today; this is
    forward-compat).
  * `CircuitOpenError` → `APIConnectionError`,
    `asyncio.TimeoutError` → `APITimeoutError`. Same conversion the
    other breakered provider classes use so livekit-agents' retry
    ladder handles every breaker uniformly.

Hoisted out of `jarvis_agent.py` 2026-05-10 (Step 5a of the 10/10
refactor). The class + factory are re-exported under their legacy
underscored names in jarvis_agent so the ~24 in-file references and
the existing test suite are untouched.
"""
from __future__ import annotations

import asyncio

from livekit.agents import APIConnectionError, APITimeoutError
from livekit.plugins import groq

from resilience import STT_BREAKER
from resilience.circuit_breaker import CircuitOpenError


class BreakeredGroqSTT(groq.STT):
    """groq.STT wrapped by `STT_BREAKER`. On `CircuitOpenError`, raises
    `APIConnectionError` so FallbackAdapter (if any STT fallback is
    configured) takes over without waiting the full upstream timeout."""

    async def _recognize_impl(self, *args, **kw):
        try:
            return await STT_BREAKER.call(super()._recognize_impl, *args, **kw)
        except CircuitOpenError as e:
            raise APIConnectionError() from e
        except asyncio.TimeoutError:
            # Breaker's own 8 s timeout fired (separate from the
            # underlying STT's timeout). Surface as `APITimeoutError`
            # so livekit-agents' retry / fallback path handles it
            # uniformly with other timeout sources.
            raise APITimeoutError() from None

    async def _call_with_breaker_for_test(self):
        """Test seam — instance method so the test exercises
        `build_breakered_stt()` construction, catching factory regressions
        (wrong model string, broken constructor signature) at test time
        rather than at production startup. The body itself only probes
        the breaker-open path; production calls go through
        `_recognize_impl`."""
        async def _no_op():
            return None
        try:
            return await STT_BREAKER.call(_no_op)
        except CircuitOpenError as e:
            raise APIConnectionError() from e
        except asyncio.TimeoutError:
            raise APITimeoutError() from None


def build_breakered_stt() -> BreakeredGroqSTT:
    """Constructor used by the JarvisAgent wiring at session.start()."""
    return BreakeredGroqSTT(model="whisper-large-v3-turbo", language="en")

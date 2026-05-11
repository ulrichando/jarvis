"""Wrap each LLM stream in `asyncio.wait_for` so a stalled Groq
connection raises TimeoutError instead of hanging forever.

Live failure 2026-05-02 22:01: supervisor handed off to browser
subagent, subagent's `on_enter` fired, then dead air for 3+
minutes — Groq HTTP stream stalled mid-token. Our `LLM_KWARGS={
"timeout": 5.0, "max_retries": 0}` looks like a fix but is connect-
only (see livekit-agents `types.py` `APIConnectOptions`); once one
chunk arrives, the timer never re-fires.

This module patches `inference.llm.LLMStream._run` to wrap the
original call in `asyncio.wait_for(..., timeout=N)`. On timeout we
raise `APITimeoutError(retryable=True)` so the FallbackAdapter
flips to the secondary LLM (DeepSeek). User gets a (slower) reply
instead of dead silence.

Tunable via `JARVIS_LLM_IDLE_TIMEOUT` (seconds, default 30).
Setting it to 0 disables the wrap (debug only).

Idempotent. Stacks on top of the existing sanitizer patches.
Reference pattern: handoff_text_suppressor.py.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger("jarvis.llm_idle_timeout")


def _timeout_seconds() -> float:
    raw = os.environ.get("JARVIS_LLM_IDLE_TIMEOUT", "30")
    try:
        v = float(raw)
    except ValueError:
        v = 30.0
    return v


def install() -> None:
    """Patch LLMStream._run with an asyncio.wait_for envelope.
    Idempotent."""
    from livekit.agents.inference import llm as inf_llm
    from livekit.agents._exceptions import APITimeoutError

    if getattr(inf_llm.LLMStream, "_jarvis_idle_timeout_patched", False):
        return

    timeout = _timeout_seconds()
    if timeout <= 0:
        logger.warning(
            "JARVIS_LLM_IDLE_TIMEOUT=%s — idle-timeout DISABLED", timeout
        )
        inf_llm.LLMStream._jarvis_idle_timeout_patched = True
        return

    orig_run = inf_llm.LLMStream._run

    async def _patched_run(self) -> None:
        try:
            await asyncio.wait_for(orig_run(self), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "[idle-timeout] LLM stream exceeded %.1fs — raising "
                "APITimeoutError so FallbackAdapter flips to next LLM",
                timeout,
            )
            raise APITimeoutError(retryable=True) from None

    inf_llm.LLMStream._run = _patched_run
    inf_llm.LLMStream._jarvis_idle_timeout_patched = True
    logger.info(
        "LLM idle-timeout installed (timeout=%.1fs via JARVIS_LLM_IDLE_TIMEOUT)",
        timeout,
    )

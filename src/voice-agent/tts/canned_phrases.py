"""Loader for ~/.jarvis/cache/voice/*.wav — the breaker-open
fallback for the LLM circuit breaker. When _LLM_BREAKER is open
and the agent has no completion to speak, it plays one of these
canned WAVs so the user hears something instead of dead air.

Spec: docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("jarvis.canned")

CACHE_DIR = Path.home() / ".jarvis" / "cache" / "voice"
# NB: scripts/render-canned-phrases.py uses PHRASES as a dict
# {filename → text}; this tuple is the stems-only form for the
# loader's get_phrase_bytes(name) lookup. Keep the basenames in
# sync with the renderer.
PHRASES = ("one_second", "connection_unstable", "try_again")


def get_phrase_bytes(name: str) -> bytes | None:
    """Return raw WAV bytes for a canned phrase, or None if missing
    or empty. None is the explicit "no fallback available" signal so
    the caller can choose silence (rather than crashing or sending
    zero bytes downstream which the LiveKit emitter rejects)."""
    if name not in PHRASES:
        logger.warning("[canned] unknown phrase %r — known: %s", name, PHRASES)
        return None
    path = CACHE_DIR / f"{name}.wav"
    if not path.exists():
        logger.info("[canned] missing: %s", path)
        return None
    data = path.read_bytes()
    if not data:
        logger.info("[canned] empty WAV: %s", path)
        return None
    return data


def is_available(name: str = "one_second") -> bool:
    """True if at least the given canned phrase exists and is non-empty.
    Default `one_second` is the most-likely-to-be-used fallback."""
    return get_phrase_bytes(name) is not None

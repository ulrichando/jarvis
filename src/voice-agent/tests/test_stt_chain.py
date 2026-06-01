"""Tests for `providers/stt.py::build_stt_chain` — the Deepgram-then-
Groq-Whisper STT chain added 2026-05-18 for fast barge-in.

The chain must:
  - Return a `FallbackAdapter` of [Deepgram, Whisper] when
    DEEPGRAM_API_KEY is set.
  - Return Groq Whisper alone when the key is unset (graceful
    degradation, safe to ship without the key).
  - Survive Deepgram-plugin-missing / Deepgram-construction-error
    without raising — fall through to Whisper.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


def test_no_deepgram_key_returns_whisper_only(monkeypatch):
    """Without DEEPGRAM_API_KEY, the chain degrades to Whisper alone
    — same as pre-2026-05-18 behaviour."""
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    from providers.stt import build_stt_chain, BreakeredGroqSTT
    chain = build_stt_chain()
    # When Deepgram is absent, the chain is a single Whisper, NOT a
    # FallbackAdapter (the framework accepts both shapes).
    assert isinstance(chain, BreakeredGroqSTT)


def test_with_deepgram_key_returns_fallback_chain(monkeypatch):
    """With DEEPGRAM_API_KEY set + a VAD passed, the chain is a
    FallbackAdapter wrapping [Deepgram, Whisper] in priority order."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-deepgram")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    from livekit.agents.stt import FallbackAdapter
    from livekit.plugins import silero
    from providers.stt import build_stt_chain
    # FallbackAdapter requires a real VAD to auto-wrap the non-streaming
    # Whisper. Use Silero in mock mode — load() lazy-spawns the model.
    # For test, pass any object with the StreamAdapter interface — but
    # easiest is to construct a real one (it's fast enough; ~50 ms).
    vad = silero.VAD.load()
    chain = build_stt_chain(vad=vad)
    assert isinstance(chain, FallbackAdapter), (
        f"expected FallbackAdapter, got {type(chain).__name__}"
    )


def test_with_deepgram_key_no_vad_returns_deepgram_alone(monkeypatch):
    """With Deepgram key but no VAD passed, the chain CAN'T wrap
    Whisper as streaming, so it degrades to Deepgram-alone (better
    than crashing or returning the broken FallbackAdapter)."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-deepgram")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    from livekit.plugins import deepgram
    from providers.stt import build_stt_chain
    chain = build_stt_chain(vad=None)
    assert isinstance(chain, deepgram.STT), (
        f"expected Deepgram STT alone, got {type(chain).__name__}"
    )


def test_deepgram_build_returns_none_falls_through(monkeypatch):
    """When `_build_deepgram_stt` returns None (any of: no key,
    plugin missing, construction error), the chain returns Whisper
    alone — does NOT crash, does NOT return a half-built FallbackAdapter."""
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    from providers import stt as stt_mod
    monkeypatch.setattr(stt_mod, "_build_deepgram_stt", lambda: None)
    chain = stt_mod.build_stt_chain()
    assert isinstance(chain, stt_mod.BreakeredGroqSTT)


def test_deepgram_construction_failure_falls_through(monkeypatch):
    """If deepgram.STT(...) raises (bad config, network at init, etc.),
    log + fall through to Whisper alone."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-deepgram")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    from providers.stt import build_stt_chain, BreakeredGroqSTT
<<<<<<< HEAD
    from providers import stt as _stt_mod
    # Deepgram is now constructed via the language-pinning subclass _DeepgramSTT
    # (so the FallbackAdapter recovery path can't crash on a None language) —
    # patch THAT to simulate a construction failure.
    with patch.object(_stt_mod, "_DeepgramSTT", side_effect=RuntimeError("simulated init fail")):
=======
    from livekit.plugins import deepgram as _dg_mod
    with patch.object(_dg_mod, "STT", side_effect=RuntimeError("simulated init fail")):
>>>>>>> origin/master
        chain = build_stt_chain()
    assert isinstance(chain, BreakeredGroqSTT)


# ── Keyterm boosting (recognition fix, 2026-05-20) ───────────────────
# The echo-vs-accent telemetry diagnosis showed JARVIS's "misheard me"
# turns were dominated by genuine recognition errors, NOT echo — e.g.
# "Joris"/"Jervis" for "Jarvis", which no downstream garbage-gate can
# catch because they're plausible English. Deepgram Nova-3 keyterm
# prompting boosts these at the STT level. (Nova-3 only; `keyterm`,
# never `keywords` — the plugin's _validate_keyterm rejects keywords
# on Nova-3.)

def test_deepgram_stt_boosts_jarvis_keyterm(monkeypatch):
    """The Deepgram Nova-3 STT must ship a keyterm list containing
    'Jarvis' so the wake-name resolves at the STT level instead of
    landing as 'Joris' in the transcript."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-deepgram")
    monkeypatch.delenv("JARVIS_STT_KEYTERMS", raising=False)
    from providers.stt import _build_deepgram_stt
    stt = _build_deepgram_stt()
    assert stt is not None, "Deepgram STT should build with a key set"
    keyterms = [t.lower() for t in stt._opts.keyterm]
    assert "jarvis" in keyterms, (
        f"expected 'jarvis' in keyterm boost list, got {stt._opts.keyterm!r}"
    )


def test_stt_keyterms_extensible_via_env(monkeypatch):
    """Operators extend the boost list with their own names / domain
    vocabulary via JARVIS_STT_KEYTERMS (comma-separated). The default
    'Jarvis' is always present; the list is de-duplicated case-
    insensitively and entries are trimmed."""
    monkeypatch.setenv("JARVIS_STT_KEYTERMS", "Ulrich, LiveKit , jarvis")
    from providers.stt import _stt_keyterms
    terms = _stt_keyterms()
    low = [t.lower() for t in terms]
    assert "jarvis" in low          # default always present
    assert "ulrich" in low          # env-added
    assert "livekit" in low         # env-added, whitespace trimmed
    assert low.count("jarvis") == 1, f"'jarvis' not de-duplicated: {terms!r}"

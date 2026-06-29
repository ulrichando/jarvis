"""Tests for `providers/stt.py::build_stt_chain` — the Deepgram-then-
local-faster-whisper STT chain (fast barge-in via Deepgram streaming).

The chain must:
  - Return a `FallbackAdapter` of [Deepgram, local] when DEEPGRAM_API_KEY
    is set AND the local rung is enabled.
  - Return Deepgram alone when no local rung / no VAD.
  - Return the on-device faster-whisper alone when Deepgram is
    unavailable but the local rung is built.
  - RAISE when neither Deepgram nor a local rung is available — the Groq
    Whisper universal fallback was removed 2026-06-29 (full-Groq-
    eradication pass), so "no STT" is now a loud config error.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch


def test_no_deepgram_no_local_raises(monkeypatch):
    """Without DEEPGRAM_API_KEY and with no local STT rung, there is no
    STT at all (the Groq Whisper fallback was removed) — build_stt_chain
    raises rather than returning a non-STT."""
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_LOCAL_STT_ENABLED", raising=False)
    from providers.stt import build_stt_chain
    with pytest.raises(RuntimeError):
        build_stt_chain()


def test_with_deepgram_key_returns_fallback_chain(monkeypatch):
    """With DEEPGRAM_API_KEY + the local rung enabled + a VAD passed, the
    chain is a FallbackAdapter wrapping [Deepgram, local faster-whisper]."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-deepgram")
    monkeypatch.setenv("JARVIS_LOCAL_STT_ENABLED", "1")
    monkeypatch.setenv("JARVIS_LOCAL_STT_DEVICE", "cpu")
    monkeypatch.setenv("JARVIS_LOCAL_STT_COMPUTE", "int8")
    from livekit.agents.stt import FallbackAdapter
    from livekit.plugins import silero
    from providers.stt import build_stt_chain
    # FallbackAdapter requires a real VAD to auto-wrap the non-streaming
    # local rung. Silero in mock mode — load() lazy-spawns the model.
    vad = silero.VAD.load()
    chain = build_stt_chain(vad=vad)
    assert isinstance(chain, FallbackAdapter), (
        f"expected FallbackAdapter, got {type(chain).__name__}"
    )


def test_with_deepgram_key_no_vad_returns_deepgram_alone(monkeypatch):
    """With Deepgram key but no VAD passed (and no local rung), the chain
    degrades to Deepgram-alone (better than crashing or returning a broken
    FallbackAdapter)."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-deepgram")
    monkeypatch.delenv("JARVIS_LOCAL_STT_ENABLED", raising=False)
    from livekit.plugins import deepgram
    from providers.stt import build_stt_chain
    chain = build_stt_chain(vad=None)
    assert isinstance(chain, deepgram.STT), (
        f"expected Deepgram STT alone, got {type(chain).__name__}"
    )


def test_deepgram_none_no_local_raises(monkeypatch):
    """When `_build_deepgram_stt` returns None (no key / plugin missing /
    construction error) and no local rung is built, build_stt_chain raises
    — it does NOT crash with an empty FallbackAdapter."""
    monkeypatch.delenv("JARVIS_LOCAL_STT_ENABLED", raising=False)
    from providers import stt as stt_mod
    monkeypatch.setattr(stt_mod, "_build_deepgram_stt", lambda: None)
    with pytest.raises(RuntimeError):
        stt_mod.build_stt_chain()


def test_deepgram_disabled_falls_to_local(monkeypatch):
    """JARVIS_DEEPGRAM_DISABLED=1 skips Deepgram even with a key; with the
    local rung enabled the chain runs on on-device faster-whisper alone
    (Groq Whisper was removed 2026-06-29). Reversible by unsetting the flag."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-deepgram")
    monkeypatch.setenv("JARVIS_DEEPGRAM_DISABLED", "1")
    monkeypatch.setenv("JARVIS_LOCAL_STT_ENABLED", "1")
    monkeypatch.setenv("JARVIS_LOCAL_STT_DEVICE", "cpu")
    monkeypatch.setenv("JARVIS_LOCAL_STT_COMPUTE", "int8")
    from providers.stt import build_stt_chain, _build_deepgram_stt
    from providers.faster_whisper_stt import FasterWhisperSTT
    # Short-circuits to None despite the key being set...
    assert _build_deepgram_stt() is None
    # ...so the chain is the on-device faster-whisper alone.
    chain = build_stt_chain()
    assert isinstance(chain, FasterWhisperSTT)


def test_local_only_strips_all_cloud_fallback(monkeypatch):
    """JARVIS_STT_LOCAL_ONLY=1 removes EVERY cloud rung (Deepgram), so the
    chain is the on-device faster-whisper ALONE — 100% local, no cloud
    safety net (user request 2026-06-21). Reversible by unsetting the flag.
    The CPU/int8 build is constructed without loading the model (lazy), so
    this needs no GPU."""
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.setenv("JARVIS_LOCAL_STT_ENABLED", "1")
    monkeypatch.setenv("JARVIS_LOCAL_STT_PRIMARY", "1")
    monkeypatch.setenv("JARVIS_STT_LOCAL_ONLY", "1")
    monkeypatch.setenv("JARVIS_LOCAL_STT_DEVICE", "cpu")
    monkeypatch.setenv("JARVIS_LOCAL_STT_COMPUTE", "int8")
    from providers.stt import build_stt_chain
    from providers.faster_whisper_stt import FasterWhisperSTT
    chain = build_stt_chain()
    assert isinstance(chain, FasterWhisperSTT), (
        f"expected on-device FasterWhisperSTT alone, got {type(chain).__name__}"
    )


def test_local_only_no_local_rung_raises(monkeypatch):
    """JARVIS_STT_LOCAL_ONLY=1 with no local rung built AND no Deepgram
    leaves no STT — build_stt_chain raises (the Groq Whisper fallback that
    used to cover this was removed 2026-06-29)."""
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_LOCAL_STT_ENABLED", raising=False)
    monkeypatch.setenv("JARVIS_STT_LOCAL_ONLY", "1")
    from providers.stt import build_stt_chain
    with pytest.raises(RuntimeError):
        build_stt_chain()


def test_deepgram_construction_failure_falls_to_local(monkeypatch):
    """If _DeepgramSTT(...) raises (bad config, network at init, etc.),
    log + fall through to the local rung."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-deepgram")
    monkeypatch.setenv("JARVIS_LOCAL_STT_ENABLED", "1")
    monkeypatch.setenv("JARVIS_LOCAL_STT_DEVICE", "cpu")
    monkeypatch.setenv("JARVIS_LOCAL_STT_COMPUTE", "int8")
    from providers.stt import build_stt_chain
    from providers.faster_whisper_stt import FasterWhisperSTT
    from providers import stt as _stt_mod
    # Deepgram is constructed via the language-pinning subclass _DeepgramSTT;
    # patch THAT to simulate a construction failure.
    with patch.object(_stt_mod, "_DeepgramSTT", side_effect=RuntimeError("simulated init fail")):
        chain = build_stt_chain()
    assert isinstance(chain, FasterWhisperSTT)


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

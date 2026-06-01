"""Regression: Deepgram STT must never let a None/auto language reach a stream.

The LiveKit FallbackAdapter calls `stt.stream(language=self._language)` on both
its main and RECOVERY paths, where `self._language` can be None. Deepgram
STREAMING rejects a None language with a fatal, recoverable=False ValueError
("language detection is not supported in streaming mode") that tears down the
whole AgentSession before any audio flows — the intermittent "JARVIS can't hear
after a restart" bug (~16% of startups). `providers.stt._DeepgramSTT` closes the
gap by coercing a falsy/auto language back to the pinned construction language.
"""
import pytest

from providers import stt as stt_mod


pytestmark = pytest.mark.skipif(
    stt_mod._DeepgramSTT is None,
    reason="livekit-plugins-deepgram not installed",
)


def _dg():
    return stt_mod._DeepgramSTT(model="nova-3-general", language="en-US", api_key="test")


def test_none_language_coerced_to_pinned():
    # the FallbackAdapter recovery path passes language=None — must NOT become None
    assert _dg()._sanitize_options(language=None).language is not None


def test_unset_language_uses_pinned():
    assert _dg()._sanitize_options().language is not None


def test_real_language_is_preserved():
    # a concrete non-English language must still be honored (multi-lingual users)
    lang = _dg()._sanitize_options(language="es").language
    assert str(lang).lower().startswith("es")


def test_pinned_language_is_en_us_by_default():
    assert str(_dg()._sanitize_options(language=None).language).lower().startswith("en")

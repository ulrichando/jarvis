"""DispatchingTTS — language axis on pick().

en + any route → route's English inner (existing behaviour).
fr + any route → the single French inner (EdgeTTS fr-FR-HenriNeural).
Unknown lang (de, es, etc.) → falls back to English. The LLM still
respects soul.md but the voice stays English until we add more
locales — YAGNI for v1.
"""
from __future__ import annotations

from pipeline.dispatching_tts import DispatchingTTS


class _StubTTS:
    def __init__(self, label: str) -> None:
        self.voice_id = label

    def __repr__(self) -> str:
        return f"<StubTTS {self.voice_id}>"


def _make_dispatcher(fr_inner=None):
    inners = {
        "BANTER":    _StubTTS("en:austin"),
        "TASK":      _StubTTS("en:troy"),
        "REASONING": _StubTTS("en:troy"),
        "EMOTIONAL": _StubTTS("en:daniel"),
    }
    return DispatchingTTS(
        inners=inners,
        fallback=_StubTTS("en:fallback"),
        fr_inner=fr_inner,
    )


def test_en_picks_english_route_inner():
    d = _make_dispatcher()
    picked = d.pick("TASK", lang="en")
    assert picked.voice_id == "en:troy"


def test_en_default_lang_is_backward_compatible():
    """Existing callers passing only route= must still work — lang
    defaults to 'en'."""
    d = _make_dispatcher()
    picked = d.pick("TASK")
    assert picked.voice_id == "en:troy"


def test_fr_returns_fr_inner_regardless_of_route():
    fr = _StubTTS("fr:henri")
    d = _make_dispatcher(fr_inner=fr)
    for route in ["BANTER", "TASK", "REASONING", "EMOTIONAL"]:
        picked = d.pick(route, lang="fr")
        assert picked is fr, f"route={route} did not get fr_inner"


def test_fr_without_fr_inner_falls_back_to_english():
    """If build_dispatching_tts failed to construct fr_inner (e.g.,
    EdgeTTS import error), fr requests should not crash — fall back
    to the English route."""
    d = _make_dispatcher(fr_inner=None)
    picked = d.pick("TASK", lang="fr")
    assert picked.voice_id == "en:troy"


def test_unknown_lang_falls_back_to_english():
    fr = _StubTTS("fr:henri")
    d = _make_dispatcher(fr_inner=fr)
    picked = d.pick("TASK", lang="de")
    assert picked.voice_id == "en:troy"


def test_last_route_and_voice_id_updated():
    """Telemetry breadcrumbs the dispatcher exposes for the metrics
    span — must still be set on both en and fr paths."""
    fr = _StubTTS("fr:henri")
    d = _make_dispatcher(fr_inner=fr)
    d.pick("BANTER", lang="en")
    assert d.last_route == "BANTER"
    assert d.last_voice_id == "en:austin"
    d.pick("REASONING", lang="fr")
    assert d.last_route == "REASONING"
    assert d.last_voice_id == "fr:henri"


def test_build_dispatching_tts_constructs_fr_inner(monkeypatch, tmp_path):
    """build_dispatching_tts() should attach a French EdgeTTS instance
    as fr_inner. The voice defaults to fr-FR-HenriNeural; overridable
    via JARVIS_FR_EDGE_VOICE."""
    from providers.tts import build_dispatching_tts

    # Groq TTS constructor reads GROQ_API_KEY at __init__ time; set a
    # dummy value so the test doesn't fail before reaching the fr_inner block.
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    d = build_dispatching_tts()
    assert d.fr_inner is not None, (
        "build_dispatching_tts must construct a French EdgeTTS inner"
    )
    # The EdgeTTS instance's voice_id is set by build_dispatching_tts
    # to a `edge:fr-…` shape so the metrics span tells English/French
    # apart at a glance.
    vid = getattr(d.fr_inner, "voice_id", "")
    assert vid.startswith("edge:fr-"), (
        f"fr_inner voice_id should start with 'edge:fr-', got {vid!r}"
    )


def test_build_dispatching_tts_respects_fr_voice_env(monkeypatch):
    """Override the French voice via JARVIS_FR_EDGE_VOICE."""
    from providers.tts import build_dispatching_tts

    # Groq TTS constructor reads GROQ_API_KEY at __init__ time.
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("JARVIS_FR_EDGE_VOICE", "fr-FR-DeniseNeural")
    d = build_dispatching_tts()
    assert d.fr_inner is not None
    vid = getattr(d.fr_inner, "voice_id", "")
    assert "fr-FR-Den" in vid, (
        f"override voice should appear in voice_id, got {vid!r}"
    )

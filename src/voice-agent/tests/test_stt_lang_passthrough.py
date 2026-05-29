"""STT language unpinning + kill-switch env.

Default: language=None (Whisper / Deepgram auto-detect).
JARVIS_LANG_AUTODETECT=0 → language='en' (revert to pre-spec behavior
without a redeploy)."""
from __future__ import annotations

from providers import stt as stt_mod


def test_stt_language_default_is_none(monkeypatch):
    monkeypatch.delenv("JARVIS_LANG_AUTODETECT", raising=False)
    assert stt_mod._stt_language() is None


def test_stt_language_killswitch_pins_english(monkeypatch):
    monkeypatch.setenv("JARVIS_LANG_AUTODETECT", "0")
    assert stt_mod._stt_language() == "en"


def test_stt_language_killswitch_truthy_pins_english(monkeypatch):
    """Any non-zero truthy value also disables — common convention."""
    monkeypatch.setenv("JARVIS_LANG_AUTODETECT", "false")
    assert stt_mod._stt_language() == "en"
    monkeypatch.setenv("JARVIS_LANG_AUTODETECT", "off")
    assert stt_mod._stt_language() == "en"


def test_stt_language_explicit_one_enables_autodetect(monkeypatch):
    monkeypatch.setenv("JARVIS_LANG_AUTODETECT", "1")
    assert stt_mod._stt_language() is None


# ── Task 6: LangContext wiring via _update_lang_from_stt_event ─────────

import types


def _make_event(language, confidence=0.9, is_final=True, transcript="bonjour"):
    """Mimic a LiveKit user_input_transcribed event shape — duck-typed
    attribute access is what the handler uses."""
    return types.SimpleNamespace(
        language=language,
        confidence=confidence,
        is_final=is_final,
        transcript=transcript,
    )


def test_handler_updates_lang_context_on_high_confidence_french():
    """The STT result handler should call session.lang_ctx.set(lang,
    confidence) when language and confidence are present on the event."""
    from pipeline.lang_context import LangContext
    from jarvis_agent import _update_lang_from_stt_event

    ctx = LangContext()
    ev = _make_event(language="fr", confidence=0.92)
    _update_lang_from_stt_event(ctx, ev)
    assert ctx.get() == "fr"


def test_handler_no_op_when_language_missing():
    """STT plugins that don't surface a language field — handler must
    not crash; LangContext stays at its prior value."""
    from pipeline.lang_context import LangContext
    from jarvis_agent import _update_lang_from_stt_event

    ctx = LangContext()
    ev = _make_event(language=None, confidence=0.9)
    _update_lang_from_stt_event(ctx, ev)
    assert ctx.get() == "en"  # unchanged


def test_handler_no_op_below_confidence_floor():
    from pipeline.lang_context import LangContext
    from jarvis_agent import _update_lang_from_stt_event

    ctx = LangContext()
    ev = _make_event(language="fr", confidence=0.4)
    _update_lang_from_stt_event(ctx, ev)
    assert ctx.get() == "en"


def test_handler_no_op_when_confidence_missing():
    """Some events omit confidence; default to 1.0 (above floor) so
    the language still propagates."""
    from pipeline.lang_context import LangContext
    from jarvis_agent import _update_lang_from_stt_event

    ctx = LangContext()
    ev = types.SimpleNamespace(language="fr", is_final=True, transcript="bonjour")
    _update_lang_from_stt_event(ctx, ev)
    assert ctx.get() == "fr"

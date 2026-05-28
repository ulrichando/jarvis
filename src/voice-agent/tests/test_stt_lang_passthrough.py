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

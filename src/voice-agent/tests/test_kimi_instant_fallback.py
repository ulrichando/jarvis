"""Guards for the 2026-07-02 pin-fallback work.

Context: DeepSeek inference degraded to 18-28s TTFT while the account was
funded, and because the voice model was PINNED (JARVIS_PIN_ALL_ROUTES=1) with
no fallback, JARVIS went completely silent. Fix = a Kimi K2.6 Instant fallback
rung behind the pin. Two things must not regress:

1. kimi-k2.6-instant must build in *Instant* mode — thinking disabled + temp
   0.6. K2.6 defaults to thinking (temp locked to 1.0 → 400 otherwise, plus the
   builtin web_search break), so dropping the extra_body silently re-breaks it.
2. wrap_pin_fallback must only wrap when JARVIS_PIN_FALLBACK_MODEL names a
   DIFFERENT registered model, and otherwise return the primary untouched.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

_VOICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_kimi_thinking_toggle_constants():
    from providers.llm import _KIMI_INSTANT_EXTRA, _KIMI_THINKING_EXTRA

    assert _KIMI_INSTANT_EXTRA == {"thinking": {"type": "disabled"}}
    assert _KIMI_THINKING_EXTRA == {"thinking": {"type": "enabled"}}


def test_kimi_instant_ungated_builds_thinking_disabled_temp_06():
    """kimi-k2.6-instant is UNGATED (2026-07-02): it's the pin fallback rung, so it
    must register + build correctly WITHOUT JARVIS_KIMI_VOICE_EXPERIMENTAL (which
    also swaps Kimi into the dispatcher — an unwanted side effect). Fresh
    subprocess with the flag explicitly cleared + only a dummy key."""
    code = (
        "import os\n"
        "os.environ.pop('JARVIS_KIMI_VOICE_EXPERIMENTAL', None)\n"
        "os.environ['KIMI_API_KEY']='test-key-not-real'\n"
        "from providers.llm import SPEECH_MODELS\n"
        "assert 'kimi-k2.6-instant' in SPEECH_MODELS, 'instant not registered without the flag'\n"
        "assert 'kimi-k2.6-thinking' not in SPEECH_MODELS, 'thinking should stay gated'\n"
        "o = SPEECH_MODELS['kimi-k2.6-instant']['build']()._opts\n"
        "assert o.model == 'kimi-k2.6', o.model\n"
        "assert float(o.temperature) == 0.6, o.temperature\n"
        "assert o.extra_body == {'thinking': {'type': 'disabled'}}, o.extra_body\n"
        "print('OK')\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code], cwd=_VOICE_ROOT,
        capture_output=True, text=True,
    )
    assert "OK" in r.stdout, f"stdout={r.stdout!r} stderr={r.stderr[-800:]!r}"


def test_wrap_pin_fallback_noop_when_unset(monkeypatch):
    from providers import llm as m

    monkeypatch.delenv("JARVIS_PIN_FALLBACK_MODEL", raising=False)
    primary = object()
    assert m.wrap_pin_fallback(primary, "deepseek-v4-flash") is primary


def test_wrap_pin_fallback_noop_when_same_as_primary(monkeypatch):
    from providers import llm as m

    monkeypatch.setenv("JARVIS_PIN_FALLBACK_MODEL", "deepseek-v4-flash")
    primary = object()
    assert m.wrap_pin_fallback(primary, "deepseek-v4-flash") is primary


def test_wrap_pin_fallback_noop_when_unregistered(monkeypatch):
    from providers import llm as m

    monkeypatch.setenv("JARVIS_PIN_FALLBACK_MODEL", "no-such-model-xyz")
    primary = object()
    assert m.wrap_pin_fallback(primary, "deepseek-v4-flash") is primary


def test_wrap_pin_fallback_wraps_when_configured(monkeypatch):
    from providers import llm as m
    import livekit.agents.llm as lk_llm

    sentinel_fb = object()
    m.SPEECH_MODELS["_test_fb_model"] = {"label": "t", "build": lambda: sentinel_fb}

    captured = {}

    class _FakeFB:
        def __init__(self, llms, *, attempt_timeout=5.0, **kw):
            captured["llms"] = llms
            captured["timeout"] = attempt_timeout

    monkeypatch.setattr(lk_llm, "FallbackAdapter", _FakeFB)
    monkeypatch.setenv("JARVIS_PIN_FALLBACK_MODEL", "_test_fb_model")
    monkeypatch.setenv("JARVIS_PIN_FALLBACK_TIMEOUT", "6")

    primary = object()
    try:
        out = m.wrap_pin_fallback(primary, "deepseek-v4-flash")
        assert isinstance(out, _FakeFB)
        assert captured["llms"] == [primary, sentinel_fb]
        assert captured["timeout"] == 6.0
        assert out._jarvis_label == "deepseek-v4-flash"
    finally:
        m.SPEECH_MODELS.pop("_test_fb_model", None)

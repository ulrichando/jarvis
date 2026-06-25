"""Truthful STT/TTS provider labels + strict-local TTS (2026-06-22).

Covers the "stt/tts not local?" label bug: the legacy tray switcher reports a
cloud pick (``groq:troy``) while the AgentSession pipeline forces on-device
Kokoro via env flags, so ``/status`` lied (tray showed Orpheus while Kokoro
spoke, and had no STT label at all). These pin:

  - ``active_tts_provider`` / ``active_stt_engine`` label resolution, and
  - the new ``JARVIS_LOCAL_TTS_ONLY`` strict-local behaviour in
    ``build_dispatching_tts`` (drops Orpheus + Edge so TTS is 100% on-device).

No model loads / no network — same fast-unit discipline as
test_local_offline_fallback.py.
"""
import pytest

from voice_client_tray_config import active_stt_engine, active_tts_provider


def _clear(monkeypatch, names):
    for n in names:
        monkeypatch.delenv(n, raising=False)


# ── TTS label resolution ───────────────────────────────────────────────

_TTS_ENVS = (
    "JARVIS_LOCAL_TTS_PRIMARY", "JARVIS_LOCAL_TTS_ONLY",
    "JARVIS_LOCAL_TTS_ENGINE", "JARVIS_LOCAL_TTS_VOICE",
)


def test_tts_label_passthrough_without_local_override(monkeypatch):
    _clear(monkeypatch, _TTS_ENVS)
    # No local flag → report whatever the legacy tray file holds, unchanged.
    assert active_tts_provider("groq:troy") == "groq:troy"


def test_tts_label_kokoro_when_primary_default(monkeypatch):
    # Local-first DEFAULT: with no explicit engine pick (empty spec),
    # LOCAL_TTS_PRIMARY reports the on-device engine/voice.
    _clear(monkeypatch, _TTS_ENVS)
    monkeypatch.setenv("JARVIS_LOCAL_TTS_PRIMARY", "1")
    monkeypatch.setenv("JARVIS_LOCAL_TTS_ENGINE", "kokoro")
    monkeypatch.setenv("JARVIS_LOCAL_TTS_VOICE", "af_heart")
    assert active_tts_provider("") == "kokoro:af_heart"


def test_tts_label_honors_explicit_online_pick(monkeypatch):
    # Spec is AUTHORITATIVE (2026-06-25): an explicit online pick is honored
    # even under LOCAL_TTS_PRIMARY — build_tts_chain runs that engine, so the
    # /status label must reflect it instead of always reporting Kokoro.
    _clear(monkeypatch, _TTS_ENVS)
    monkeypatch.setenv("JARVIS_LOCAL_TTS_PRIMARY", "1")
    monkeypatch.setenv("JARVIS_LOCAL_TTS_ENGINE", "kokoro")
    assert active_tts_provider("groq:troy") == "groq:troy"
    assert active_tts_provider("edge:en-US-AriaNeural") == "edge:en-US-AriaNeural"


def test_tts_label_kokoro_defaults_when_only(monkeypatch):
    _clear(monkeypatch, _TTS_ENVS)
    monkeypatch.setenv("JARVIS_LOCAL_TTS_ONLY", "1")  # engine/voice default
    assert active_tts_provider("groq:austin") == "kokoro:af_heart"


def test_tts_label_piper_engine_default(monkeypatch):
    # Piper as the local-first default (empty spec → on-device).
    _clear(monkeypatch, _TTS_ENVS)
    monkeypatch.setenv("JARVIS_LOCAL_TTS_PRIMARY", "1")
    monkeypatch.setenv("JARVIS_LOCAL_TTS_ENGINE", "piper")
    assert active_tts_provider("") == "piper:local"


def test_tts_label_only_forces_local_over_pick(monkeypatch):
    # Strict-local (JARVIS_LOCAL_TTS_ONLY) overrides even an explicit online pick.
    _clear(monkeypatch, _TTS_ENVS)
    monkeypatch.setenv("JARVIS_LOCAL_TTS_ONLY", "1")
    monkeypatch.setenv("JARVIS_LOCAL_TTS_ENGINE", "piper")
    assert active_tts_provider("groq:troy") == "piper:local"


# ── STT engine label (distinct from the reply-LLM speech_model) ─────────

_STT_ENVS = (
    "JARVIS_LOCAL_STT_PRIMARY", "JARVIS_STT_LOCAL_ONLY",
    "JARVIS_LOCAL_STT_MODEL", "JARVIS_DEEPGRAM_DISABLED", "DEEPGRAM_API_KEY",
)


def test_stt_label_local_when_primary(monkeypatch):
    _clear(monkeypatch, _STT_ENVS)
    monkeypatch.setenv("JARVIS_LOCAL_STT_PRIMARY", "1")
    monkeypatch.setenv("JARVIS_LOCAL_STT_MODEL", "large-v3-turbo")
    # Local label uses the familiar "whisper-…" spelling matching the Groq id.
    assert active_stt_engine() == "whisper-large-v3-turbo (local)"


def test_stt_label_local_when_only(monkeypatch):
    _clear(monkeypatch, _STT_ENVS)
    monkeypatch.setenv("JARVIS_STT_LOCAL_ONLY", "1")
    eng = active_stt_engine()
    assert eng.startswith("whisper-") and "(local)" in eng


def test_stt_label_does_not_double_prefix(monkeypatch):
    """If someone sets the model id WITH the family prefix already, don't
    produce 'whisper-whisper-…'."""
    _clear(monkeypatch, _STT_ENVS)
    monkeypatch.setenv("JARVIS_STT_LOCAL_ONLY", "1")
    monkeypatch.setenv("JARVIS_LOCAL_STT_MODEL", "whisper-large-v3-turbo")
    assert active_stt_engine() == "whisper-large-v3-turbo (local)"


def test_stt_label_groq_when_deepgram_disabled(monkeypatch):
    _clear(monkeypatch, _STT_ENVS)
    monkeypatch.setenv("JARVIS_DEEPGRAM_DISABLED", "1")
    assert active_stt_engine() == "groq:whisper-large-v3-turbo"


def test_stt_label_deepgram_when_key_present(monkeypatch):
    _clear(monkeypatch, _STT_ENVS)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dummy")
    assert active_stt_engine() == "deepgram:nova-3"


# ── strict-local TTS dispatcher (JARVIS_LOCAL_TTS_ONLY) ─────────────────

@pytest.fixture
def fake_piper_model(tmp_path, monkeypatch):
    """Stub .onnx that exists (PiperTTS loads lazily — never read here)."""
    onnx = tmp_path / "voice.onnx"
    onnx.write_bytes(b"\x00")
    monkeypatch.setenv("JARVIS_LOCAL_TTS_MODEL_PATH", str(onnx))
    return onnx


def _set_local_tts(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-dummy")  # Orpheus constructs, then dropped
    monkeypatch.setenv("JARVIS_LOCAL_TTS_ENABLED", "1")
    monkeypatch.setenv("JARVIS_LOCAL_TTS_ENGINE", "piper")  # in-process, no server
    monkeypatch.delenv("JARVIS_LOCAL_TTS_PRIMARY", raising=False)


def test_tts_local_only_drops_cloud_rungs(monkeypatch, fake_piper_model):
    _set_local_tts(monkeypatch)
    monkeypatch.setenv("JARVIS_LOCAL_TTS_ONLY", "1")
    from livekit.agents import tts as lk_tts
    from providers.tts import build_dispatching_tts
    disp = build_dispatching_tts()
    for route in ("BANTER", "TASK", "REASONING", "EMOTIONAL"):
        inner = disp.inners[route]
        assert getattr(inner, "voice_id", "").endswith(":local"), route
        # No FallbackAdapter ⇒ no Orpheus/Edge cloud rung behind it.
        assert not isinstance(inner, lk_tts.FallbackAdapter), route
    # French Edge-TTS is Microsoft cloud — dropped under strict-local.
    assert disp.fr_inner is None


def test_tts_keeps_cloud_fallback_without_local_only(monkeypatch, fake_piper_model):
    _set_local_tts(monkeypatch)
    monkeypatch.delenv("JARVIS_LOCAL_TTS_ONLY", raising=False)
    from livekit.agents import tts as lk_tts
    from providers.tts import build_dispatching_tts
    disp = build_dispatching_tts()
    # Control: without local-only the route is a FallbackAdapter (Orpheus → Edge
    # → local), proving the strict-local test above is actually doing something.
    assert isinstance(disp.inners["TASK"], lk_tts.FallbackAdapter)

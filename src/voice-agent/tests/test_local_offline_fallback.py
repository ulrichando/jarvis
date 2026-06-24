"""Unit tests for the local offline fallback stack (LLM / STT / TTS / Vision).

Covers the GATING + WIRING of the rungs added by the 2026-06-15 local-LLM
design (~/.claude/plans/we-need-to-find-polymorphic-allen.md) — not live
model behavior (that's proven by the live soak / round-trip checks). All
tests avoid the heavy model loads + network so the suite stays fast:

  - LLM rung-0 is inspected via the per-route chain `_jarvis_label`.
  - STT/TTS rung builders are checked for gating; the model files are
    never actually loaded (lazy) — TTS uses a stub .onnx path.
  - Vision dispatch mocks the Ollama call.
"""
import pytest


# ─────────────────────────────────────────────────────────────────────
# LLM rung-0
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def cloud_keys(monkeypatch):
    """Dummy Groq key so cloud primaries construct (no network call at
    construction); no Anthropic/DeepSeek so the resolved chain is
    deterministic."""
    monkeypatch.setenv("GROQ_API_KEY", "test-dummy")
    for k in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("JARVIS_LOCAL_LLM_ASSUME_AVAILABLE", raising=False)


def _route_label(disp, route):
    return getattr(disp.inners.get(route), "_jarvis_label", "") or ""


def test_llm_local_rung_off_by_default(cloud_keys, monkeypatch):
    monkeypatch.delenv("JARVIS_LOCAL_LLM_ENABLED", raising=False)
    from providers.llm import build_dispatching_llm
    disp = build_dispatching_llm()
    assert not _route_label(disp, "BANTER").startswith("local:")


def test_llm_local_rung_on_all_routes(cloud_keys, monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL_LLM_ENABLED", "1")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_ASSUME_AVAILABLE", "1")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "qwen3:14b")
    monkeypatch.delenv("JARVIS_LOCAL_LLM_ROUTES", raising=False)
    from providers.llm import build_dispatching_llm
    disp = build_dispatching_llm()
    for r in ("BANTER", "REASONING", "EMOTIONAL", "TASK_CODE",
              "TASK_DESKTOP", "TASK_FILES", "TASK_OTHER"):
        assert _route_label(disp, r) == "local:qwen3:14b", r


def test_llm_local_rung_route_filter(cloud_keys, monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL_LLM_ENABLED", "1")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_ASSUME_AVAILABLE", "1")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "m")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_ROUTES", "REASONING")
    from providers.llm import build_dispatching_llm
    disp = build_dispatching_llm()
    assert _route_label(disp, "REASONING").startswith("local:")
    assert not _route_label(disp, "BANTER").startswith("local:")


def test_llm_local_only_when_no_cloud_keys(monkeypatch):
    """The plan's headline: stay alive when ALL cloud is unavailable.
    With zero cloud keys + local enabled, local becomes the primary."""
    for k in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("JARVIS_LOCAL_LLM_ENABLED", "1")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_ASSUME_AVAILABLE", "1")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "solo")
    from providers.llm import build_dispatching_llm
    disp = build_dispatching_llm()
    assert _route_label(disp, "BANTER") == "local:solo"


def test_llm_local_rung_skipped_when_unavailable(cloud_keys, monkeypatch):
    """Stale local env must not make telemetry claim local when no server/model exists."""
    monkeypatch.setenv("JARVIS_LOCAL_LLM_ENABLED", "1")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "qwen3:14b")
    monkeypatch.delenv("JARVIS_LOCAL_LLM_ASSUME_AVAILABLE", raising=False)

    import providers.llm as llm
    monkeypatch.setattr(llm, "_probe_local_llm", lambda *args, **kw: (False, "down"))

    disp = llm.build_dispatching_llm()
    assert not _route_label(disp, "BANTER").startswith("local:")


def test_llm_tray_entries_present_and_build(monkeypatch):
    from providers.llm import SPEECH_MODELS
    assert "ollama/llama3.1:8b" in SPEECH_MODELS
    assert "ollama/qwen3:14b" in SPEECH_MODELS
    # build() must not raise (no network at construction).
    llm = SPEECH_MODELS["ollama/llama3.1:8b"]["build"]()
    assert llm is not None


# ─────────────────────────────────────────────────────────────────────
# STT rung (faster-whisper)
# ─────────────────────────────────────────────────────────────────────

def test_stt_local_off_by_default(monkeypatch):
    monkeypatch.delenv("JARVIS_LOCAL_STT_ENABLED", raising=False)
    from providers.faster_whisper_stt import build_local_stt
    assert build_local_stt() is None


def test_stt_local_on_builds_adapter(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL_STT_ENABLED", "1")
    monkeypatch.setenv("JARVIS_LOCAL_STT_MODEL", "base")
    from providers.faster_whisper_stt import build_local_stt, FasterWhisperSTT
    s = build_local_stt()
    assert isinstance(s, FasterWhisperSTT)
    assert s.label == "local:faster-whisper/base"
    # Capabilities: non-streaming (the chain's StreamAdapter wraps it).
    assert s.capabilities.streaming is False


def test_stt_chain_appends_local_when_enabled(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "dummy")
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    from livekit.agents.stt import FallbackAdapter
    try:
        from livekit.plugins import silero
        vad = silero.VAD.load()
    except Exception:
        pytest.skip("Silero VAD unavailable for chain test")
    from providers.stt import build_stt_chain

    monkeypatch.setenv("JARVIS_LOCAL_STT_ENABLED", "0")
    off = build_stt_chain(vad=vad)
    # Deepgram absent + local off → single Groq Whisper rung, returned bare.
    assert not isinstance(off, FallbackAdapter)

    monkeypatch.setenv("JARVIS_LOCAL_STT_ENABLED", "1")
    monkeypatch.setenv("JARVIS_LOCAL_STT_MODEL", "base")
    on = build_stt_chain(vad=vad)
    assert isinstance(on, FallbackAdapter)
    assert len(on._stt_instances) == 2  # Groq Whisper + local faster-whisper


# ─────────────────────────────────────────────────────────────────────
# TTS rung (Piper)
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_piper_model(tmp_path, monkeypatch):
    """A stub .onnx path that exists (PiperTTS loads the voice lazily, so
    the file content is never read at construction)."""
    onnx = tmp_path / "voice.onnx"
    onnx.write_bytes(b"\x00")  # not a real model — never loaded in unit tests
    monkeypatch.setenv("JARVIS_LOCAL_TTS_MODEL_PATH", str(onnx))
    return onnx


def test_tts_local_off_by_default(monkeypatch):
    monkeypatch.delenv("JARVIS_LOCAL_TTS_ENABLED", raising=False)
    from providers.piper_tts import build_local_tts
    assert build_local_tts() is None


def test_tts_local_on_missing_model_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_LOCAL_TTS_ENABLED", "1")
    monkeypatch.setenv("JARVIS_LOCAL_TTS_MODEL_PATH", str(tmp_path / "nope.onnx"))
    from providers.piper_tts import build_local_tts
    assert build_local_tts() is None


def test_tts_local_on_builds_adapter(monkeypatch, fake_piper_model):
    monkeypatch.setenv("JARVIS_LOCAL_TTS_ENABLED", "1")
    from providers.piper_tts import build_local_tts, PiperTTS
    t = build_local_tts()
    assert isinstance(t, PiperTTS)
    assert t.model.startswith("piper:")
    assert t.capabilities.streaming is False


def test_tts_unknown_engine_returns_none(monkeypatch, fake_piper_model):
    monkeypatch.setenv("JARVIS_LOCAL_TTS_ENABLED", "1")
    monkeypatch.setenv("JARVIS_LOCAL_TTS_ENGINE", "festival")  # genuinely unsupported
    from providers.piper_tts import build_local_tts
    assert build_local_tts() is None


def test_tts_kokoro_engine_builds_endpoint_adapter(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL_TTS_ENABLED", "1")
    monkeypatch.setenv("JARVIS_LOCAL_TTS_ENGINE", "kokoro")
    monkeypatch.setenv("JARVIS_LOCAL_TTS_URL", "http://127.0.0.1:8880/v1")
    monkeypatch.setenv("JARVIS_LOCAL_TTS_VOICE", "af_bella")
    from providers.piper_tts import build_local_tts
    from providers.kokoro_tts import KokoroEndpointTTS
    t = build_local_tts()
    assert isinstance(t, KokoroEndpointTTS)
    assert t.model == "kokoro:af_bella"
    assert t.provider == "kokoro-local"
    assert t.capabilities.streaming is False


def test_tts_kokoro_needs_no_local_model_file(monkeypatch):
    """Kokoro is endpoint-based — it must NOT require a local .onnx (unlike
    Piper), so it builds even with no JARVIS_LOCAL_TTS_MODEL_PATH set."""
    monkeypatch.setenv("JARVIS_LOCAL_TTS_ENABLED", "1")
    monkeypatch.setenv("JARVIS_LOCAL_TTS_ENGINE", "kokoro")
    monkeypatch.delenv("JARVIS_LOCAL_TTS_MODEL_PATH", raising=False)
    from providers.piper_tts import build_local_tts
    from providers.kokoro_tts import KokoroEndpointTTS
    assert isinstance(build_local_tts(), KokoroEndpointTTS)


def test_tts_chain_appends_piper_when_enabled(monkeypatch, fake_piper_model, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "dummy")
    from providers.tts import build_tts_chain
    from providers.piper_tts import PiperTTS
    provider_file = tmp_path / "tts-provider"  # absent → default Orpheus

    monkeypatch.setenv("JARVIS_LOCAL_TTS_ENABLED", "0")
    off = build_tts_chain(provider_file)
    assert not any(isinstance(x, PiperTTS) for x in off)

    monkeypatch.setenv("JARVIS_LOCAL_TTS_ENABLED", "1")
    on = build_tts_chain(provider_file)
    assert isinstance(on[-1], PiperTTS)  # appended LAST


# ─────────────────────────────────────────────────────────────────────
# Vision rung (Ollama)
# ─────────────────────────────────────────────────────────────────────

def test_vision_available_gate(monkeypatch):
    from vision import ollama_vision
    monkeypatch.delenv("JARVIS_LOCAL_VISION_ENABLED", raising=False)
    assert ollama_vision.ollama_vision_available() is False
    monkeypatch.setenv("JARVIS_LOCAL_VISION_ENABLED", "1")
    assert ollama_vision.ollama_vision_available() is True


@pytest.mark.parametrize("env_url,expected", [
    ("http://127.0.0.1:11434", "http://127.0.0.1:11434/v1"),
    ("http://127.0.0.1:11434/", "http://127.0.0.1:11434/v1"),
    ("http://host:11434/v1", "http://host:11434/v1"),
    ("", "http://127.0.0.1:11434/v1"),
])
def test_vision_base_url_normalization(monkeypatch, env_url, expected):
    from vision import ollama_vision
    monkeypatch.setenv("JARVIS_OLLAMA_URL", env_url)
    assert ollama_vision._base_url() == expected


def test_webcam_dispatch_offline_uses_local(monkeypatch):
    """No Anthropic key + local vision on → routes to the Ollama helper
    and reports the local model label."""
    import tools.webcam as wc
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("JARVIS_LOCAL_VISION_ENABLED", "1")
    monkeypatch.setenv("JARVIS_OLLAMA_VISION_MODEL", "moondream")
    monkeypatch.setattr(wc.ollama_vision, "analyze_jpeg",
                        lambda jpeg, q, system=None: "a local description")
    answer, model = wc._analyze_jpeg(b"JPEG", "what is this?")
    assert answer == "a local description"
    assert model == "ollama:moondream"


def test_webcam_falls_back_to_local_when_anthropic_errors(monkeypatch):
    import tools.webcam as wc
    monkeypatch.setenv("ANTHROPIC_API_KEY", "present")
    monkeypatch.setenv("JARVIS_LOCAL_VISION_ENABLED", "1")
    monkeypatch.setenv("JARVIS_OLLAMA_VISION_MODEL", "llava")

    def boom(jpeg, q):
        raise RuntimeError("anthropic vision down")
    monkeypatch.setattr(wc, "_analyze_jpeg_anthropic", boom)
    monkeypatch.setattr(wc.ollama_vision, "analyze_jpeg",
                        lambda jpeg, q, system=None: "local saved the day")
    answer, model = wc._analyze_jpeg(b"JPEG", "q")
    assert answer == "local saved the day"
    assert model == "ollama:llava"


# ─────────────────────────────────────────────────────────────────────
# VRAM-aware model picker (Odysseus-inspired)
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("vram,ram,expected", [
    (192.0, 256.0, "qwen3:235b-a22b"),
    (80.0,  128.0, "llama3.3:70b"),
    (48.0,  128.0, "llama3.3:70b"),
    (24.0,   64.0, "qwen3:32b"),
    (16.0,   64.0, "qwen3:14b"),
    (12.0,   32.0, "qwen3:14b"),
    (8.0,    32.0, "llama3.1:8b"),
    (6.0,    62.0, "llama3.1:8b"),
    (4.0,    16.0, "llama3.2:3b"),
])
def test_hwfit_vram_tiers(vram, ram, expected):
    """Real Ollama tags, quant-aware fit. Bigger VRAM → bigger model."""
    from providers.local_model_picker import pick
    assert pick(vram, ram)[0] == expected


def test_hwfit_tags_are_real_ollama_tags():
    """Guard against the qwen3:72b bug — every catalog tag must be a real
    Ollama model family (no invented sizes)."""
    from providers.local_model_picker import _MODELS
    real = {"llama3.1:405b", "qwen3:235b-a22b", "qwen2.5:72b", "llama3.3:70b",
            "qwen3:32b", "qwen3:14b", "llama3.1:8b", "qwen2.5:7b", "llama3.2:3b"}
    assert {m[0] for m in _MODELS} <= real


def test_hwfit_prefers_vram_fit_over_heavy_offload():
    """8GB GPU + 256GB RAM: a VRAM-resident 8B must beat a heavily-offloaded
    70B — voice is latency-sensitive, so the offload penalty keeps us fast."""
    from providers.local_model_picker import pick
    assert pick(8.0, 256.0)[0] == "llama3.1:8b"


def test_hwfit_analyze_reports_runmode_and_headroom():
    from providers.local_model_picker import analyze
    top = analyze(80.0, 128.0)[0]
    assert top.run_mode == "gpu" and top.offload_frac == 0.0
    assert top.headroom_quant in ("Q8_0", "Q6_K", "Q5_K_M", "Q4_K_M")
    assert top.footprint_gb > 0


def test_picker_resolve_tag(monkeypatch):
    from providers import local_model_picker as p
    # Explicit tag passes through untouched.
    assert p.resolve_model_tag("qwen3:14b") == "qwen3:14b"
    assert p.resolve_model_tag("llama3.1:8b") == "llama3.1:8b"
    # 'auto' (any case) resolves to a catalog tag.
    monkeypatch.setattr(p, "detect_vram_gb", lambda: 6.0)
    monkeypatch.setattr(p, "detect_ram_gb", lambda: 62.0)
    assert p.resolve_model_tag("auto") == "llama3.1:8b"
    assert p.resolve_model_tag("AUTO") == "llama3.1:8b"


def test_picker_detect_never_raises():
    """Detection must degrade to None, never crash boot."""
    from providers.local_model_picker import detect_vram_gb, detect_ram_gb
    detect_vram_gb()   # may return a float or None depending on host
    detect_ram_gb()


def test_picker_auto_tray_entry_present():
    from providers.llm import SPEECH_MODELS
    assert "ollama/auto" in SPEECH_MODELS


def test_webcam_gate_allows_local_only(monkeypatch):
    """check_webcam_requirements passes with no Anthropic key when local
    vision is enabled (and the camera is reachable)."""
    import tools.webcam as wc
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_WEBCAM_DISABLED", raising=False)
    monkeypatch.setenv("JARVIS_LOCAL_VISION_ENABLED", "1")
    monkeypatch.setattr(wc, "webcam_available", lambda: True)
    assert wc.check_webcam_requirements() is True
    # ...and is blocked when neither backend is available.
    monkeypatch.setenv("JARVIS_LOCAL_VISION_ENABLED", "0")
    assert wc.check_webcam_requirements() is False

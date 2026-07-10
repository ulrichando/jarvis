"""Local mode pins the speech SUPERVISOR to the on-device model (2026-07-10).

The bug: ~/.jarvis/voice-mode=local only set JARVIS_LOCAL_LLM_ENABLED=1.
With JARVIS_PIN_ALL_ROUTES=1 and a cloud model pinned in
~/.jarvis/voice-model, the dispatcher (and its local rung-0 prepend) never
ran — the pinned CLOUD model stayed the brain and the live log showed
`speech LLM: claude-haiku-4-5` in local mode. The fix makes
read_speech_model() voice-mode-aware: local mode returns
`ollama/<resolved-installed-tag>` (auto-registered into SPEECH_MODELS for
tags outside the fixed catalog, e.g. qwen2.5:7b) WITHOUT rewriting the
voice-model file, so flipping back to cloud restores the old pin exactly.

Hermetic: SPEECH_MODEL_FILE / VOICE_MODE_FILE are patched to tmp_path and
discovery is monkeypatched — no real ~/.jarvis reads, no `ollama list`.
"""
import pytest

import providers.llm as llm_mod
from providers import local_model_picker


@pytest.fixture
def files(tmp_path, monkeypatch):
    """Redirect the two ~/.jarvis flat files + clear the model env."""
    vmodel = tmp_path / "voice-model"
    vmode = tmp_path / "voice-mode"
    monkeypatch.setattr(llm_mod, "SPEECH_MODEL_FILE", vmodel)
    monkeypatch.setattr(llm_mod, "VOICE_MODE_FILE", vmode)
    monkeypatch.delenv("JARVIS_LOCAL_LLM_MODEL", raising=False)
    # Never let the fallback discovery path shell out to real ollama.
    monkeypatch.setattr(
        local_model_picker, "resolve_installed_model_tag", lambda preferred="": None
    )
    return vmodel, vmode


def _write(p, text):
    p.write_text(text + "\n", encoding="utf-8")


# ── Cloud modes: byte-identical behavior ─────────────────────────────

def test_cloud_mode_returns_file_pin(files):
    vmodel, vmode = files
    _write(vmodel, "deepseek-chat-v3")
    _write(vmode, "cloud")
    assert llm_mod.read_speech_model() == "deepseek-chat-v3"


def test_absent_mode_file_returns_file_pin(files):
    vmodel, _ = files
    _write(vmodel, "deepseek-chat-v3")
    assert llm_mod.read_speech_model() == "deepseek-chat-v3"


def test_cloud_mode_unknown_model_still_falls_back_to_default(files):
    vmodel, vmode = files
    _write(vmodel, "not-a-real-model")
    _write(vmode, "cloud")
    assert llm_mod.read_speech_model() == llm_mod.DEFAULT_SPEECH_MODEL


# ── Local mode: the supervisor IS the local model ────────────────────

def test_local_mode_overrides_cloud_pin(files, monkeypatch):
    """THE regression: cloud pin + voice-mode=local must yield the ollama id,
    not the pinned cloud model."""
    vmodel, vmode = files
    _write(vmodel, "claude-haiku-4-5")
    _write(vmode, "local")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "qwen2.5:7b")
    assert llm_mod.read_speech_model() == "ollama/qwen2.5:7b"


def test_local_mode_does_not_rewrite_voice_model_file(files, monkeypatch):
    # deepseek-chat-v3: an UNCONDITIONAL registry entry (the claude-* ids are
    # gated on the anthropic plugin/key, absent in the hermetic test env).
    vmodel, vmode = files
    _write(vmodel, "deepseek-chat-v3")
    _write(vmode, "local")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "qwen2.5:7b")
    assert llm_mod.read_speech_model() == "ollama/qwen2.5:7b"
    # Flipping back to cloud must restore the old pin exactly.
    assert vmodel.read_text().strip() == "deepseek-chat-v3"
    _write(vmode, "cloud")
    assert llm_mod.read_speech_model() == "deepseek-chat-v3"


def test_local_mode_auto_registers_uncataloged_tag(files, monkeypatch):
    """qwen2.5:7b has no static SPEECH_MODELS entry — read_speech_model must
    register one (else it warns 'unknown speech model' and falls back to the
    cloud default, which was the observable symptom)."""
    vmodel, vmode = files
    _write(vmodel, "claude-haiku-4-5")
    _write(vmode, "local")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "qwen2.5:7b")
    name = llm_mod.read_speech_model()
    assert name in llm_mod.SPEECH_MODELS
    inst = llm_mod.SPEECH_MODELS[name]["build"]()
    assert inst.model == "qwen2.5:7b"
    # MANDATORY for local ollama servers — strict schema silently breaks
    # JARVIS's 20+ tools.
    assert inst._strict_tool_schema is False
    # Rung-0 label convention so telemetry + failover wraps recognize local.
    assert inst._jarvis_label == "local:qwen2.5:7b"


def test_local_mode_respects_explicit_ollama_pin(files, monkeypatch):
    """A user-picked ollama/* pin in voice-model wins over env resolution."""
    vmodel, vmode = files
    _write(vmodel, "ollama/llama3.1:8b")
    _write(vmode, "local")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "qwen2.5:7b")
    assert llm_mod.read_speech_model() == "ollama/llama3.1:8b"


def test_local_mode_env_auto_falls_back_to_discovery(files, monkeypatch):
    vmodel, vmode = files
    _write(vmodel, "claude-haiku-4-5")
    _write(vmode, "local")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "auto")
    monkeypatch.setattr(
        local_model_picker,
        "resolve_installed_model_tag",
        lambda preferred="": "llama3.1:8b",
    )
    assert llm_mod.read_speech_model() == "ollama/llama3.1:8b"


def test_local_mode_nothing_resolvable_keeps_cloud_pin(files, monkeypatch):
    """No env model + empty discovery → keep the cloud pin (degrade to the
    cloud brain rather than pinning a dead endpoint and going mute)."""
    vmodel, vmode = files
    _write(vmodel, "deepseek-chat-v3")
    _write(vmode, "local")
    # fixture already stubs discovery to None + clears the env
    assert llm_mod.read_speech_model() == "deepseek-chat-v3"


# ── Registration helper edge cases ───────────────────────────────────

def test_register_rejects_non_ollama_ids():
    assert llm_mod._register_ollama_speech_model("claude-haiku-4-5") is False
    assert "claude-haiku-4-5-bogus" not in llm_mod.SPEECH_MODELS
    assert llm_mod._register_ollama_speech_model("ollama/") is False
    assert "ollama/" not in llm_mod.SPEECH_MODELS


def test_register_is_idempotent_and_keeps_static_entries():
    before = llm_mod.SPEECH_MODELS["ollama/llama3.1:8b"]
    assert llm_mod._register_ollama_speech_model("ollama/llama3.1:8b") is True
    assert llm_mod.SPEECH_MODELS["ollama/llama3.1:8b"] is before


def test_make_local_speech_llm_profile():
    inst = llm_mod._make_local_speech_llm("some:tag")
    assert inst.model == "some:tag"
    assert inst._strict_tool_schema is False
    assert inst._jarvis_label == "local:some:tag"

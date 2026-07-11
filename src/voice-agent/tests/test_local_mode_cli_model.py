"""Local mode pins the CLI/TOOL model to the on-device Ollama model (2026-07-11).

The bug: the 2026-07-10 fix pinned the speech SUPERVISOR to the local model
in voice-mode=local, but every `run_jarvis_cli` tool call still used the
CLOUD pin in ~/.jarvis/cli-model (live: claude-sonnet-4-6) — CLI_MODELS had
no ollama entry and read_cli_model() wasn't voice-mode-aware, so "local
mode" tool calls ran on a cloud model through the proxy. The fix mirrors
the speech-side shape exactly:

  - `_register_ollama_cli_model` additively registers `ollama:<tag>`
    CLI_MODELS entries (id form = the CLI's jarvisModelRegistry.ts
    dynamic-discovery ids, which the proxy resolves STATELESSLY);
  - `read_cli_model()` returns `ollama:<resolved installed tag>` when
    voice-mode=local WITHOUT rewriting the cli-model file;
  - `_clean_env_for_cli()` routes the ollama pick to the LOCAL Ollama
    daemon (ANTHROPIC_MODEL=ollama:<tag> + OLLAMA_MODEL/OLLAMA_BASE_URL)
    while keeping the §P0-SEC-8 secret stripping byte-identical.

Hermetic: CLI_MODEL_FILE / VOICE_MODE_FILE are patched to tmp_path and
discovery is monkeypatched — no real ~/.jarvis reads, no `ollama list`.
"""
import pytest

import jarvis_agent as ja
import providers.llm as llm_mod
import voice_client_tray_config as vtc
from providers import local_model_picker


@pytest.fixture
def files(tmp_path, monkeypatch):
    """Redirect the two ~/.jarvis flat files + clear the model env."""
    cmodel = tmp_path / "cli-model"
    vmode = tmp_path / "voice-mode"
    monkeypatch.setattr(ja, "CLI_MODEL_FILE", cmodel)
    # jarvis_agent._local_mode_cli_model reads providers.llm.VOICE_MODE_FILE
    # dynamically — one monkeypatch covers the speech AND the CLI reader.
    monkeypatch.setattr(llm_mod, "VOICE_MODE_FILE", vmode)
    monkeypatch.delenv("JARVIS_LOCAL_LLM_MODEL", raising=False)
    monkeypatch.delenv("JARVIS_LOCAL_LLM_URL", raising=False)
    # Never let the fallback discovery path shell out to real ollama.
    monkeypatch.setattr(
        local_model_picker, "resolve_installed_model_tag", lambda preferred="": None
    )
    return cmodel, vmode


def _write(p, text):
    p.write_text(text + "\n", encoding="utf-8")


# ── Cloud modes: byte-identical behavior ─────────────────────────────

def test_cloud_mode_returns_file_pin(files):
    cmodel, vmode = files
    _write(cmodel, "claude-sonnet-4-6")
    _write(vmode, "cloud")
    assert ja.read_cli_model() == "claude-sonnet-4-6"


def test_absent_mode_file_returns_file_pin(files):
    cmodel, _ = files
    _write(cmodel, "deepseek-v4-pro")
    assert ja.read_cli_model() == "deepseek-v4-pro"


def test_cloud_mode_unknown_model_still_falls_back_to_default(files):
    cmodel, vmode = files
    _write(cmodel, "not-a-real-model")
    _write(vmode, "cloud")
    assert ja.read_cli_model() == ja.DEFAULT_CLI_MODEL


# ── Local mode: tool calls run the local model ───────────────────────

def test_local_mode_overrides_cloud_pin(files, monkeypatch):
    """THE regression: cloud pin + voice-mode=local must yield the ollama
    id, not the pinned cloud model (tool calls were running Sonnet)."""
    cmodel, vmode = files
    _write(cmodel, "claude-sonnet-4-6")
    _write(vmode, "local")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "qwen2.5:7b")
    assert ja.read_cli_model() == "ollama:qwen2.5:7b"
    # Registered so run_jarvis_cli's CLI_MODELS[...] lookups resolve.
    assert ja.CLI_MODELS["ollama:qwen2.5:7b"] == {
        "provider": "ollama",
        "model":    "qwen2.5:7b",
        "label":    "Local · qwen2.5:7b (Ollama)",
    }


def test_local_mode_does_not_rewrite_cli_model_file(files, monkeypatch):
    cmodel, vmode = files
    _write(cmodel, "deepseek-v4-pro")
    _write(vmode, "local")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "qwen2.5:7b")
    assert ja.read_cli_model() == "ollama:qwen2.5:7b"
    # Flipping back to cloud must restore the old pin exactly.
    assert cmodel.read_text().strip() == "deepseek-v4-pro"
    _write(vmode, "cloud")
    assert ja.read_cli_model() == "deepseek-v4-pro"


def test_local_mode_env_auto_falls_back_to_discovery(files, monkeypatch):
    cmodel, vmode = files
    _write(cmodel, "claude-sonnet-4-6")
    _write(vmode, "local")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "auto")
    monkeypatch.setattr(
        local_model_picker,
        "resolve_installed_model_tag",
        lambda preferred="": "llama3.1:8b",
    )
    assert ja.read_cli_model() == "ollama:llama3.1:8b"


def test_local_mode_nothing_resolvable_keeps_cloud_pin(files):
    """No env model + empty discovery → keep the cloud pin (degrade to a
    working cloud tool path rather than pointing at a dead endpoint)."""
    cmodel, vmode = files
    _write(cmodel, "deepseek-v4-pro")
    _write(vmode, "local")
    # fixture already stubs discovery to None + clears the env
    assert ja.read_cli_model() == "deepseek-v4-pro"


def test_local_mode_respects_explicit_ollama_pin(files, monkeypatch):
    """A user-picked ollama:* pin in cli-model wins over env resolution."""
    cmodel, vmode = files
    _write(cmodel, "ollama:llama3.1:8b")
    _write(vmode, "local")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "qwen2.5:7b")
    assert ja.read_cli_model() == "ollama:llama3.1:8b"


# ── Registration helper edge cases ───────────────────────────────────

def test_register_rejects_non_ollama_ids():
    assert ja._register_ollama_cli_model("claude-sonnet-4-6") is False
    assert ja._register_ollama_cli_model("ollama:") is False
    assert "ollama:" not in ja.CLI_MODELS


def test_register_is_idempotent():
    assert ja._register_ollama_cli_model("ollama:some:tag") is True
    first = ja.CLI_MODELS["ollama:some:tag"]
    assert ja._register_ollama_cli_model("ollama:some:tag") is True
    assert ja.CLI_MODELS["ollama:some:tag"] is first


# ── _clean_env_for_cli: local routing + secret stripping intact ──────

def test_clean_env_ollama_routes_local_and_strips_secrets(files, monkeypatch):
    ja._register_ollama_cli_model("ollama:qwen2.5:7b")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-real-deepseek")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-real-anthropic")
    monkeypatch.setenv("GH_TOKEN", "gho_real")
    monkeypatch.setenv("SOME_SERVICE_SECRET", "shh")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    env = ja._clean_env_for_cli("ollama:qwen2.5:7b")
    # Local routing: the request model id carries the tag (the proxy
    # resolves ollama:<tag> statelessly → local daemon, no cloud).
    assert env["JARVIS_PROVIDER"] == "ollama"
    assert env["JARVIS_MODEL"] == "qwen2.5:7b"
    assert env["ANTHROPIC_MODEL"] == "ollama:qwen2.5:7b"
    assert env["OLLAMA_MODEL"] == "qwen2.5:7b"
    assert env["OLLAMA_BASE_URL"] == "http://127.0.0.1:11434"
    # Still the LOCAL proxy hop (the CLI only speaks Anthropic shape);
    # the stub key proves no real credential was handed over.
    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:4000"
    assert env["ANTHROPIC_API_KEY"] == "jarvis-proxy"
    # §P0-SEC-8 stripping is UNCHANGED on the local path.
    assert "DEEPSEEK_API_KEY" not in env
    assert "GH_TOKEN" not in env
    assert "SOME_SERVICE_SECRET" not in env


def test_clean_env_ollama_derives_base_url_from_local_llm_url(files, monkeypatch):
    ja._register_ollama_cli_model("ollama:qwen2.5:7b")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_URL", "http://10.0.0.5:11434/v1")
    env = ja._clean_env_for_cli("ollama:qwen2.5:7b")
    assert env["OLLAMA_BASE_URL"] == "http://10.0.0.5:11434"


def test_clean_env_cloud_path_unchanged(files, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-real-deepseek")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    env = ja._clean_env_for_cli("deepseek-v4-pro")
    assert env["JARVIS_PROVIDER"] == "deepseek"
    assert env["JARVIS_MODEL"] == "deepseek-v4-pro"
    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:4000"
    assert env["ANTHROPIC_API_KEY"] == "jarvis-proxy"
    # No local-mode vars leak onto the cloud path.
    assert "ANTHROPIC_MODEL" not in env
    assert "OLLAMA_MODEL" not in env
    # Secrets still stripped.
    assert "DEEPSEEK_API_KEY" not in env


# ── Tray display parity (voice_client_tray_config.read_cli_model) ────

@pytest.fixture
def tray_files(tmp_path, monkeypatch):
    cmodel = tmp_path / "cli-model"
    vmode = tmp_path / "voice-mode"
    monkeypatch.setattr(vtc, "CLI_MODEL_FILE", cmodel)
    monkeypatch.setattr(vtc, "_VOICE_MODE_FILE", vmode)
    monkeypatch.setattr(vtc, "_local_cli_tag_cache", (0.0, None))
    monkeypatch.delenv("JARVIS_LOCAL_LLM_MODEL", raising=False)
    monkeypatch.setattr(
        local_model_picker, "resolve_installed_model_tag", lambda preferred="": None
    )
    return cmodel, vmode


def test_tray_display_local_mode_shows_local_model(tray_files, monkeypatch):
    cmodel, vmode = tray_files
    _write(cmodel, "claude-sonnet-4-6")
    _write(vmode, "local")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "qwen2.5:7b")
    assert vtc.read_cli_model() == "ollama:qwen2.5:7b"


def test_tray_display_cloud_mode_unchanged(tray_files):
    cmodel, vmode = tray_files
    _write(cmodel, "claude-sonnet-4-6")
    _write(vmode, "cloud")
    assert vtc.read_cli_model() == "claude-sonnet-4-6"


def test_tray_display_local_mode_unresolvable_falls_back_to_pin(tray_files):
    cmodel, vmode = tray_files
    _write(cmodel, "claude-sonnet-4-6")
    _write(vmode, "local")
    # fixture stubs discovery to None + clears env → cloud pin shown
    # (matches read_cli_model's keep-the-cloud-pin degradation).
    assert vtc.read_cli_model() == "claude-sonnet-4-6"


def test_tray_display_discovery_is_ttl_cached(tray_files, monkeypatch):
    cmodel, vmode = tray_files
    _write(cmodel, "claude-sonnet-4-6")
    _write(vmode, "local")
    calls = {"n": 0}

    def _resolve(preferred=""):
        calls["n"] += 1
        return "qwen2.5:7b"

    monkeypatch.setattr(local_model_picker, "resolve_installed_model_tag", _resolve)
    assert vtc.read_cli_model() == "ollama:qwen2.5:7b"
    assert vtc.read_cli_model() == "ollama:qwen2.5:7b"
    assert calls["n"] == 1  # second hit served from the TTL cache


# ── Conversation-mode preset sanity ──────────────────────────────────

def test_local_mode_preset_leaves_cli_model_untouched():
    """The built-in Local mode must NOT write a cli-model value (None =
    leave the cloud pin on disk; the read-time override does the rest).
    The old preset wrote the malformed 'ollama-qwen3-30b-a3b', which
    read_cli_model rejected → silent fallback to a CLOUD default."""
    from pipeline import conversation_modes as cm
    local = next(m for m in cm._BUILTINS if m["id"] == "local")
    assert local["cli_model"] is None
    assert local["voice_model"] is None

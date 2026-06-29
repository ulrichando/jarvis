import json
from pathlib import Path
import pytest


@pytest.fixture
def modes_path(tmp_path, monkeypatch):
    p = tmp_path / "modes.json"
    monkeypatch.setattr("pipeline.conversation_modes.MODES_FILE", p)
    return p


def test_seeds_builtins_when_missing(modes_path):
    from pipeline import conversation_modes as cm
    doc = cm.load()
    ids = [m["id"] for m in doc["modes"]]
    assert ids == ["deepseek", "claude", "local"]
    assert doc["active"] == "deepseek"
    assert modes_path.exists()
    on_disk = json.loads(modes_path.read_text())
    assert on_disk["active"] == "deepseek"


def test_deepseek_builtin_is_internally_consistent(modes_path):
    from pipeline import conversation_modes as cm
    ds = next(m for m in cm.load()["modes"] if m["id"] == "deepseek")
    assert ds["voice_model"] == "deepseek-v4-flash"
    assert ds["cli_model"] == "deepseek-v4-pro"
    assert ds["voice_mode"] == "cloud"


def test_local_builtin_is_on_device(modes_path):
    from pipeline import conversation_modes as cm
    lo = next(m for m in cm.load()["modes"] if m["id"] == "local")
    assert lo["voice_mode"] == "local"
    assert lo["voice_model"] is None
    assert lo["cli_model"] == "ollama-qwen3-30b-a3b"


def test_apply_writes_all_setting_files(modes_path, tmp_path, monkeypatch):
    from pipeline import conversation_modes as cm
    files = {}
    for name in ("voice-mode", "voice-model", "cli-model",
                 "tts-provider", "voice-tts-voice", "mode-allowed-tools"):
        p = tmp_path / name
        files[name] = p
        monkeypatch.setattr(cm, f"_F_{name.replace('-', '_').upper()}", p)

    cm.apply("claude")

    assert files["voice-mode"].read_text().strip() == "cloud"
    assert files["voice-model"].read_text().strip() == "claude-haiku-4-5"
    assert files["cli-model"].read_text().strip() == "claude-sonnet-4-6"
    assert files["tts-provider"].read_text().strip() == "kokoro:af_bella"
    assert files["voice-tts-voice"].read_text().strip() == "af_bella"
    assert files["mode-allowed-tools"].read_text().strip() == ""
    assert cm.load()["active"] == "claude"


def test_apply_local_omits_voice_model(modes_path, tmp_path, monkeypatch):
    from pipeline import conversation_modes as cm
    vm = tmp_path / "voice-model"
    vm.write_text("stale-value\n")
    monkeypatch.setattr(cm, "_F_VOICE_MODEL", vm)
    monkeypatch.setattr(cm, "_F_VOICE_MODE", tmp_path / "voice-mode")
    monkeypatch.setattr(cm, "_F_CLI_MODEL", tmp_path / "cli-model")
    monkeypatch.setattr(cm, "_F_TTS_PROVIDER", tmp_path / "tts-provider")
    monkeypatch.setattr(cm, "_F_VOICE_TTS_VOICE", tmp_path / "voice-tts-voice")
    monkeypatch.setattr(cm, "_F_MODE_ALLOWED_TOOLS", tmp_path / "mode-allowed-tools")

    cm.apply("local")
    assert (tmp_path / "voice-mode").read_text().strip() == "local"
    assert vm.read_text().strip() == "stale-value"

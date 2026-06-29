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

"""Spec B (Plane 3) — propose_code_mod tool registration + queue write."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def test_tool_inert_when_env_unset(monkeypatch):
    monkeypatch.delenv("JARVIS_AUTOMOD_ENABLED", raising=False)
    sys.modules.pop("tools.code_mod", None)
    from tools import code_mod
    assert code_mod.is_available() is False


def test_tool_available_when_env_set(monkeypatch):
    monkeypatch.setenv("JARVIS_AUTOMOD_ENABLED", "1")
    sys.modules.pop("tools.code_mod", None)
    from tools import code_mod
    assert code_mod.is_available() is True


def test_propose_writes_to_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_ENABLED", "1")
    sys.modules.pop("tools.code_mod", None)
    from tools import code_mod
    res = code_mod._handle_propose({
        "intent": "fix the 'sir' suffix",
        "rationale": "user asked explicitly",
    })
    res_dict = json.loads(res)
    assert res_dict["success"]
    assert "id" in res_dict

    queue = (tmp_path / "auto-mods" / "queue.jsonl").read_text().strip().splitlines()
    assert len(queue) == 1
    rec = json.loads(queue[0])
    assert rec["kind"] == "explicit"
    assert rec["intent"] == "fix the 'sir' suffix"
    assert rec["rationale"] == "user asked explicitly"


def test_propose_rejects_empty_intent(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_ENABLED", "1")
    sys.modules.pop("tools.code_mod", None)
    from tools import code_mod
    res = code_mod._handle_propose({"intent": "", "rationale": "x"})
    res_dict = json.loads(res)
    assert not res_dict.get("success", True)


def test_propose_rejects_empty_rationale(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_ENABLED", "1")
    sys.modules.pop("tools.code_mod", None)
    from tools import code_mod
    res = code_mod._handle_propose({"intent": "fix X", "rationale": "  "})
    res_dict = json.loads(res)
    assert not res_dict.get("success", True)


def test_schema_shape(monkeypatch):
    monkeypatch.setenv("JARVIS_AUTOMOD_ENABLED", "1")
    sys.modules.pop("tools.code_mod", None)
    from tools.code_mod import CODE_MOD_SCHEMA
    assert CODE_MOD_SCHEMA["name"] == "propose_code_mod"
    props = CODE_MOD_SCHEMA["parameters"]["properties"]
    assert "intent" in props
    assert "rationale" in props
    assert set(CODE_MOD_SCHEMA["parameters"]["required"]) == {"intent", "rationale"}


def test_propose_appends_to_existing_queue(tmp_path, monkeypatch):
    """Two consecutive proposes should produce two queue lines."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_ENABLED", "1")
    sys.modules.pop("tools.code_mod", None)
    from tools import code_mod
    code_mod._handle_propose({"intent": "fix A", "rationale": "r1"})
    code_mod._handle_propose({"intent": "fix B", "rationale": "r2"})
    queue = (tmp_path / "auto-mods" / "queue.jsonl").read_text().strip().splitlines()
    assert len(queue) == 2
    intents = [json.loads(line)["intent"] for line in queue]
    assert "fix A" in intents
    assert "fix B" in intents

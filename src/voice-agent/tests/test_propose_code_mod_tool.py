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
    assert rec["evolution"]["criteria_version"]
    assert "safety" in rec["evolution"]["satisfied"]


def test_propose_kicks_ondemand_when_spawn_live(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_ENABLED", "1")
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "1")
    sys.modules.pop("tools.code_mod", None)
    from tools import code_mod

    calls = []

    class _Proc:
        pid = 123

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return _Proc()

    monkeypatch.setattr(code_mod.subprocess, "Popen", fake_popen)
    res = json.loads(code_mod._handle_propose({
        "intent": "improve self-evolution routing",
        "rationale": "user asked what JARVIS would improve about herself",
    }))

    assert res["success"] is True
    assert res["spawn_started"] is True
    assert calls
    assert calls[0][0][0].endswith("bin/jarvis-evolution-ondemand")
    assert calls[0][0][1] == res["id"]
    assert calls[0][1]["start_new_session"] is True


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
    assert props["source"]["enum"] == ["explicit", "autonomous"]
    assert set(CODE_MOD_SCHEMA["parameters"]["required"]) == {"intent", "rationale"}
    desc = CODE_MOD_SCHEMA["description"].lower()
    assert "self-improve" in desc
    assert "what you would improve" in desc
    assert "self-initiate" in desc


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


def test_propose_can_mark_autonomous_source(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_ENABLED", "1")
    sys.modules.pop("tools.code_mod", None)
    from tools import code_mod

    res = json.loads(code_mod._handle_propose({
        "intent": "tighten browser routing after repeated wrong-tool choices",
        "rationale": "same routing failure happened repeatedly in recent turns",
        "source": "autonomous",
    }))
    assert res["success"] is True
    assert res["source"] == "autonomous"
    queue = (tmp_path / "auto-mods" / "queue.jsonl").read_text().strip().splitlines()
    rec = json.loads(queue[0])
    assert rec["kind"] == "autonomous"
    assert rec["evolution"]["source"] == "autonomous"


def test_supervisor_routes_self_improvement_to_code_mod():
    prompt = (
        Path(__file__).resolve().parents[1] / "prompts" / "supervisor.md"
    ).read_text(encoding="utf-8").lower()
    assert "self-evolution / source changes" in prompt
    assert "propose_code_mod" in prompt
    assert "do not\nsay you cannot self-modify" in prompt
    assert "what would you improve about\nyourself" in prompt
    assert 'source="autonomous"' in prompt
    assert "repeated friction" in prompt

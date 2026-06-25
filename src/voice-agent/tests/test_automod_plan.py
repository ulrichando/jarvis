"""Pre-build PLAN stage — fusion + gate + best-effort (2026-06-25). LLM stubbed.

The plan stage is ADVISORY-but-gating-early: it rejects an intent before the
build only on a clear blocklist / scope / infeasible verdict; any LLM failure
proceeds with no plan so the loop is never blocked.
"""
from __future__ import annotations

import sys
from pathlib import Path

_VA = Path(__file__).resolve().parent.parent
if str(_VA) not in sys.path:
    sys.path.insert(0, str(_VA))

from pipeline.automod import plan as pl


# ── _gate ─────────────────────────────────────────────────────────────


def test_gate_in_scope_clean_ok():
    ok, _ = pl._gate(["src/voice-agent/tools/foo.py", "src/voice-agent/pipeline/bar.py"])
    assert ok


def test_gate_out_of_scope_rejects():
    ok, reason = pl._gate(["src/web/app/page.tsx"])
    assert not ok and "leaves" in reason


def test_gate_blocklisted_rejects():
    ok, reason = pl._gate(["src/voice-agent/sanitizers/dsml.py"])
    assert not ok and "blocklist" in reason


# ── make_plan (LLM stubbed via _call_json) ────────────────────────────


def test_make_plan_no_intent_proceeds():
    out = pl.make_plan("")
    assert out["verdict"] == "proceed" and out["plan"] is None


def test_make_plan_agents_down_proceeds(monkeypatch):
    monkeypatch.setattr(pl, "_call_json", lambda spec, prompt: None)
    out = pl.make_plan("do a thing")
    assert out["verdict"] == "proceed"  # never block the loop on a plan-stage failure
    assert "unavailable" in out["reason"]


def test_make_plan_blocklisted_file_rejects_early(monkeypatch):
    # Both drafts plan a blocklisted file → rejected at the union gate (no judge).
    monkeypatch.setattr(
        pl, "_call_json",
        lambda spec, prompt: {"approach": "edit sanitizer",
                              "files": ["src/voice-agent/sanitizers/dsml.py"], "risks": []},
    )
    out = pl.make_plan("tweak the dsml sanitizer")
    assert out["verdict"] == "reject" and "blocklist" in out["reason"]


def test_make_plan_judge_infeasible_rejects(monkeypatch):
    def stub(spec, prompt):
        if "PLAN 1" in prompt:  # the judge call
            return {"chosen": 1, "feasible": False, "reason": "too big",
                    "approach": "x", "files": ["src/voice-agent/a.py"], "risks": []}
        return {"approach": "draft", "files": ["src/voice-agent/a.py"], "risks": []}

    monkeypatch.setattr(pl, "_call_json", stub)
    out = pl.make_plan("rewrite everything")
    assert out["verdict"] == "reject" and "infeasible" in out["reason"]


def test_make_plan_clean_proceeds_with_fused_plan(monkeypatch):
    def stub(spec, prompt):
        if "PLAN 1" in prompt:  # judge picks + refines
            return {"chosen": 1, "feasible": True, "reason": "sound",
                    "approach": "add a guard", "files": ["src/voice-agent/tools/x.py"],
                    "risks": ["minor"]}
        return {"approach": "add a guard", "files": ["src/voice-agent/tools/x.py"], "risks": []}

    monkeypatch.setattr(pl, "_call_json", stub)
    out = pl.make_plan("add a guard to tool x")
    assert out["verdict"] == "proceed"
    assert out["plan"]["files"] == ["src/voice-agent/tools/x.py"]
    assert out["plan"]["approach"] == "add a guard"
    assert len(out["models"]) == 3  # two drafters + a judge


def test_format_for_prompt():
    block = pl.format_for_prompt(
        {"approach": "do x", "files": ["src/voice-agent/a.py"], "risks": ["r1"]}
    )
    assert "REVIEWED PLAN" in block and "do x" in block and "src/voice-agent/a.py" in block
    assert pl.format_for_prompt(None) == ""

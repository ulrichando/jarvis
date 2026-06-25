"""3-lens review council — fusion, best-effort, + multi-model dispatch (2026-06-25).

The council is ADVISORY (writes <id>.review.json, never gates). Each lens runs on
a DIFFERENT model family with a fallback to Claude. These tests pin the worst-of
fusion, the per-lens model selection + fallback, and that failures degrade to
'skipped' (never a silent pass). The model call is stubbed — no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))

from pipeline.automod import review_council as rc


def _lens(verdict, findings=None):
    return {"verdict": verdict, "findings": findings or [], "summary": "", "model": "test:m"}


# ── _fuse: worst-of ───────────────────────────────────────────────────


def test_fuse_all_pass_approves():
    out = rc._fuse({k: _lens("pass") for k in ("correctness", "security", "regression")})
    assert out["verdict"] == "pass"
    assert out["recommendation"] == "approve"


def test_fuse_any_concern_cautions():
    out = rc._fuse({"correctness": _lens("pass"), "security": _lens("concern"), "regression": _lens("pass")})
    assert out["verdict"] == "concern"
    assert out["recommendation"] == "caution"
    assert out["concern_lenses"] == ["security"]


def test_fuse_any_block_rejects():
    out = rc._fuse({"correctness": _lens("block"), "security": _lens("concern"), "regression": _lens("pass")})
    assert out["verdict"] == "block"
    assert out["recommendation"] == "reject"
    assert out["blocking_lenses"] == ["correctness"]


def test_fuse_skipped_never_reads_as_pass():
    out = rc._fuse({"correctness": rc._skipped("x"), "security": _lens("concern"), "regression": rc._skipped("y")})
    assert out["verdict"] == "concern"
    assert set(out["skipped"]) == {"correctness", "regression"}


def test_fuse_all_skipped_is_skipped():
    out = rc._fuse({k: rc._skipped("x") for k in ("correctness", "security", "regression")})
    assert out["verdict"] == "skipped"
    assert out["recommendation"] == "review"


# ── _lens_spec: the multi-model selection ─────────────────────────────


def test_lens_defaults_are_distinct_families():
    provs = {rc._lens_spec(l)[0] for l in ("correctness", "security", "regression")}
    assert provs == {"anthropic", "openai", "deepseek"}  # genuinely different model families


def test_lens_spec_env_override(monkeypatch):
    monkeypatch.setenv("JARVIS_REVIEW_MODEL_SECURITY", "groq:openai/gpt-oss-120b")
    assert rc._lens_spec("security") == ("groq", "openai/gpt-oss-120b")


def test_lens_spec_bare_value_is_anthropic(monkeypatch):
    monkeypatch.setenv("JARVIS_REVIEW_MODEL_CORRECTNESS", "claude-opus-4-8")
    assert rc._lens_spec("correctness") == ("anthropic", "claude-opus-4-8")


# ── review_proposal: best-effort persistence ──────────────────────────


def test_review_proposal_no_key_skips_and_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(rc, "_any_provider_key", lambda: False)
    out = rc.review_proposal("automod-nokey", "some diff", "intent")
    assert out["overall"]["verdict"] == "skipped"
    assert all(v["verdict"] == "skipped" for v in out["lenses"].values())
    assert rc.read_review("automod-nokey")["overall"]["verdict"] == "skipped"


def test_review_proposal_empty_diff_skips(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(rc, "_any_provider_key", lambda: True)
    out = rc.review_proposal("automod-nodiff", "   ", "intent")
    assert out["overall"]["verdict"] == "skipped"


def test_review_proposal_fuses_and_records_models(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(rc, "_any_provider_key", lambda: True)

    def fake_one(lens, instruction, intent, diff):
        base = _lens("block" if lens == "security" else "pass", ["finding"] if lens == "security" else [])
        return {**base, "model": f"prov:{lens}"}

    monkeypatch.setattr(rc, "_review_one", fake_one)
    out = rc.review_proposal("automod-z", "real diff", "intent")
    assert out["overall"]["recommendation"] == "reject"
    assert out["overall"]["blocking_lenses"] == ["security"]
    assert out["models"]["security"] == "prov:security"  # per-lens model recorded
    assert rc.read_review("automod-z")["overall"]["recommendation"] == "reject"


# ── _review_one: dispatch + parse + fallback (model call stubbed) ─────


def test_review_one_parses_and_records_its_model(monkeypatch):
    monkeypatch.setattr(rc, "_call_model", lambda p, m, prompt: '{"verdict":"block","findings":["bad"],"summary":"no"}')
    out = rc._review_one("security", "instr", "intent", "diff")
    assert out["verdict"] == "block"
    assert out["findings"] == ["bad"]
    assert out["model"].startswith("openai:")  # ran on its configured (non-Claude) model


def test_review_one_unknown_verdict_becomes_concern(monkeypatch):
    monkeypatch.setattr(rc, "_call_model", lambda p, m, prompt: '{"verdict":"looks-fine","findings":[],"summary":"ok"}')
    out = rc._review_one("correctness", "instr", "intent", "diff")
    assert out["verdict"] == "concern"  # never silently 'pass' on a bad verdict


def test_review_one_falls_back_to_anthropic_when_primary_down(monkeypatch):
    calls = []

    def fake_call(provider, model, prompt):
        calls.append(provider)
        if provider != "anthropic":
            raise RuntimeError("primary provider down")
        return '{"verdict":"pass","findings":[],"summary":"ok"}'

    monkeypatch.setattr(rc, "_call_model", fake_call)
    out = rc._review_one("security", "instr", "intent", "diff")
    assert out["verdict"] == "pass"
    assert out["model"] == f"anthropic:{rc._FALLBACK_MODEL}"  # fell back to Claude
    assert calls[0] == "openai" and "anthropic" in calls


def test_review_one_all_attempts_fail_skips(monkeypatch):
    def fake_call(provider, model, prompt):
        raise RuntimeError("everything down")

    monkeypatch.setattr(rc, "_call_model", fake_call)
    out = rc._review_one("regression", "instr", "intent", "diff")
    assert out["verdict"] == "skipped"

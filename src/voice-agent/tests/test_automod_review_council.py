"""3-lens review council — fusion + best-effort behavior (2026-06-25).

The council is ADVISORY: it writes <id>.review.json but never gates a deploy.
These tests pin the worst-of fusion + that failures degrade to 'skipped'
(never a silent pass). The LLM call is stubbed — no network.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))

from pipeline.automod import review_council as rc


def _lens(verdict, findings=None):
    return {"verdict": verdict, "findings": findings or [], "summary": ""}


# ── _fuse: worst-of ───────────────────────────────────────────────────


def test_fuse_all_pass_approves():
    out = rc._fuse({"correctness": _lens("pass"), "security": _lens("pass"), "regression": _lens("pass")})
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
    # one real concern + two skipped → the overall is the concern, and the
    # skipped lenses are recorded (so the human doesn't read them as passes).
    out = rc._fuse({"correctness": rc._skipped("x"), "security": _lens("concern"), "regression": rc._skipped("y")})
    assert out["verdict"] == "concern"
    assert set(out["skipped"]) == {"correctness", "regression"}


def test_fuse_all_skipped_is_skipped():
    out = rc._fuse({k: rc._skipped("x") for k in ("correctness", "security", "regression")})
    assert out["verdict"] == "skipped"
    assert out["recommendation"] == "review"


# ── review_proposal: best-effort persistence ──────────────────────────


def test_review_proposal_no_key_skips_and_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = rc.review_proposal("automod-nokey", "some diff", "intent")
    assert out["overall"]["verdict"] == "skipped"
    assert all(v["verdict"] == "skipped" for v in out["lenses"].values())
    assert rc.read_review("automod-nokey")["overall"]["verdict"] == "skipped"


def test_review_proposal_empty_diff_skips(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    out = rc.review_proposal("automod-nodiff", "   ", "intent")
    assert out["overall"]["verdict"] == "skipped"


def test_review_proposal_fuses_lenses_and_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    def fake_one(lens, instruction, intent, diff):
        return _lens("block" if lens == "security" else "pass", ["finding"] if lens == "security" else [])

    monkeypatch.setattr(rc, "_review_one", fake_one)
    out = rc.review_proposal("automod-z", "real diff", "intent")
    assert out["overall"]["verdict"] == "block"
    assert out["overall"]["recommendation"] == "reject"
    assert out["overall"]["blocking_lenses"] == ["security"]
    assert rc.read_review("automod-z")["overall"]["recommendation"] == "reject"


# ── _review_one: parsing + normalization (LLM stubbed) ────────────────


def _stub_anthropic(monkeypatch, body_text):
    block = types.SimpleNamespace(text=body_text)
    resp = types.SimpleNamespace(content=[block])
    fake = types.SimpleNamespace(
        Anthropic=lambda **kw: types.SimpleNamespace(
            messages=types.SimpleNamespace(create=lambda **k: resp)
        )
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake)


def test_review_one_valid_verdict_passes_through(monkeypatch):
    _stub_anthropic(monkeypatch, '{"verdict":"block","findings":["bad thing"],"summary":"nope"}')
    out = rc._review_one("security", "instr", "intent", "diff")
    assert out["verdict"] == "block"
    assert out["findings"] == ["bad thing"]


def test_review_one_unknown_verdict_becomes_concern(monkeypatch):
    _stub_anthropic(monkeypatch, '{"verdict":"looks-fine","findings":[],"summary":"ok"}')
    out = rc._review_one("correctness", "instr", "intent", "diff")
    assert out["verdict"] == "concern"  # never silently 'pass' on a bad verdict


def test_review_one_unparseable_output_skips(monkeypatch):
    _stub_anthropic(monkeypatch, "the model rambled without any JSON at all")
    out = rc._review_one("regression", "instr", "intent", "diff")
    assert out["verdict"] == "skipped"

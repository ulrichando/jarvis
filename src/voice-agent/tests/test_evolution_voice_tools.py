"""Tests for the evolution voice tools.

Tests call the underlying coroutine bodies directly because
@function_tool wrapping in livekit-agents makes the decorated
callable a non-trivially callable Tool, not a plain coroutine.
The implementation exposes `*_impl` functions for this purpose.
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest


ANCHOR = """\
---
schema_version: 2
---

## ═══ ANCHOR ═══

- <!-- id=A-0001 tier=anchor --> Reply "Yes?".
"""


@pytest.fixture
def populated_store(tmp_path, monkeypatch):
    from pipeline.evolution.store import RuleStore
    from pipeline.evolution.schema import Rule
    from pipeline.evolution import audit_log

    anchor = tmp_path / "anchor.md"
    learned = tmp_path / "learned.md"
    anchor.write_text(ANCHOR)
    sha = hashlib.sha256(ANCHOR.encode()).hexdigest()
    learned.write_text(
        f"---\nschema_version: 2\nanchor_baseline_sha256: {sha}\n---\n\n"
    )
    monkeypatch.setattr(audit_log, "LOG_PATH", tmp_path / "audit.jsonl")

    store = RuleStore(anchor_path=anchor, learned_path=learned)
    store.load()
    store.save_rule(Rule(id="R-0001", tier="core", text="Yes? reply rule"))
    store.save_rule(Rule(id="R-0002", tier="accepted",
                          text="Use --profile-directory=Default with Chrome"))
    store.save_rule(Rule(id="R-0003", tier="staged",
                          text="[STAGED] Don't open chromium"))

    from tools import evolution_voice
    monkeypatch.setattr(evolution_voice, "_default_store",
                        lambda: RuleStore(anchor_path=anchor, learned_path=learned))
    return store


def test_evolution_status_counts_each_tier(populated_store):
    from tools.evolution_voice import evolution_status_impl

    out = asyncio.run(evolution_status_impl())
    assert "1 in core" in out
    assert "1 accepted" in out
    assert "1 staged" in out
    assert "anchor" not in out.lower() or "1 anchor" in out


def test_revert_rule_demotes_by_fuzzy_match(populated_store):
    from tools.evolution_voice import revert_rule_impl

    out = asyncio.run(revert_rule_impl(query="chromium"))
    assert "R-0003" in out or "chromium" in out.lower()

    loaded = populated_store.load()
    assert all(r.id != "R-0003" for r in loaded.staged)
    assert any(r.id == "R-0003" for r in loaded.archived)


def test_revert_rule_refuses_anchor_match(populated_store):
    from tools.evolution_voice import revert_rule_impl

    out = asyncio.run(revert_rule_impl(query="reply yes"))
    assert "anchor" in out.lower() or "cannot" in out.lower() or "refused" in out.lower()


def test_review_staged_rules_lists_with_prefix(populated_store):
    from tools.evolution_voice import review_staged_rules_impl

    out = asyncio.run(review_staged_rules_impl())
    assert "R-0003" in out
    assert "chromium" in out.lower()

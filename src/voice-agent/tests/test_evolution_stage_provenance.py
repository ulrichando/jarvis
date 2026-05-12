"""Tests for Stage 1 — Provenance gate."""
from __future__ import annotations

import pytest


def test_batch_proposal_needs_three_evidence_turns():
    from pipeline.evolution.evaluator.provenance import provenance_stage

    p = {
        "source": "batch_miner",
        "rule": "test rule",
        "evidence_turns": ["t-1", "t-2"],
    }
    r = provenance_stage(p)
    assert r.passed is False
    assert "evidence" in r.reason.lower()


def test_batch_proposal_passes_with_three_turns():
    from pipeline.evolution.evaluator.provenance import provenance_stage

    p = {
        "source": "batch_miner",
        "rule": "test rule",
        "evidence_turns": ["t-1", "t-2", "t-3"],
    }
    r = provenance_stage(p)
    assert r.passed is True


def test_live_capture_needs_only_one_turn():
    from pipeline.evolution.evaluator.provenance import provenance_stage

    p = {
        "source": "live_capture",
        "rule": "stop opening chromium",
        "evidence_turns": ["t-1", "t-2"],
        "matched_phrase": "don't open",
    }
    r = provenance_stage(p)
    assert r.passed is True


def test_rule_over_200_chars_fails():
    from pipeline.evolution.evaluator.provenance import provenance_stage

    p = {
        "source": "batch_miner",
        "rule": "x" * 220,
        "evidence_turns": ["t-1", "t-2", "t-3"],
    }
    r = provenance_stage(p)
    assert r.passed is False
    assert "length" in r.reason.lower()


def test_archival_proposal_uses_target_id_not_evidence_turns():
    from pipeline.evolution.evaluator.provenance import provenance_stage

    p = {
        "source": "contradiction_detector",
        "kind": "archive_dead_subsystem",
        "target_id": "R-0011",
        "reason": "dead_subsystem",
    }
    r = provenance_stage(p)
    assert r.passed is True

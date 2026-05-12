"""Stage 1 — Provenance gate.

Cheapest stage. Drops proposals with insufficient evidence so we
don't burn judge tokens on noise. Three rules:

  - batch_miner / contradiction-detector proposals need ≥3
    evidence turn IDs (or a target_id for archival)
  - live_capture proposals need ≥1 evidence turn + a matched phrase
  - rule text must be ≤200 chars
"""
from __future__ import annotations

from .base import EvaluatorResult


__all__ = ["provenance_stage"]


def provenance_stage(proposal: dict) -> EvaluatorResult:
    source = proposal.get("source") or ""
    rule = (proposal.get("rule") or "").strip()
    turns = proposal.get("evidence_turns") or []

    if proposal.get("kind", "").startswith("archive_") and proposal.get("target_id"):
        return EvaluatorResult(
            stage="provenance",
            passed=True,
            reason=f"archival proposal targets {proposal['target_id']}",
            detail={"kind": proposal["kind"]},
        )

    if not rule:
        return EvaluatorResult(
            stage="provenance",
            passed=False,
            reason="missing rule text",
        )
    if len(rule) > 200:
        return EvaluatorResult(
            stage="provenance",
            passed=False,
            reason=f"rule length {len(rule)} > 200",
        )

    if source == "live_capture":
        if not turns:
            return EvaluatorResult(
                stage="provenance",
                passed=False,
                reason="live_capture missing evidence turn",
            )
        if not proposal.get("matched_phrase"):
            return EvaluatorResult(
                stage="provenance",
                passed=False,
                reason="live_capture missing matched_phrase",
            )
        return EvaluatorResult(
            stage="provenance",
            passed=True,
            reason=f"live_capture with {len(turns)} evidence turn(s)",
            detail={"evidence_turns": list(turns)},
        )

    if len(turns) < 3:
        return EvaluatorResult(
            stage="provenance",
            passed=False,
            reason=f"insufficient evidence: {len(turns)} turn(s), need ≥3",
        )
    return EvaluatorResult(
        stage="provenance",
        passed=True,
        reason=f"{len(turns)} evidence turns",
        detail={"evidence_turns": list(turns)},
    )

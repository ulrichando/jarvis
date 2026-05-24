"""Track 2b — procedure as a PROPOSAL_KIND in skill_review.

Validates: kind in PROPOSAL_KINDS, _validate_payload accepts/rejects,
apply_proposal writes through to file_memory's procedure target.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def test_procedure_in_proposal_kinds():
    from pipeline.skill_review import PROPOSAL_KINDS
    assert "procedure" in PROPOSAL_KINDS


def test_validate_payload_accepts_valid_procedure():
    from pipeline.skill_review import _validate_payload
    payload = {"name": "deploy-app", "steps": ["run pytest", "git push", "check CI"]}
    cleaned = _validate_payload("procedure", payload)
    assert cleaned is not None
    assert cleaned["name"] == "deploy-app"
    assert cleaned["steps"] == ["run pytest", "git push", "check CI"]


@pytest.mark.parametrize("bad_payload", [
    {"name": "", "steps": ["a"]},
    {"name": "Deploy App", "steps": ["a"]},   # not kebab-case
    {"name": "deploy-app", "steps": []},
    {"name": "deploy-app", "steps": "not a list"},
    {"name": "deploy-app"},  # missing steps
    {"steps": ["a"]},        # missing name
])
def test_validate_payload_rejects_bad_procedure(bad_payload):
    from pipeline.skill_review import _validate_payload
    cleaned = _validate_payload("procedure", bad_payload)
    assert cleaned is None


def test_apply_proposal_writes_procedure(tmp_path, monkeypatch):
    """Apply a procedure proposal → PROCEDURES.md gains the entry."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline import file_memory
    file_memory.reload_store()

    from pipeline.skill_review import Proposal, apply_proposal
    p = Proposal(
        kind="procedure",
        payload={"name": "deploy-app", "steps": ["run pytest", "git push"]},
        rationale="testing",
        source_turn_id=42,
    )
    res = apply_proposal(p)
    assert res.ok, res.detail

    # File exists with content
    procedures_md = tmp_path / "memories" / "PROCEDURES.md"
    assert procedures_md.exists()
    body = procedures_md.read_text(encoding="utf-8")
    assert "deploy-app" in body
    assert "run pytest" in body
    assert "git push" in body

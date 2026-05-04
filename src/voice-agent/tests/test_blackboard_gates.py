"""Evidence-finder helpers — the core of the grounding gate's check."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def populated_client():
    from blackboard.client import BlackboardClient
    from blackboard.schema import ToolResult

    prefix = f"test:{time.time_ns()}"
    c = BlackboardClient(prefix=prefix)
    base = time.time()
    c.write_tool_result(ToolResult(
        tool="ext_new_tab", args={"url": "https://youtube.com"},
        result="ok: tab opened", ok=True, ts=base - 5, call_id="call_a",
    ))
    c.write_tool_result(ToolResult(
        tool="ext_navigate", args={"url": "https://example.com"},
        result="ok: navigated", ok=True, ts=base - 2, call_id="call_b",
    ))
    yield c
    for key in c._r.keys(f"{prefix}:*"):
        c._r.delete(key)


def test_find_tool_evidence_matches_recent(populated_client):
    from blackboard.gates import find_tool_evidence
    # Looking for evidence of "tab opened" — should match call_a.
    ev = find_tool_evidence(
        populated_client,
        claim_keywords=["opened", "tab"],
        within_seconds=30,
    )
    assert ev is not None
    assert ev.tool == "ext_new_tab"


def test_find_tool_evidence_returns_none_when_too_old(populated_client):
    from blackboard.gates import find_tool_evidence
    # within_seconds=1 — both fixture entries are older than 1s.
    ev = find_tool_evidence(
        populated_client,
        claim_keywords=["opened"],
        within_seconds=1,
    )
    assert ev is None


def test_find_tool_evidence_no_match_returns_none(populated_client):
    from blackboard.gates import find_tool_evidence
    ev = find_tool_evidence(
        populated_client,
        claim_keywords=["posted", "tweet"],
        within_seconds=60,
    )
    assert ev is None


def test_has_recent_tool_specific_name(populated_client):
    from blackboard.gates import has_recent_tool
    assert has_recent_tool(
        populated_client, tool_name="ext_new_tab", within_seconds=30,
    ) is True
    assert has_recent_tool(
        populated_client, tool_name="ext_send_email", within_seconds=30,
    ) is False

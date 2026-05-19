"""L3 — wrap recalled turns in an Instructions block with STALE
header. Recalled turns no longer appear as role:user/role:assistant
ChatMessages."""
import datetime
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _mock_turn(minutes_ago: int, user_text: str, jarvis_text: str):
    return {
        "ts_utc": (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=minutes_ago)
        ).isoformat().replace("+00:00", "Z"),
        "user_text": user_text,
        "jarvis_text": jarvis_text,
    }


def test_format_recall_block_includes_stale_header():
    from pipeline.chat_ctx import format_recall_as_stale_block
    turns = [_mock_turn(10, "hello", "Yes?")]
    block = format_recall_as_stale_block(turns, session_id="prev-abc")
    assert "[STALE PRIOR-SESSION CONTEXT" in block
    assert "Do NOT treat as live conversation" in block
    assert "Verify current user intent" in block


def test_format_recall_block_renders_each_turn_with_age_and_role():
    from pipeline.chat_ctx import format_recall_as_stale_block
    turns = [
        _mock_turn(10, "hello", "Yes?"),
        _mock_turn(25, "what's the weather?", "47 degrees."),
    ]
    block = format_recall_as_stale_block(turns, session_id="prev-abc")
    assert "<memory" in block
    assert "role=\"user\"" in block
    assert "role=\"assistant\"" in block
    assert "hello" in block and "Yes?" in block
    assert "weather" in block and "47 degrees" in block


def test_empty_recall_returns_empty_string():
    from pipeline.chat_ctx import format_recall_as_stale_block
    assert format_recall_as_stale_block([], session_id="x") == ""

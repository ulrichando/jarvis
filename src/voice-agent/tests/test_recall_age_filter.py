"""L3 — recall age filter. Recalled turns older than
JARVIS_RECALL_MAX_AGE_S must be dropped entirely."""
import datetime
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _mock_turn(minutes_ago: int, user_text: str = "hi", jarvis_text: str = "Yes?"):
    return {
        "ts_utc": (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=minutes_ago)
        ).isoformat().replace("+00:00", "Z"),
        "user_text": user_text,
        "jarvis_text": jarvis_text,
    }


def test_recall_drops_turns_older_than_max_age(monkeypatch):
    """Turns older than JARVIS_RECALL_MAX_AGE_S are excluded."""
    monkeypatch.setenv("JARVIS_RECALL_MAX_AGE_S", "1800")
    from pipeline.chat_ctx import filter_recall_by_age

    turns = [
        _mock_turn(10, "fresh"),     # within window
        _mock_turn(45, "stale-1"),   # over window
        _mock_turn(180, "stale-2"),  # over window
    ]
    kept = filter_recall_by_age(turns)
    assert len(kept) == 1
    assert kept[0]["user_text"] == "fresh"


def test_recall_zero_age_disables_recall(monkeypatch):
    """JARVIS_RECALL_MAX_AGE_S=0 disables recall entirely."""
    monkeypatch.setenv("JARVIS_RECALL_MAX_AGE_S", "0")
    from pipeline.chat_ctx import filter_recall_by_age

    turns = [_mock_turn(1, "very recent"), _mock_turn(60, "older")]
    kept = filter_recall_by_age(turns)
    assert kept == []


def test_recall_default_window_is_1800s(monkeypatch):
    """Default age window is 30 minutes when env var is unset."""
    monkeypatch.delenv("JARVIS_RECALL_MAX_AGE_S", raising=False)
    from pipeline.chat_ctx import filter_recall_by_age

    turns = [_mock_turn(25, "in-window"), _mock_turn(35, "over")]
    kept = filter_recall_by_age(turns)
    assert len(kept) == 1
    assert kept[0]["user_text"] == "in-window"

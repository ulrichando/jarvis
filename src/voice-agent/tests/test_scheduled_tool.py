"""Unit checks for the `scheduled` voice tool's pure helpers (no network)."""
import os

os.environ.setdefault("JARVIS_SCHEDULED_VOICE_TOKEN", "test-token")

from tools import scheduled  # noqa: E402


def test_match_exact_then_substring():
    tasks = [
        {"id": "1", "name": "Morning Brief"},
        {"id": "2", "name": "Weekly Review"},
    ]
    # exact (case-insensitive)
    assert scheduled._match(tasks, "morning brief")["id"] == "1"
    # substring
    assert scheduled._match(tasks, "weekly")["id"] == "2"
    # no match
    assert scheduled._match(tasks, "nonexistent") is None
    # empty
    assert scheduled._match(tasks, "  ") is None


def test_fmt_list():
    assert "don't have any" in scheduled._fmt_list([]).lower()
    out = scheduled._fmt_list(
        [{"name": "Morning Brief", "label": "Weekdays at 8:00 AM", "paused": False}]
    )
    assert "Morning Brief" in out and "8:00" in out
    # paused surfaces as paused
    assert "paused" in scheduled._fmt_list([{"name": "X", "paused": True}]).lower()


def test_configured_gate():
    assert scheduled._configured() is True  # token set at import
    old = os.environ.pop("JARVIS_SCHEDULED_VOICE_TOKEN", None)
    try:
        assert scheduled._configured() is False
    finally:
        if old is not None:
            os.environ["JARVIS_SCHEDULED_VOICE_TOKEN"] = old

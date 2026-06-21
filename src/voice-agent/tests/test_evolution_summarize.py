"""Tests for the self-evolution proposal summary generator."""
from __future__ import annotations

from pipeline.automod.summarize import _tests_ok, summarize


def test_summarize_basic():
    s = summarize({
        "id": "automod-2026-06-21-abc123",
        "intent": "Cache the route classifier regex\nReduces per-turn allocs.",
        "files_changed": ["src/voice-agent/pipeline/turn_router.py"],
        "diff_summary": "1 file changed, 4 insertions(+), 2 deletions(-)",
        "test_output_tail": "3219 passed in 70s",
    })
    assert s["title"] == "Cache the route classifier regex"   # first line of intent
    assert s["tests_ok"] is True
    assert "turn_router.py" in s["markdown"]
    assert "1 file," in s["short"] and "tests pass" in s["short"]


def test_summarize_flags_failed_tests():
    s = summarize({
        "id": "x", "intent": "y",
        "files_changed": ["a", "b"],
        "test_output_tail": "1 failed, 2 passed in 3s",
    })
    assert s["tests_ok"] is False
    assert "check tests" in s["short"]
    assert "2 files," in s["short"]


def test_tests_ok_heuristic():
    assert _tests_ok("3219 passed in 70s") is True
    assert _tests_ok("1 failed, 2 passed") is False
    assert _tests_ok("ERROR collecting tests") is False
    assert _tests_ok("") is False


def test_summarize_empty_artifact_is_safe():
    s = summarize({})
    assert s["title"].startswith("JARVIS self-evolution")
    assert s["tests_ok"] is False
    assert isinstance(s["markdown"], str) and s["markdown"]

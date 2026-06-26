"""Queue dedup at enqueue (2026-06-26): a non-retry intent whose exact text is
already pending is skipped; RETRY intents are exempt (they must re-attempt)."""
from __future__ import annotations

import json

from pipeline.automod import cycle


def _queue_intents():
    qp = cycle.queue_path()
    return [json.loads(l)["intent"] for l in qp.read_text().splitlines() if l.strip()]


def test_enqueue_skips_duplicate_non_retry_intent(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    cycle._enqueue({"id": "a", "intent": "Add a module docstring to foo.py"})
    cycle._enqueue({"id": "b", "intent": "Add a module docstring to foo.py"})  # dup → skip
    cycle._enqueue({"id": "c", "intent": "A different goal entirely"})
    intents = _queue_intents()
    assert intents.count("Add a module docstring to foo.py") == 1
    assert "A different goal entirely" in intents


def test_enqueue_keeps_retries_even_with_identical_text(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    cycle._enqueue({"id": "r1", "intent": "RETRY (attempt 2) of a self-evolution change that FAILED."})
    cycle._enqueue({"id": "r2", "intent": "RETRY (attempt 2) of a self-evolution change that FAILED."})
    assert len(_queue_intents()) == 2  # retries are never deduped


def test_already_queued_matches_exact_text(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    cycle._enqueue({"id": "a", "intent": "Goal X"})
    assert cycle._already_queued("Goal X") is True
    assert cycle._already_queued("Goal Y") is False

"""Tests for the closed evolution loop (2026-06-23):
self-assessment → auto-queue improvements; failed build → learn + re-queue with
a new approach (capped). Hermetic via JARVIS_HOME.
"""
from __future__ import annotations

import json
import time


def _read_queue(home):
    p = home / "auto-mods" / "queue.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_enqueue_improvements_queues_and_dedups(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import introspection

    result = {"improvements": [
        {"title": "Cut TTFW in the turn path", "rationale": "latency 0.4", "target_axis": "latency"},
        {"title": "Cut TTFW in the turn path", "rationale": "dup", "target_axis": "latency"},  # dup title
        {"title": "Tighten clarify routing", "rationale": "reask", "target_axis": "reask"},
    ]}
    n = introspection.enqueue_improvements(result)
    assert n == 2  # dup collapsed
    q = _read_queue(tmp_path)
    assert len(q) == 2
    assert all(r["kind"] == "self_improvement" for r in q)
    assert all(r["evolution"]["source"] == "autonomous" for r in q)
    # second call with the same titles → all deduped against the live queue
    assert introspection.enqueue_improvements(result) == 0


def test_priority_assignment_and_retry_inherits():
    from pipeline.automod import criteria, patterns
    assert criteria.enrich_record({"kind": "self_improvement", "intent": "x"})["priority"] == "P0"
    assert criteria.enrich_record({"kind": "correction", "intent": "x"})["priority"] == "P1"
    assert criteria.enrich_record({"kind": "fitness", "intent": "x"})["priority"] == "P2"
    assert criteria.enrich_record({"kind": "weird", "intent": "x"})["priority"] == "P3"
    # an explicitly-set priority is preserved (not overwritten by kind)
    assert criteria.enrich_record({"kind": "fitness", "intent": "x", "priority": "P0"})["priority"] == "P0"
    # retry inherits its lineage's priority ("comes back as P0..3 by ranking")
    art = {"status": "failed", "id": "automod-2026-06-23-zzzzzz", "attempt": 1,
           "rejection_reason": "too_many_files:9>5", "intent": "Fix", "priority": "P0"}
    assert patterns.build_retry_intent(art)["priority"] == "P0"


def _write_failed(home, *, id, attempt=1, reason="too_many_files:83>5", recent=True, extra=None):
    d = home / "auto-mods"
    d.mkdir(parents=True, exist_ok=True)
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if recent else "2020-01-01T00:00:00Z"
    art = {"id": id, "status": "failed", "rejection_reason": reason, "intent": "Fix the thing",
           "attempt": attempt, "created_at": created, "kind": "fitness"}
    if extra:
        art.update(extra)
    (d / f"{id}.json").write_text(json.dumps(art))


def test_scan_failed_retries_requeues_with_lesson(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import patterns

    _write_failed(tmp_path, id="automod-2026-06-23-aaaaaa", attempt=1)
    assert patterns._scan_failed_retries() == 1
    q = _read_queue(tmp_path)
    assert len(q) == 1
    retry = q[0]
    assert retry["attempt"] == 2
    assert retry["lineage"] == "automod-2026-06-23-aaaaaa"
    assert "5 files" in retry["intent"]  # the too_many_files lesson
    assert any("too_many_files" in x for x in retry["prior_failures"])
    # artifact marked retried → second scan is a no-op
    assert patterns._scan_failed_retries() == 0


def test_scan_failed_retries_does_not_cap_attempts(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import patterns

    _write_failed(tmp_path, id="automod-2026-06-23-bbbbbb", attempt=25)
    assert patterns._scan_failed_retries() == 1
    retry = _read_queue(tmp_path)[0]
    assert retry["attempt"] == 26


def test_scan_failed_retries_skips_fixtures_and_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import patterns

    _write_failed(tmp_path, id="automod-test-id", attempt=1)              # fixture
    _write_failed(tmp_path, id="automod-2020-01-01-old", attempt=1, recent=False)  # stale
    assert patterns._scan_failed_retries() == 0


def test_scan_failed_retries_keeps_ranked_stale_work(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import patterns

    _write_failed(
        tmp_path,
        id="automod-2020-01-01-p0",
        attempt=9,
        recent=False,
        extra={"priority": "P0"},
    )
    assert patterns._scan_failed_retries() == 1
    retry = _read_queue(tmp_path)[0]
    assert retry["priority"] == "P0"
    assert retry["attempt"] == 10

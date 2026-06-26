"""Assessment dedup against built proposals (2026-06-26). The self-assessment
must not re-queue an improvement that already has a built artifact (pending OR
failed) — otherwise it churns the queue with already-attempted goals and the
queue never drains."""
from __future__ import annotations

import json

from pipeline.automod import _state, introspection


def test_enqueue_improvements_skips_already_built(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    home = _state._automod_home()
    home.mkdir(parents=True, exist_ok=True)
    # an already-built FAILED proposal whose intent first-line == the improvement title
    (home / "automod-old.json").write_text(
        json.dumps({"id": "old", "intent": "Add retries to the HTTP client\n\nbecause flaky",
                    "status": "failed"}),
        encoding="utf-8",
    )
    result = {"improvements": [
        {"title": "Add retries to the HTTP client", "rationale": "r", "target_axis": "reliability"}
    ]}
    assert introspection.enqueue_improvements(result) == 0  # skipped — already attempted


def test_enqueue_improvements_queues_a_new_idea(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    _state._automod_home().mkdir(parents=True, exist_ok=True)
    result = {"improvements": [{"title": "A genuinely new idea nobody has tried", "rationale": "r"}]}
    assert introspection.enqueue_improvements(result) == 1

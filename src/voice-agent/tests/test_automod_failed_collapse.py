"""Failed-retry collapse (2026-06-26): a goal that failed N times leaves N records
(one per retry attempt). collapse_failed_retries keeps one per lineage — the
original, which carries the real goal text — and archives the rest, so the
/evolution Failed list reads one-per-goal instead of N duplicates."""
from __future__ import annotations

import json

from pipeline.automod import _state, patterns


def _failed_count(home):
    return sum(1 for f in home.glob("*.json")
               if not f.name.endswith(".review.json")
               and json.loads(f.read_text()).get("status") == "failed")


def test_collapse_keeps_one_per_lineage(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    home = _state._automod_home()
    home.mkdir(parents=True, exist_ok=True)
    # goal G: original (attempt 1, real text) + 2 retries (attempts 2, 3), one lineage
    for aid, att, intent in [("g1", 1, "Add retries to X"),
                             ("g2", 2, "RETRY attempt 2"),
                             ("g3", 3, "RETRY attempt 3")]:
        (home / f"{aid}.json").write_text(json.dumps(
            {"id": aid, "status": "failed", "attempt": att, "lineage": "g1", "intent": intent}))
    # an unrelated goal (single record) — must be untouched
    (home / "h1.json").write_text(json.dumps(
        {"id": "h1", "status": "failed", "attempt": 1, "lineage": "h1", "intent": "Other"}))
    assert _failed_count(home) == 4

    assert patterns.collapse_failed_retries() == 2  # g2, g3 archived
    assert _failed_count(home) == 2                  # g1 (original) + h1 remain
    assert (home / "g1.json").exists()               # the readable original is kept
    assert not (home / "g2.json").exists()
    assert (home / "_superseded" / "g2.json").exists()  # reversible


def test_collapse_is_noop_without_duplicates(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    home = _state._automod_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "a.json").write_text(json.dumps(
        {"id": "a", "status": "failed", "attempt": 1, "lineage": "a", "intent": "X"}))
    assert patterns.collapse_failed_retries() == 0

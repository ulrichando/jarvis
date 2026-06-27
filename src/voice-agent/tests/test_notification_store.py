"""Tests for the desktop-notification store (listener writes, tool reads)."""
import json
import time

import pipeline.notification_store as ns


def test_append_and_read(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    ns.append("App", "Summary", "Body")
    recs = ns.read(limit=10)
    assert len(recs) == 1
    assert recs[0]["app"] == "App"
    assert recs[0]["summary"] == "Summary"
    assert recs[0]["body"] == "Body"


def test_read_newest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    ns.append("A", "1", "")
    ns.append("B", "2", "")
    assert [r["summary"] for r in ns.read(limit=10)] == ["2", "1"]


def test_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    for i in range(5):
        ns.append("A", str(i), "")
    assert len(ns.read(limit=2)) == 2


def test_since_seconds_filters_old(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    ns.append("A", "old", "")
    p = ns._path()
    rec = json.loads(p.read_text().splitlines()[0])
    rec["ts"] = time.time() - 7200  # 2h ago
    p.write_text(json.dumps(rec) + "\n")
    ns.append("A", "new", "")
    recs = ns.read(limit=10, since_seconds=3600)  # last hour only
    assert [r["summary"] for r in recs] == ["new"]


def test_missing_store_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "nope"))
    assert ns.read() == []


def test_skips_garbage_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    ns.append("A", "ok", "")
    with open(ns._path(), "a") as f:
        f.write("{ not json\n")
    assert [r["summary"] for r in ns.read()] == ["ok"]


def test_prune_bounds_file(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(ns, "MAX_KEEP", 5)
    for i in range(30):
        ns.append("A", str(i), "")
    assert len(ns._path().read_text().splitlines()) <= 10  # bounded by 2x cap
    assert ns.read(limit=100)[0]["summary"] == "29"  # newest survives

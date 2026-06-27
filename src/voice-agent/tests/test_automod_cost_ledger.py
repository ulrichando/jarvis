"""Tests for the evolution cost ledger (the spend brake)."""
import json

import pipeline.automod.cost_ledger as cl


def test_record_and_sum_today(tmp_path, monkeypatch):
    p = tmp_path / "cost-ledger.json"
    monkeypatch.setattr(cl, "cost_ledger_path", lambda: p)
    assert cl.spent_today() == 0.0
    cl.record("b1", 1.25)
    cl.record("b2", 0.75)
    assert round(cl.spent_today(), 2) == 2.00


def test_rollover_resets(tmp_path, monkeypatch):
    p = tmp_path / "cost-ledger.json"
    p.write_text(json.dumps({"date": "2000-01-01", "entries": [{"id": "x", "cost_usd": 9.0}]}))
    monkeypatch.setattr(cl, "cost_ledger_path", lambda: p)
    assert cl.spent_today() == 0.0  # stale day ignored


def test_daily_usd_env(monkeypatch):
    monkeypatch.setenv("JARVIS_EVOLUTION_DAILY_USD", "12.5")
    assert cl.daily_usd() == 12.5
    monkeypatch.delenv("JARVIS_EVOLUTION_DAILY_USD", raising=False)
    assert cl.daily_usd() == 6.0


def test_record_survives_garbage_file(tmp_path, monkeypatch):
    p = tmp_path / "cost-ledger.json"
    p.write_text("{ not json")
    monkeypatch.setattr(cl, "cost_ledger_path", lambda: p)
    cl.record("b1", 2.0)  # must not raise
    assert round(cl.spent_today(), 2) == 2.00

from __future__ import annotations

from evolution import ledger


def test_append_then_read_roundtrip(tmp_path):
    db = tmp_path / "evo.db"
    rid = ledger.append_reading(
        ts_utc="2026-05-30T00:00:00Z",
        window_start="2026-05-30T00:00:00Z",
        window_end="2026-05-30T01:00:00Z",
        n_turns=10,
        per_axis={"reask": 1.0, "confab": 0.9},
        composite=0.95,
        guardrail_flags={"reask": False, "confab": False},
        passed=True,
        candidate_id="cand-1",
        db_path=db,
    )
    assert isinstance(rid, int) and rid > 0
    rows = ledger.read_readings(db_path=db)
    assert len(rows) == 1
    row = rows[0]
    assert row["n_turns"] == 10
    assert row["composite"] == 0.95
    assert row["per_axis"] == {"reask": 1.0, "confab": 0.9}
    assert row["guardrail_flags"] == {"reask": False, "confab": False}
    assert row["candidate_id"] == "cand-1"
    assert row["passed"] is True  # proves 1/0 -> bool coercion


def test_append_only_no_mutation_api():
    assert not hasattr(ledger, "update_reading")
    assert not hasattr(ledger, "delete_reading")


def test_read_missing_db_returns_empty(tmp_path):
    assert ledger.read_readings(db_path=tmp_path / "none.db") == []


def test_init_is_idempotent(tmp_path):
    db = tmp_path / "evo.db"
    ledger.init_ledger(db)
    ledger.init_ledger(db)  # second call must not error

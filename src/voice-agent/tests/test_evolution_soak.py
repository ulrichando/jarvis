from __future__ import annotations

from datetime import datetime, timedelta, timezone

from evolution import soak, ledger


def test_previous_local_day_window_utc_edt():
    # noon EDT (UTC-4) on 2026-05-30
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
    since, until = soak.previous_local_day_window_utc(now)
    assert since == "2026-05-29T04:00:00Z"     # local 2026-05-29 00:00 EDT
    assert until == "2026-05-30T03:59:59Z"     # local 2026-05-29 23:59:59 EDT


def test_previous_local_day_window_utc_format_is_z():
    now = datetime(2026, 1, 15, 9, 30, 0, tzinfo=timezone(timedelta(hours=-5)))  # EST
    since, until = soak.previous_local_day_window_utc(now)
    assert since.endswith("Z") and until.endswith("Z")
    assert since == "2026-01-14T05:00:00Z" and until == "2026-01-15T04:59:59Z"


def _fixed_now():
    return datetime(2026, 5, 30, 2, 30, 0, tzinfo=timezone(timedelta(hours=-4)))


def test_run_soak_logs_then_dedups(tmp_path):
    led = tmp_path / "led.db"
    tel = tmp_path / "none.db"          # absent telemetry → empty-window reading
    r1 = soak.run_soak(now=_fixed_now(), telemetry_db=tel, ledger_db=led, gate_on=True)
    assert r1["action"] == "logged" and r1["reading_id"] is not None
    r2 = soak.run_soak(now=_fixed_now(), telemetry_db=tel, ledger_db=led, gate_on=True)
    assert r2["action"] == "already_logged"
    assert len(ledger.read_readings(db_path=led)) == 1     # exactly one row


def test_run_soak_writes_only_under_gate(tmp_path):
    led = tmp_path / "led.db"
    tel = tmp_path / "none.db"
    r_off = soak.run_soak(now=_fixed_now(), telemetry_db=tel, ledger_db=led, gate_on=False)
    assert r_off["action"] == "no_gate" and r_off["reading_id"] is None
    assert ledger.read_readings(db_path=led) == []         # nothing written
    r_on = soak.run_soak(now=_fixed_now(), telemetry_db=tel, ledger_db=led, gate_on=True)
    assert r_on["action"] == "logged"
    assert len(ledger.read_readings(db_path=led)) == 1


def test_run_soak_window_matches_previous_local_day(tmp_path):
    r = soak.run_soak(now=_fixed_now(), telemetry_db=tmp_path / "none.db",
                      ledger_db=tmp_path / "led.db", gate_on=False)
    assert r["since"] == "2026-05-29T04:00:00Z"
    assert r["until"] == "2026-05-30T03:59:59Z"

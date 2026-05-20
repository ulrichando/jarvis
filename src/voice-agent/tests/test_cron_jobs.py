import pytest
from datetime import datetime, timedelta
from pipeline import cron_jobs as cj


def _now():
    return datetime(2026, 5, 20, 7, 0, 0).astimezone()


def test_parse_interval():
    assert cj.parse_schedule("every 30m") == {"kind": "interval", "every_s": 1800}


def test_parse_daily_at():
    assert cj.parse_schedule("daily at 08:00") == {"kind": "daily-at", "at": "08:00"}
    assert cj.parse_schedule("every day at 8am") == {"kind": "daily-at", "at": "08:00"}


def test_parse_duration_oneshot():
    s = cj.parse_schedule("in 2h", _now=_now())
    assert s["kind"] == "once"
    assert datetime.fromisoformat(s["run_at"]) == _now() + timedelta(hours=2)


def test_parse_rejects_subminute_interval():
    with pytest.raises(ValueError):
        cj.parse_schedule("every 10s")


def test_parse_unrecognized_raises():
    with pytest.raises(ValueError):
        cj.parse_schedule("whenever I feel like it")


def test_next_run_daily_at_rolls_forward():
    nxt = cj.compute_next_run({"kind": "daily-at", "at": "08:00"}, _now=_now())
    assert datetime.fromisoformat(nxt).hour == 8
    later = _now().replace(hour=9)
    nxt2 = cj.compute_next_run({"kind": "daily-at", "at": "08:00"}, _now=later)
    expected = later.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    assert datetime.fromisoformat(nxt2) == expected


def test_next_run_once_consumed_after_run():
    sched = {"kind": "once", "run_at": _now().isoformat()}
    assert cj.compute_next_run(sched, _now=_now(), last_run_at=None) == sched["run_at"]
    assert cj.compute_next_run(sched, _now=_now(), last_run_at=_now().isoformat()) is None


def test_next_run_interval_from_last():
    last = _now().isoformat()
    nxt = cj.compute_next_run({"kind": "interval", "every_s": 3600}, _now=_now(), last_run_at=last)
    assert datetime.fromisoformat(nxt) == _now() + timedelta(hours=1)


def test_next_run_once_stale_returns_none():
    stale = {"kind": "once", "run_at": (_now() - timedelta(minutes=10)).isoformat()}
    assert cj.compute_next_run(stale, _now=_now(), last_run_at=None) is None


def test_parse_iso_timestamp_oneshot():
    s = cj.parse_schedule("2026-05-21T09:00")
    assert s["kind"] == "once"
    assert datetime.fromisoformat(s["run_at"]).hour == 9

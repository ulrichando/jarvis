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

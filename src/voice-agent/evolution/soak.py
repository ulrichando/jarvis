"""Evolution calibration soak — daily fitness logging + trend review.

Pure window math + thin composition over the read-only reader, fitness, and the
append-only ledger. No import-time side effects; not imported by the voice-agent
runtime. The gate write is env-gated identically to `score --log`.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone


def previous_local_day_window_utc(now: datetime) -> tuple[str, str]:
    """Bounds of the *previous local calendar day* as UTC 'YYYY-MM-DDTHH:MM:SSZ'
    strings, inclusive. `now` MUST be timezone-aware; the day is computed in `now`'s
    own tz (so the result is deterministic and machine-independent), then converted
    to UTC to match telemetry's `ts_utc`."""
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    y_start = today_start - timedelta(days=1)
    y_end = today_start - timedelta(seconds=1)
    def _z(d: datetime) -> str:
        return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _z(y_start), _z(y_end)

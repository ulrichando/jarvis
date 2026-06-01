"""Evolution calibration soak — daily fitness logging + trend review.

Pure window math + thin composition over the read-only reader, fitness, and the
append-only ledger. No import-time side effects; not imported by the voice-agent
runtime. The gate write is env-gated identically to `score --log`.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from . import db_read, ledger as _ledger, backtest as _backtest


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


def run_soak(*, now: datetime, telemetry_db=db_read.DEFAULT_TELEMETRY_DB,
             ledger_db=_ledger.DEFAULT_LEDGER_DB, gate_on: bool) -> dict:
    """Score the previous local day and (if gate_on) append one ledger reading.
    Idempotent per window via reading_exists. Returns a result dict describing what
    happened: action ∈ {already_logged, no_gate, logged}."""
    since, until = previous_local_day_window_utc(now)
    base = {"since": since, "until": until, "reading_id": None,
            "composite": None, "passed": None, "n_turns": None}
    if _ledger.reading_exists(window_start=since, window_end=until, db_path=ledger_db):
        return {**base, "action": "already_logged"}
    reading = _backtest.score_window(telemetry_db, since=since, until=until)
    base.update(composite=reading.composite, passed=reading.passed, n_turns=reading.n_turns)
    if not gate_on:
        return {**base, "action": "no_gate"}
    ts = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rid = _ledger.append_reading(
        ts_utc=ts, window_start=since, window_end=until, n_turns=reading.n_turns,
        per_axis=reading.per_axis, composite=reading.composite,
        guardrail_flags=reading.guardrail_flags, passed=reading.passed, db_path=ledger_db)
    return {**base, "action": "logged", "reading_id": rid}


def format_trend_table(readings: list[dict]) -> str:
    """Render ledger readings (newest-first, as read_readings returns) as an aligned
    per-axis table. Pure: takes dicts, returns a string. DATE = window_start[:10]
    (UTC date == local date for west-of-UTC offsets, e.g. this EDT/EST box)."""
    header = (f"{'DATE':<11} {'n':>4} {'comp':>7}  "
              f"{'reask':>6} {'confab':>6} {'lat':>6} {'action':>6} {'intr':>6}  RESULT")
    lines = [header]
    for r in readings:
        ws = r.get("window_start")
        date = ws[:10] if ws else "(all)"
        ax = r.get("per_axis") or {}

        def _fmt(key: str) -> str:
            v = ax.get(key)
            return f"{v:.3f}" if isinstance(v, (int, float)) else "-"

        lines.append(
            f"{date:<11} {r.get('n_turns', 0):>4} {r.get('composite', 0.0):>7.4f}  "
            f"{_fmt('reask'):>6} {_fmt('confab'):>6} {_fmt('latency'):>6} "
            f"{_fmt('action'):>6} {_fmt('interruption'):>6}  "
            f"{'PASS' if r.get('passed') else 'FAIL'}")
    return "\n".join(lines)

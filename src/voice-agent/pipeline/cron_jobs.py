"""JARVIS scheduler job store (phase 1).

Schedule kinds: "once" | "interval" | "daily-at". Persistence in
~/.jarvis/cron/jobs.json (atomic, 0600). Adapted + simplified from
hermes/cron/jobs.py — phase 1 drops cron expressions, skills, model
override, workdir, and profiles. All time logic takes an injected
`_now` so it's deterministic in tests.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
logger = logging.getLogger("jarvis.cron_jobs")

CRON_DIR = Path.home() / ".jarvis" / "cron"
JOBS_FILE = CRON_DIR / "jobs.json"
OUTPUT_DIR = CRON_DIR / "output"
PENDING_FILE = CRON_DIR / "pending.jsonl"

MAX_JOBS = int(os.environ.get("JARVIS_CRON_MAX_JOBS", "50"))
MIN_INTERVAL_S = 60
ONESHOT_GRACE_S = 120

_UNIT_S = {"s": 1, "m": 60, "h": 3600}
_INTERVAL_RE = re.compile(r"^every\s+(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours)$", re.I)
_DAILY_RE = re.compile(r"^(?:every\s+day\s+at|daily\s+at|at)\s+(.+)$", re.I)
_DUR_RE = re.compile(r"^(?:in\s+)?(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours)$", re.I)
_HHMM_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", re.I)


def now() -> datetime:
    return datetime.now().astimezone()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.astimezone()


def _unit_seconds(n: int, unit: str) -> int:
    return n * _UNIT_S[unit[0].lower()]


def _parse_hhmm(s: str) -> str:
    m = _HHMM_RE.match(s.strip())
    if not m:
        raise ValueError(f"Unrecognized time-of-day: {s!r}. Use 'HH:MM', '8am', '8:30pm'.")
    hh = int(m.group(1))
    mm = int(m.group(2) or 0)
    ampm = (m.group(3) or "").lower()
    if ampm == "pm" and hh != 12:
        hh += 12
    elif ampm == "am" and hh == 12:
        hh = 0
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"Time out of range: {s!r}")
    return f"{hh:02d}:{mm:02d}"


def parse_schedule(text: str, *, _now: datetime | None = None) -> dict:
    """Parse a human/config schedule string into a schedule dict.

    Accepts: 'every 30m' (interval), 'daily at 08:00' / 'every day at 8am' /
    'at 8am' (daily-at), 'in 2h' / '30m' (one-shot from now), ISO timestamp (one-shot).
    Raises ValueError with examples on anything unrecognized.
    """
    t = (text or "").strip()
    now_ = _now or now()

    m = _INTERVAL_RE.match(t)
    if m:
        every_s = _unit_seconds(int(m.group(1)), m.group(2))
        if every_s < MIN_INTERVAL_S:
            raise ValueError(f"Interval must be ≥ {MIN_INTERVAL_S}s (got {every_s}s).")
        return {"kind": "interval", "every_s": every_s}

    m = _DAILY_RE.match(t)
    if m:
        return {"kind": "daily-at", "at": _parse_hhmm(m.group(1))}

    if "T" in t or re.match(r"^\d{4}-\d{2}-\d{2}", t):
        try:
            dt = _aware(datetime.fromisoformat(t.replace("Z", "+00:00")))
            return {"kind": "once", "run_at": dt.isoformat()}
        except ValueError as e:
            raise ValueError(f"Invalid timestamp {t!r}: {e}") from e

    m = _DUR_RE.match(t)
    if m:
        run_at = now_ + timedelta(seconds=_unit_seconds(int(m.group(1)), m.group(2)))
        return {"kind": "once", "run_at": run_at.isoformat()}

    raise ValueError(
        f"Unrecognized schedule {text!r}. Examples: 'every 30m', "
        f"'daily at 08:00', 'in 2h', '2026-05-21T09:00'."
    )


def compute_next_run(schedule: dict, *, _now: datetime | None = None,
                     last_run_at: str | None = None) -> str | None:
    """Next ISO run time, or None if the schedule has no more runs."""
    now_ = _now or now()
    kind = schedule.get("kind")

    if kind == "once":
        if last_run_at:
            return None
        run_at = _aware(datetime.fromisoformat(schedule["run_at"]))
        if run_at >= now_ - timedelta(seconds=ONESHOT_GRACE_S):
            return schedule["run_at"]
        return None

    if kind == "interval":
        base = _aware(datetime.fromisoformat(last_run_at)) if last_run_at else now_
        return (base + timedelta(seconds=schedule["every_s"])).isoformat()

    if kind == "daily-at":
        hh, mm = (int(x) for x in schedule["at"].split(":"))
        cand = now_.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand <= now_:
            cand += timedelta(days=1)
        return cand.isoformat()

    return None

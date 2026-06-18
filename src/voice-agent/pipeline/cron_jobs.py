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


import contextlib
import functools
import time as _time

try:
    import fcntl
except ImportError:  # Windows has no fcntl — _store_lock skips locking there
    fcntl = None

_VALID_DELIVERY = {"notify", "voice", "notify+voice", "local"}


def ensure_dirs() -> None:
    CRON_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for p in (CRON_DIR, OUTPUT_DIR):
        try:
            os.chmod(p, 0o700)
        except OSError:
            pass


@contextlib.contextmanager
def _store_lock():
    """Cross-process exclusive lock around read-modify-write of jobs.json.
    The systemd jarvis-cron.timer process (ticking) and the voice daemon's
    schedule tools both mutate the store; this serializes them so a
    concurrent write can't lose the other's update. CRON_DIR is read at call
    time so test monkeypatching of the path is honored."""
    ensure_dirs()
    if fcntl is None:  # Windows: only the voice agent mutates the store (no
        yield          # systemd cron timer), so there's no cross-process race.
        return
    # Empty lock file; encoding is harmless but quiets the cross-platform
    # checker (Windows defaults to cp1252 otherwise).
    f = open(CRON_DIR / ".store.lock", "w", encoding="utf-8")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        f.close()  # releasing the fd releases the flock


def _locked(fn):
    """Wrap a store mutator so its load-modify-save runs under _store_lock."""
    @functools.wraps(fn)
    def wrap(*args, **kwargs):
        with _store_lock():
            return fn(*args, **kwargs)
    return wrap


def new_job(*, name: str, type: str, schedule: dict, command: str | None = None,
            prompt: str | None = None, delivery: str = "notify+voice",
            created_by: str = "config") -> dict:
    """Build a job dict (not yet persisted). Voice-created jobs are gated
    behind pending_confirm/enabled=False until confirm_schedule fires."""
    scan_job_content(f"{name}\n{command or ''}\n{prompt or ''}")
    if type not in ("script", "prompt"):
        raise ValueError(f"type must be 'script' or 'prompt' (got {type!r})")
    if type == "script" and not command:
        raise ValueError("script jobs require a command")
    if type == "prompt" and not prompt:
        raise ValueError("prompt jobs require a prompt")
    if delivery not in _VALID_DELIVERY:
        raise ValueError(f"delivery must be one of {_VALID_DELIVERY}")
    voice = created_by == "voice"
    return {
        "id": uuid.uuid4().hex[:12], "name": name, "type": type,
        "command": command, "prompt": prompt, "schedule": schedule,
        "delivery": delivery, "enabled": not voice, "pending_confirm": voice,
        "created_by": created_by, "created_ts": _time.time(),
        "next_run_at": compute_next_run(schedule), "last_run_at": None,
        "run_count": 0, "consecutive_failures": 0,
    }


def load_jobs() -> list[dict]:
    ensure_dirs()
    if not JOBS_FILE.exists():
        return []
    try:
        return json.loads(JOBS_FILE.read_text("utf-8")).get("jobs", [])
    except (json.JSONDecodeError, OSError) as e:
        logger.error("[cron] jobs.json unreadable (%s); treating as empty", e)
        return []


def save_jobs(jobs: list[dict]) -> None:
    ensure_dirs()
    fd, tmp = tempfile.mkstemp(dir=str(JOBS_FILE.parent), prefix=".jobs_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"jobs": jobs}, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, JOBS_FILE)
        os.chmod(JOBS_FILE, 0o600)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@_locked
def add_job(job: dict) -> dict:
    jobs = load_jobs()
    if len(jobs) >= MAX_JOBS:
        raise ValueError(f"Job limit reached ({MAX_JOBS}). Remove a job first.")
    jobs.append(job)
    save_jobs(jobs)
    return job


def get_job(job_id: str) -> dict | None:
    return next((j for j in load_jobs() if j["id"] == job_id), None)


@_locked
def remove_job(job_id: str) -> bool:
    jobs = load_jobs()
    kept = [j for j in jobs if j["id"] != job_id]
    if len(kept) == len(jobs):
        return False
    save_jobs(kept)
    return True


@_locked
def _mutate(job_id: str, **changes) -> dict | None:
    jobs = load_jobs()
    for j in jobs:
        if j["id"] == job_id:
            j.update(changes)
            save_jobs(jobs)
            return j
    return None


def set_enabled(job_id: str, enabled: bool) -> dict | None:
    return _mutate(job_id, enabled=enabled)


def set_confirmed(job_id: str) -> dict | None:
    return _mutate(job_id, pending_confirm=False, enabled=True)


@_locked
def get_due_jobs(*, _now: datetime | None = None) -> list[dict]:
    """Jobs eligible to run: enabled, confirmed, with next_run_at <= now.
    Recurring jobs whose time is stale by > 2x their period are fast-forwarded
    (skipped this tick) so a downed daemon doesn't fire a burst on restart."""
    now_ = _now or now()
    due: list[dict] = []
    dirty = False
    jobs = load_jobs()
    for j in jobs:
        if not j.get("enabled") or j.get("pending_confirm"):
            continue
        nra = j.get("next_run_at")
        if not nra:
            continue
        nrt = _aware(datetime.fromisoformat(nra))
        if nrt > now_:
            continue
        kind = j["schedule"].get("kind")
        if kind in ("interval", "daily-at"):
            period = j["schedule"].get("every_s", 86400)
            if (now_ - nrt).total_seconds() > 2 * period:
                j["next_run_at"] = compute_next_run(j["schedule"], _now=now_)
                dirty = True
                continue
        due.append(j)
    if dirty:
        save_jobs(jobs)
    return due


@_locked
def advance_next_run(job_id: str, *, _now: datetime | None = None) -> bool:
    """Advance a recurring job's next_run_at BEFORE running it (at-most-once).
    One-shot jobs are left unchanged so they can retry after a crash."""
    now_ = _now or now()
    jobs = load_jobs()
    for j in jobs:
        if j["id"] == job_id and j["schedule"].get("kind") in ("interval", "daily-at"):
            j["next_run_at"] = compute_next_run(j["schedule"], _now=now_, last_run_at=now_.isoformat())
            save_jobs(jobs)
            return True
    return False


@_locked
def mark_job_run(job_id: str, *, ok: bool, _now: datetime | None = None) -> None:
    """Record a run. Bumps counters; recomputes next_run_at; auto-disables a
    one-shot after it fires and any job after N consecutive failures."""
    now_ = _now or now()
    max_fail = int(os.environ.get("JARVIS_CRON_MAX_FAILURES", "3"))
    jobs = load_jobs()
    for j in jobs:
        if j["id"] != job_id:
            continue
        j["last_run_at"] = now_.isoformat()
        j["run_count"] = j.get("run_count", 0) + 1
        j["consecutive_failures"] = 0 if ok else j.get("consecutive_failures", 0) + 1
        if j["schedule"].get("kind") == "once":
            j["enabled"] = False
        else:
            j["next_run_at"] = compute_next_run(j["schedule"], _now=now_, last_run_at=now_.isoformat())
        if not ok and j["consecutive_failures"] >= max_fail:
            j["enabled"] = False
            logger.warning("[cron] job %s auto-disabled after %d failures", job_id, j["consecutive_failures"])
        save_jobs(jobs)
        return


# Ported from hermes/tools/memory_tool.py::_scan_memory_content — patterns
# that should never appear in an autonomously-stored, re-injected job.
_INJECTION_RES = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
    re.compile(r"\b(you\s+are\s+now|jailbreak|DAN[\s_-]?mode)\b", re.I),
    re.compile(r"disregard\s+(the\s+)?(system|above)", re.I),
    re.compile(r"curl\s+[^\n]*\$\(", re.I),                       # exfil via command-substitution
    re.compile(r"\b(wget|curl)\b[^\n]*(\.env\b|id_rsa|\.ssh/|\.aws/|\bcredentials\b)", re.I),
    re.compile(r"[​‌‍⁠﻿]"),              # invisible unicode
]


def scan_job_content(text: str) -> None:
    """Raise ValueError if `text` matches a prompt-injection / exfil pattern."""
    for rx in _INJECTION_RES:
        if rx.search(text or ""):
            raise ValueError(f"Job content rejected by safety scan (matched {rx.pattern!r}).")

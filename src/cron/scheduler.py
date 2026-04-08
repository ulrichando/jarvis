"""JARVIS cron scheduler.

Supports three schedule types (mirroring OpenClaw's cron service):

  1. ``at``    — one-shot at an absolute ISO timestamp
  2. ``every`` — repeat every N seconds (``every: 300`` = every 5 min)
  3. ``cron``  — standard 5-field cron expression (minute hour dom month dow)

Deterministic stagger (from OpenClaw src/cron/service.ts):
  To prevent thundering-herd on multi-agent systems, each job's first
  fire is offset by ``SHA256(job_id) % stagger_ms`` milliseconds.
  This is deterministic — the same job always gets the same offset.

Persistence:
  Jobs are optionally persisted to ``.jarvis/scheduled_tasks.json``
  (durable=True).  Session-only jobs (durable=False) vanish on restart.

Usage:
    from src.cron.scheduler import get_scheduler

    sched = get_scheduler()
    await sched.start()

    job_id = sched.add_job(
        prompt="say good morning",
        cron="0 9 * * *",
        recurring=True,
        durable=True,
        label="Morning greeting",
    )

    sched.remove_job(job_id)
    sched.list_jobs()
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine

log = logging.getLogger("jarvis.cron")

# ── Cron expression parsing (minimal 5-field parser) ─────────────────────────

def _next_cron_ts(expr: str, after: float | None = None) -> float:
    """Return the next UTC timestamp at which a 5-field cron expression fires.

    Only handles simple ``*``, ``*/N``, and specific numeric values.
    For complex expressions install the optional ``croniter`` package.
    """
    try:
        from croniter import croniter  # type: ignore
        base = after or time.time()
        it = croniter(expr, base)
        return it.get_next(float)
    except ImportError:
        pass

    # Fallback: align to next whole minute boundary + crude field checks.
    import datetime as dt
    now = dt.datetime.utcnow() if after is None else dt.datetime.utcfromtimestamp(after)
    # Advance by 1 minute and try up to 60*24*366 minutes
    candidate = now.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression (need 5 fields): {expr!r}")
    f_min, f_hour, f_dom, f_month, f_dow = parts

    def _matches(value: int, spec: str) -> bool:
        if spec == "*":
            return True
        if spec.startswith("*/"):
            step = int(spec[2:])
            return value % step == 0
        return value == int(spec)

    for _ in range(60 * 24 * 400):
        # cron DOW: 0=Sunday…6=Saturday; Python weekday(): 0=Monday…6=Sunday
        _cron_dow = (candidate.weekday() + 1) % 7
        if (
            _matches(candidate.minute, f_min)
            and _matches(candidate.hour, f_hour)
            and _matches(candidate.day, f_dom)
            and _matches(candidate.month, f_month)
            and _matches(_cron_dow, f_dow)
        ):
            return candidate.timestamp()
        candidate += dt.timedelta(minutes=1)

    raise ValueError(f"Could not find next fire time for: {expr!r}")


# ── Deterministic stagger ─────────────────────────────────────────────────────

_DEFAULT_STAGGER_MS = 60_000  # 60 s window


def _stagger_offset_ms(job_id: str, stagger_ms: int = _DEFAULT_STAGGER_MS) -> int:
    """Return a deterministic offset in milliseconds for *job_id*.

    SHA256(job_id) % stagger_ms — same job always gets the same offset,
    so re-registering after a restart doesn't change firing times.
    """
    digest = hashlib.sha256(job_id.encode()).digest()
    value = int.from_bytes(digest[:8], "big")
    return value % stagger_ms


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class CronJob:
    id: str
    prompt: str
    # Schedule: exactly one of cron_expr / every_seconds / at_timestamp must be set
    cron_expr: str = ""          # 5-field cron
    every_seconds: float = 0.0   # repeat interval
    at_timestamp: float = 0.0    # one-shot UTC timestamp
    # Options
    recurring: bool = True
    durable: bool = False
    label: str = ""
    max_age_days: int = 30
    stagger_ms: int = _DEFAULT_STAGGER_MS
    # Runtime state (not persisted)
    next_fire: float = field(default=0.0, compare=False)
    created_at: float = field(default_factory=time.time, compare=False)
    last_fired: float = field(default=0.0, compare=False)
    fire_count: int = field(default=0, compare=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Drop runtime-only fields for persistence
        for key in ("next_fire", "last_fired", "fire_count"):
            d.pop(key, None)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "CronJob":
        # Strip unknown keys for forward-compat
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


# ── Scheduler ─────────────────────────────────────────────────────────────────


class CronScheduler:
    """Async cron scheduler with persistence and deterministic stagger."""

    def __init__(
        self,
        dispatch: Callable[[str], Coroutine[Any, Any, str]] | None = None,
        persist_path: Path | None = None,
    ) -> None:
        """
        Args:
            dispatch:      Async callable ``(prompt) → response_str`` that
                           executes the job's prompt.  Typically
                           ``brain.think_stream`` or similar.
            persist_path:  Where durable jobs are stored.  Defaults to
                           ``~/.jarvis/scheduled_tasks.json``.
        """
        self._dispatch = dispatch
        self._jobs: dict[str, CronJob] = {}
        self._task: asyncio.Task | None = None
        self._persist_path = persist_path or (
            Path.home() / ".jarvis" / "scheduled_tasks.json"
        )
        self._load_durable()

    # ── Public API ────────────────────────────────────────────────────

    def add_job(
        self,
        prompt: str,
        *,
        cron: str = "",
        every: float = 0.0,
        at: float = 0.0,
        recurring: bool = True,
        durable: bool = False,
        label: str = "",
        max_age_days: int = 30,
        job_id: str | None = None,
    ) -> str:
        """Create and register a new cron job.  Returns the job ID."""
        if not (cron or every or at):
            raise ValueError("Provide one of: cron=, every=, at=")

        jid = job_id or str(uuid.uuid4())
        job = CronJob(
            id=jid,
            prompt=prompt,
            cron_expr=cron,
            every_seconds=every,
            at_timestamp=at,
            recurring=recurring,
            durable=durable,
            label=label or prompt[:60],
            max_age_days=max_age_days,
        )
        job.next_fire = self._compute_next(job, stagger=True)
        self._jobs[jid] = job

        if durable:
            self._save_durable()

        log.info("Cron job added: %s [%s] next=%s", jid, label or prompt[:40],
                 time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(job.next_fire)))
        return jid

    def remove_job(self, job_id: str) -> bool:
        """Cancel and remove a job.  Returns True if it existed."""
        if job_id not in self._jobs:
            return False
        del self._jobs[job_id]
        self._save_durable()
        log.info("Cron job removed: %s", job_id)
        return True

    def list_jobs(self) -> list[dict]:
        """Return a summary dict for each registered job."""
        out = []
        for job in self._jobs.values():
            out.append({
                "id": job.id,
                "label": job.label,
                "prompt": job.prompt[:80],
                "schedule": (
                    job.cron_expr
                    or (f"every {job.every_seconds}s" if job.every_seconds else "")
                    or (f"at {job.at_timestamp}" if job.at_timestamp else "unknown")
                ),
                "recurring": job.recurring,
                "durable": job.durable,
                "next_fire": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(job.next_fire)),
                "fire_count": job.fire_count,
            })
        return out

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background scheduler loop."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="jarvis-cron")
        log.info("Cron scheduler started (%d job(s) loaded)", len(self._jobs))

    async def stop(self) -> None:
        """Stop the scheduler loop."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("Cron scheduler stopped")

    # ── Internal ──────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while True:
            now = time.time()
            fired: list[str] = []

            for jid, job in list(self._jobs.items()):
                if now < job.next_fire:
                    continue
                fired.append(jid)
                asyncio.create_task(self._fire(job), name=f"cron-{jid}")

            for jid in fired:
                job = self._jobs.get(jid)
                if job is None:
                    continue
                job.last_fired = now
                job.fire_count += 1

                if not job.recurring or job.at_timestamp:
                    # One-shot — remove after firing
                    del self._jobs[jid]
                    self._save_durable()
                elif self._is_expired(job):
                    log.info("Cron job expired (max_age): %s", jid)
                    del self._jobs[jid]
                    self._save_durable()
                else:
                    job.next_fire = self._compute_next(job, stagger=False)

            await asyncio.sleep(5)  # poll every 5 seconds

    async def _fire(self, job: CronJob) -> None:
        log.info("Cron job firing: %s  prompt=%r", job.id, job.prompt[:60])
        if self._dispatch is None:
            log.warning("No dispatch function — cron job %s dropped", job.id)
            return
        try:
            await self._dispatch(job.prompt)
        except Exception as e:
            log.error("Cron job %s failed: %s", job.id, e)

    def _compute_next(self, job: CronJob, stagger: bool = False) -> float:
        now = time.time()
        offset = (_stagger_offset_ms(job.id, job.stagger_ms) / 1000.0) if stagger else 0.0

        if job.at_timestamp:
            return job.at_timestamp + offset

        if job.every_seconds:
            base = job.last_fired or now
            return base + job.every_seconds + offset

        if job.cron_expr:
            return _next_cron_ts(job.cron_expr, after=now) + offset

        return now + 60 + offset

    def _is_expired(self, job: CronJob) -> bool:
        if not job.max_age_days:
            return False
        age_s = time.time() - job.created_at
        return age_s > job.max_age_days * 86_400

    # ── Persistence ───────────────────────────────────────────────────

    def _save_durable(self) -> None:
        durable = [j.to_dict() for j in self._jobs.values() if j.durable]
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(
                json.dumps({"jobs": durable}, indent=2), encoding="utf-8"
            )
        except OSError as e:
            log.error("Failed to save cron jobs: %s", e)

    def _load_durable(self) -> None:
        if not self._persist_path.exists():
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            for jd in data.get("jobs", []):
                job = CronJob.from_dict(jd)
                job.next_fire = self._compute_next(job, stagger=False)
                self._jobs[job.id] = job
            log.info("Loaded %d durable cron job(s)", len(self._jobs))
        except Exception as e:
            log.warning("Failed to load cron jobs: %s", e)


# ── Singleton ─────────────────────────────────────────────────────────────────

_scheduler: CronScheduler | None = None


def get_scheduler(
    dispatch: Callable[[str], Coroutine[Any, Any, str]] | None = None,
) -> CronScheduler:
    """Return the global CronScheduler singleton, creating it if needed."""
    global _scheduler
    if _scheduler is None:
        _scheduler = CronScheduler(dispatch=dispatch)
    elif dispatch is not None and _scheduler._dispatch is None:
        _scheduler._dispatch = dispatch
    return _scheduler

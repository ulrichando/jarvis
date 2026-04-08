"""CronCreateTool — schedules a new cron job via the JARVIS cron scheduler."""
from __future__ import annotations

from typing import Any

from src.tools.ScheduleCronTool.prompt import CRON_CREATE_TOOL_NAME


async def execute_cron_create(
    cron: str = "",
    prompt: str = "",
    every: float = 0.0,
    at: float = 0.0,
    recurring: bool = True,
    durable: bool = False,
    label: str = "",
    max_age_days: int = 30,
    **kwargs: Any,
) -> dict[str, Any]:
    """Create a cron job and register it with the scheduler."""
    from src.cron.scheduler import get_scheduler

    sched = get_scheduler()

    try:
        job_id = sched.add_job(
            prompt=prompt,
            cron=cron,
            every=every,
            at=at,
            recurring=recurring,
            durable=durable,
            label=label,
            max_age_days=max_age_days,
        )
        return {"job_id": job_id, "cron": cron, "label": label or prompt[:60]}
    except Exception as e:
        return {"error": str(e), "job_id": ""}

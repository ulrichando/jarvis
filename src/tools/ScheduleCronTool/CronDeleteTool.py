"""CronDeleteTool — cancels a scheduled cron job."""
from __future__ import annotations
from typing import Any
from src.tools.ScheduleCronTool.prompt import CRON_DELETE_TOOL_NAME


async def execute_cron_delete(job_id: str, **kwargs: Any) -> dict[str, Any]:
    """Delete a cron job by ID."""
    from src.cron.scheduler import get_scheduler
    sched = get_scheduler()
    deleted = sched.remove_job(job_id)
    return {"job_id": job_id, "deleted": deleted}

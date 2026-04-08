"""CronListTool — lists all scheduled cron jobs."""
from __future__ import annotations
from typing import Any
from src.tools.ScheduleCronTool.prompt import CRON_LIST_TOOL_NAME


async def execute_cron_list(**kwargs: Any) -> dict[str, Any]:
    """List all registered cron jobs."""
    from src.cron.scheduler import get_scheduler
    sched = get_scheduler()
    return {"jobs": sched.list_jobs()}

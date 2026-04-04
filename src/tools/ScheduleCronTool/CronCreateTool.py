"""CronCreateTool -- schedules a new cron job."""
from __future__ import annotations
from typing import Any
from src.tools.ScheduleCronTool.prompt import CRON_CREATE_TOOL_NAME


async def execute_cron_create(cron: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
    """Create a cron job. Stub."""
    return {"job_id": "", "cron": cron}

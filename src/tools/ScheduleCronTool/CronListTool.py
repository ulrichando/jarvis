"""CronListTool -- lists scheduled cron jobs."""
from __future__ import annotations
from typing import Any
from src.tools.ScheduleCronTool.prompt import CRON_LIST_TOOL_NAME


async def execute_cron_list(**kwargs: Any) -> dict[str, Any]:
    """List cron jobs. Stub."""
    return {"jobs": []}

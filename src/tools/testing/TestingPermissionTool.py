"""TestingPermissionTool -- tool for testing permission flows."""
from __future__ import annotations
from typing import Any

TESTING_PERMISSION_TOOL_NAME = "TestingPermission"


async def execute_testing_permission(**kwargs: Any) -> dict[str, Any]:
    """Testing tool. Not for production use."""
    return {"status": "ok"}

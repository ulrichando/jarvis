"""PowerShellTool -- Windows PowerShell execution. Stub for Linux."""
from __future__ import annotations
from typing import Any
from src.tools.PowerShellTool.toolName import POWERSHELL_TOOL_NAME


async def execute_powershell(command: str, **kwargs: Any) -> dict[str, Any]:
    """Execute a PowerShell command. Not available on Linux."""
    return {
        "error": "PowerShell is not available on this platform",
        "exit_code": 1,
    }

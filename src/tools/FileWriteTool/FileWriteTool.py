"""FileWriteTool -- writes files."""
from __future__ import annotations
from typing import Any
from src.tools.FileWriteTool.prompt import FILE_WRITE_TOOL_NAME


async def execute_file_write(file_path: str, content: str, **kwargs: Any) -> dict[str, Any]:
    """Write a file. Actual implementation in brain/agent/tools.py."""
    raise NotImplementedError("Use brain/agent/tools.py for file writing")

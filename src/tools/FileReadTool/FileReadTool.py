"""FileReadTool -- reads files."""
from __future__ import annotations
from typing import Any
from src.tools.FileReadTool.prompt import FILE_READ_TOOL_NAME


async def execute_file_read(file_path: str, **kwargs: Any) -> dict[str, Any]:
    """Read a file. Actual implementation in brain/agent/tools.py."""
    raise NotImplementedError("Use brain/agent/tools.py for file reading")

"""FileEditTool -- edits files by replacing strings."""
from __future__ import annotations
from typing import Any
from src.tools.FileEditTool.constants import FILE_EDIT_TOOL_NAME


async def execute_file_edit(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute a file edit. Actual implementation in brain/agent/tools.py."""
    raise NotImplementedError("Use brain/agent/tools.py for file editing")

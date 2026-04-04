"""Streaming tool executor for real-time tool output."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class StreamingToolExecutor:
    """Executes tools with streaming output support."""

    def __init__(self) -> None:
        self._active_executions: Dict[str, asyncio.Task] = {}

    async def execute_streaming(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        on_output: Callable[[str], None],
    ) -> Dict[str, Any]:
        """Execute a tool with streaming output callbacks."""
        result = {"type": "tool_result", "content": "", "is_error": False}

        try:
            # In a full implementation, this would stream tool output
            output = f"Tool {tool_name} completed"
            on_output(output)
            result["content"] = output
        except Exception as e:
            result["content"] = str(e)
            result["is_error"] = True

        return result

    def cancel(self, tool_id: str) -> None:
        """Cancel a running tool execution."""
        task = self._active_executions.pop(tool_id, None)
        if task:
            task.cancel()

    def cancel_all(self) -> None:
        """Cancel all running tool executions."""
        for task in self._active_executions.values():
            task.cancel()
        self._active_executions.clear()

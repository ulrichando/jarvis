"""Tool orchestration -- coordinates tool execution with hooks and permissions."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .toolExecution import execute_tool
from .toolHooks import ToolHooks

logger = logging.getLogger(__name__)


class ToolOrchestrator:
    """Orchestrates tool execution with permissions, hooks, and checkpoints."""

    def __init__(self) -> None:
        self.hooks = ToolHooks()

    async def run_tool(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        context: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Run a tool through the full pipeline."""
        # Pre-hooks
        modified_input = await self.hooks.run_pre_hooks(tool_name, tool_input)
        if modified_input is None:
            return {
                "type": "tool_result",
                "tool_name": tool_name,
                "content": "Tool execution blocked by pre-hook",
                "is_error": True,
            }

        # Execute
        result = await execute_tool(tool_name, modified_input, context)

        # Post-hooks
        result = await self.hooks.run_post_hooks(tool_name, result)

        return result

    async def run_tools_parallel(
        self,
        tool_calls: List[Dict[str, Any]],
        context: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """Run multiple tools in parallel."""
        import asyncio
        tasks = [
            self.run_tool(tc["name"], tc.get("input", {}), context)
            for tc in tool_calls
        ]
        return await asyncio.gather(*tasks)

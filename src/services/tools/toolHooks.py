"""Tool hooks -- pre/post tool execution hooks."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class ToolHooks:
    """Manages pre and post tool execution hooks."""

    def __init__(self) -> None:
        self._pre_hooks: List[Callable] = []
        self._post_hooks: List[Callable] = []

    def add_pre_hook(self, hook: Callable) -> None:
        self._pre_hooks.append(hook)

    def add_post_hook(self, hook: Callable) -> None:
        self._post_hooks.append(hook)

    async def run_pre_hooks(
        self, tool_name: str, tool_input: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Run pre-execution hooks. Returns modified input or None to block."""
        for hook in self._pre_hooks:
            result = await hook(tool_name, tool_input)
            if result is None:
                return None  # Hook blocked execution
            tool_input = result
        return tool_input

    async def run_post_hooks(
        self, tool_name: str, result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run post-execution hooks."""
        for hook in self._post_hooks:
            result = await hook(tool_name, result)
        return result

"""Computer use wrapper (non-JSX logic)."""

from __future__ import annotations

from typing import Any, Optional

from .executor import ComputerExecutor


async def execute_computer_use(
    action: dict[str, Any],
    executor: Optional[ComputerExecutor] = None,
) -> dict[str, Any]:
    """Execute a computer use action through the wrapper."""
    if executor is None:
        executor = ComputerExecutor()
    return await executor.execute_action(action)

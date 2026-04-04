"""Stop hooks for query termination conditions."""

from __future__ import annotations

from typing import Any, Callable, Optional


class StopHookResult:
    def __init__(self, should_stop: bool, reason: str = "") -> None:
        self.should_stop = should_stop
        self.reason = reason


StopHook = Callable[[dict[str, Any]], StopHookResult]


def create_max_iterations_hook(max_iterations: int) -> StopHook:
    """Create a stop hook that triggers after max iterations."""
    count = 0
    def hook(context: dict[str, Any]) -> StopHookResult:
        nonlocal count
        count += 1
        if count >= max_iterations:
            return StopHookResult(True, f"Reached max iterations ({max_iterations})")
        return StopHookResult(False)
    return hook


def create_token_budget_hook(budget: int) -> StopHook:
    """Create a stop hook that triggers when token budget is exhausted."""
    def hook(context: dict[str, Any]) -> StopHookResult:
        used = context.get("total_tokens", 0)
        if used >= budget:
            return StopHookResult(True, f"Token budget exhausted ({used}/{budget})")
        return StopHookResult(False)
    return hook

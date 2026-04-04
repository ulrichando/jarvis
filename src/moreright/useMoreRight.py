"""
Stub for external builds -- the real hook is internal only.

Self-contained: no relative imports. This is a no-op placeholder that
matches the TypeScript stub's behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional


@dataclass
class MoreRightArgs:
    enabled: bool
    set_messages: Callable[[Any], None]
    input_value: str
    set_input_value: Callable[[str], None]
    set_tool_jsx: Callable[[Any], None]


@dataclass
class MoreRightResult:
    on_before_query: Callable[..., Any]
    on_turn_complete: Callable[..., Any]
    render: Callable[[], None]


async def _default_on_before_query(
    input_str: str, all_messages: List[Any], n: int
) -> bool:
    return True


async def _default_on_turn_complete(
    all_messages: List[Any], aborted: bool
) -> None:
    pass


def _default_render() -> None:
    return None


def use_more_right(_args: MoreRightArgs) -> MoreRightResult:
    """Stub implementation that always returns passthrough callbacks."""
    return MoreRightResult(
        on_before_query=_default_on_before_query,
        on_turn_complete=_default_on_turn_complete,
        render=_default_render,
    )

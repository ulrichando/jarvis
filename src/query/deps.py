"""Query engine dependencies and dependency injection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class QueryDeps:
    """Dependencies injected into the query engine."""
    get_system_prompt: Optional[Callable[[], str]] = None
    get_tools: Optional[Callable[[], list[dict]]] = None
    on_message: Optional[Callable[[dict], None]] = None
    on_tool_use: Optional[Callable[[dict], None]] = None
    on_error: Optional[Callable[[Exception], None]] = None

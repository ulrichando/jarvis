"""Query engine configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class QueryConfig:
    """Configuration for the query engine."""
    model: Optional[str] = None
    max_tokens: int = 16384
    temperature: float = 1.0
    system_prompt: Optional[str] = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    allow_tool_use: bool = True
    max_iterations: int = 40
    token_budget: Optional[int] = None
    stop_sequences: list[str] = field(default_factory=list)

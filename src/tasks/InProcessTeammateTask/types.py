"""Types for in-process teammate tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TeammateTaskConfig:
    name: str = ""
    prompt: str = ""
    model: Optional[str] = None
    tools: list[str] = field(default_factory=list)
    max_iterations: int = 40


@dataclass
class TeammateTaskResult:
    success: bool = False
    output: str = ""
    error: Optional[str] = None
    tool_uses: int = 0
    duration_ms: float = 0


class InProcessTeammateTaskState:
    """State for in-process teammate task execution."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

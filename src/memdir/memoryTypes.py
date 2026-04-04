"""Memory type definitions for the memdir system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

MemoryType = Literal["user", "project", "codebase", "conversation", "team"]


@dataclass
class MemoryEntry:
    id: str = ""
    type: MemoryType = "project"
    title: str = ""
    content: str = ""
    path: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    tags: list[str] = field(default_factory=list)
    relevance_score: float = 0.0

"""Memory type definitions."""

from __future__ import annotations

from typing import Literal

MEMORY_TYPE_VALUES = (
    "User",
    "Project",
    "Local",
    "Managed",
    "AutoMem",
    "TeamMem",
)

MemoryType = Literal["User", "Project", "Local", "Managed", "AutoMem", "TeamMem"]

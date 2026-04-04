"""Context analysis and token stats."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .analyzeContext import TokenStats, analyze_context


@dataclass
class ContextData:
    percentage: int = 0
    raw_max_tokens: int = 200_000
    is_auto_compact_enabled: bool = True
    message_breakdown: Optional[Any] = None


# Re-export
__all__ = ["TokenStats", "ContextData", "analyze_context"]

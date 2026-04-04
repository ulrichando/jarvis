"""Context usage suggestions for optimization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

SuggestionSeverity = Literal["info", "warning"]

LARGE_TOOL_RESULT_PERCENT = 15
NEAR_CAPACITY_PERCENT = 80
MEMORY_HIGH_PERCENT = 5


@dataclass
class ContextSuggestion:
    severity: SuggestionSeverity
    title: str
    detail: str
    savings_tokens: Optional[int] = None


def generate_context_suggestions(data: Any) -> list[ContextSuggestion]:
    """Generate suggestions for context optimization."""
    suggestions: list[ContextSuggestion] = []

    percentage = getattr(data, "percentage", 0)
    if percentage >= NEAR_CAPACITY_PERCENT:
        suggestions.append(ContextSuggestion(
            severity="warning",
            title=f"Context is {percentage}% full",
            detail="Use /compact to free space.",
        ))

    suggestions.sort(key=lambda s: (0 if s.severity == "warning" else 1, -(s.savings_tokens or 0)))
    return suggestions

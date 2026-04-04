"""Token budget management for query sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class TokenBudget:
    """Tracks token usage against a budget."""
    total_budget: Optional[int] = None
    input_tokens_used: int = 0
    output_tokens_used: int = 0

    @property
    def total_used(self) -> int:
        return self.input_tokens_used + self.output_tokens_used

    @property
    def remaining(self) -> Optional[int]:
        if self.total_budget is None:
            return None
        return max(0, self.total_budget - self.total_used)

    @property
    def is_exhausted(self) -> bool:
        if self.total_budget is None:
            return False
        return self.total_used >= self.total_budget

    def add_usage(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens_used += input_tokens
        self.output_tokens_used += output_tokens

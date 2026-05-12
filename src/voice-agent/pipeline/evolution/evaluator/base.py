"""Evaluator stage protocol + result dataclass."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


__all__ = ["EvaluatorResult", "Stage"]


@dataclass
class EvaluatorResult:
    stage: str
    passed: bool
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


Stage = Callable[[dict], EvaluatorResult]

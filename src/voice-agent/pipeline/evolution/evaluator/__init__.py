"""Evaluator package: 5-stage pipeline with short-circuit on first fail."""
from __future__ import annotations

import logging
from typing import Iterable

from .base import EvaluatorResult, Stage


__all__ = ["EvaluatorPipeline", "EvaluatorResult", "Stage", "judge_call"]


logger = logging.getLogger("jarvis.evolution.evaluator")


class EvaluatorPipeline:
    def __init__(self, *, stages: Iterable[Stage]) -> None:
        self._stages: list[Stage] = list(stages)

    def run(self, proposal: dict) -> list[EvaluatorResult]:
        results: list[EvaluatorResult] = []
        for stage in self._stages:
            try:
                r = stage(proposal)
            except Exception as e:
                r = EvaluatorResult(
                    stage=stage.__name__,
                    passed=False,
                    reason=f"stage raised: {type(e).__name__}: {e}",
                )
            results.append(r)
            logger.info(
                f"[evaluator] {r.stage}: "
                f"{'PASS' if r.passed else 'FAIL'} ({r.reason})"
            )
            if not r.passed:
                break
        return results

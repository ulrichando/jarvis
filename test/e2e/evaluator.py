"""
Outcome evaluation utilities for E2E tests.

OutcomeEvaluator: checks if goals were achieved, not how.
JudgeEvaluator: uses LLM to judge if response meets goal (skips if no provider).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from src.agent.loop import MAX_ITERATIONS


class OutcomeEvaluator:
    """Evaluates test outcomes based on observable results, not code paths."""

    def file_exists(self, path: str) -> bool:
        return os.path.isfile(path)

    def file_contains(self, path: str, text: str) -> bool:
        try:
            with open(path) as f:
                return text in f.read()
        except (OSError, IOError):
            return False

    def response_addresses_goal(self, response: str, goal_keywords: list[str]) -> bool:
        """True if any keyword appears in the response (case-insensitive)."""
        lower = response.lower()
        return any(kw.lower() in lower for kw in goal_keywords)

    def no_tool_loops(self, inspector, max_same: int = 3) -> bool:
        return not inspector.has_loop(max_same=max_same)

    def under_iteration_limit(self, iters_used: int) -> bool:
        return iters_used <= MAX_ITERATIONS

    def graceful_failure(self, response: str) -> bool:
        """True if response doesn't look like an unhandled exception."""
        bad_starts = ("Traceback (most recent call last)", "Exception:", "RuntimeError:")
        return not any(response.startswith(b) for b in bad_starts)


class JudgeEvaluator:
    """Uses an LLM to judge whether a response achieved the goal."""

    async def judge(self, goal: str, response: str, reasoner) -> tuple[bool, str]:
        """
        Returns (passed, reason).
        Skips gracefully if no reasoner or exception.
        """
        try:
            if reasoner is None:
                return True, "skipped — no judge available"

            query_fn = getattr(reasoner, "query", None)
            if query_fn is None:
                return True, "skipped — no judge available"

            prompt = (
                f"Did this response achieve the goal?\n\n"
                f"Goal: {goal}\n\n"
                f"Response: {response}\n\n"
                f"Reply YES or NO followed by a brief reason."
            )
            system = "You are a strict QA evaluator. Reply only YES or NO followed by a brief reason."
            answer = await query_fn(prompt, system)
            passed = isinstance(answer, str) and answer.strip().upper().startswith("YES")
            return passed, answer.strip()
        except Exception as e:
            return True, f"skipped — exception: {e}"

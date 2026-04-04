"""
Periodic background summarization for coordinator mode sub-agents.

Generates 1-2 sentence progress summaries every ~30s by forking
the sub-agent's conversation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

SUMMARY_INTERVAL_S = 30.0


def _build_summary_prompt(previous_summary: Optional[str] = None) -> str:
    prev_line = (
        f'\nPrevious: "{previous_summary}" -- say something NEW.\n'
        if previous_summary
        else ""
    )
    return (
        "Describe your most recent action in 3-5 words using present tense (-ing). "
        "Name the file or function, not the branch. Do not use tools.\n"
        f"{prev_line}\n"
        'Good: "Reading runAgent.ts"\n'
        'Good: "Fixing null check in validate.ts"\n'
        'Good: "Running auth module tests"\n'
        'Good: "Adding retry logic to fetchUser"\n\n'
        'Bad (past tense): "Analyzed the branch diff"\n'
        'Bad (too vague): "Investigating the issue"\n'
        'Bad (too long): "Reviewing full branch diff and integration"'
    )


class AgentSummarizer:
    """Manages periodic background summarization for an agent."""

    def __init__(
        self,
        task_id: str,
        agent_id: str,
        on_summary: Optional[Callable[[str, str], None]] = None,
    ):
        self.task_id = task_id
        self.agent_id = agent_id
        self.on_summary = on_summary
        self._previous_summary: Optional[str] = None
        self._stopped = False
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """Start periodic summarization."""
        self._task = asyncio.ensure_future(self._run_loop())

    def stop(self) -> None:
        """Stop summarization."""
        logger.debug(f"[AgentSummary] Stopping summarization for {self.task_id}")
        self._stopped = True
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        """Main summarization loop."""
        while not self._stopped:
            await asyncio.sleep(SUMMARY_INTERVAL_S)
            if self._stopped:
                break
            await self._run_summary()

    async def _run_summary(self) -> None:
        """Generate a single summary."""
        if self._stopped:
            return

        logger.debug(f"[AgentSummary] Timer fired for agent {self.agent_id}")

        try:
            prompt = _build_summary_prompt(self._previous_summary)

            # In a full implementation, this would fork the agent's
            # conversation and generate a summary via LLM
            # For now, this is a placeholder
            summary_text = None

            if summary_text and self.on_summary:
                self._previous_summary = summary_text
                self.on_summary(self.task_id, summary_text)
        except Exception as e:
            if not self._stopped:
                logger.error(f"[AgentSummary] Error: {e}")


def start_agent_summarization(
    task_id: str,
    agent_id: str,
    on_summary: Optional[Callable[[str, str], None]] = None,
) -> AgentSummarizer:
    """Start periodic background summarization for an agent.

    Returns an AgentSummarizer with a stop() method.
    """
    summarizer = AgentSummarizer(task_id, agent_id, on_summary)
    summarizer.start()
    return summarizer

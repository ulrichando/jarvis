"""
Background memory consolidation (auto-dream).

Fires the /dream prompt as a forked subagent when the time-gate
passes AND enough sessions have accumulated.

Gate order (cheapest first):
  1. Time: hours since lastConsolidatedAt >= minHours (one stat)
  2. Sessions: transcript count with mtime > lastConsolidatedAt >= minSessions
  3. Lock: no other process mid-consolidation
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import is_auto_dream_enabled
from .consolidationLock import (
    read_last_consolidated_at,
    record_consolidation,
    rollback_consolidation_lock,
    try_acquire_consolidation_lock,
)
from .consolidationPrompt import build_consolidation_prompt

logger = logging.getLogger(__name__)

SESSION_SCAN_INTERVAL_MS = 10 * 60 * 1000  # 10 minutes


@dataclass
class AutoDreamConfig:
    min_hours: float = 24.0
    min_sessions: int = 5


DEFAULTS = AutoDreamConfig()


def _get_config() -> AutoDreamConfig:
    """Get auto-dream configuration with defaults."""
    min_hours_env = os.environ.get("JARVIS_DREAM_MIN_HOURS")
    min_sessions_env = os.environ.get("JARVIS_DREAM_MIN_SESSIONS")

    min_hours = DEFAULTS.min_hours
    min_sessions = DEFAULTS.min_sessions

    if min_hours_env:
        try:
            val = float(min_hours_env)
            if val > 0:
                min_hours = val
        except ValueError:
            pass

    if min_sessions_env:
        try:
            val = int(min_sessions_env)
            if val > 0:
                min_sessions = val
        except ValueError:
            pass

    return AutoDreamConfig(min_hours=min_hours, min_sessions=min_sessions)


def _count_sessions_since(since_ms: float) -> int:
    """Count session JSONL files modified after *since_ms* (epoch ms)."""
    sessions_dir = Path(
        os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))
    ) / "sessions"
    if not sessions_dir.is_dir():
        return 0
    since_s = since_ms / 1000.0
    count = 0
    for path in sessions_dir.glob("*.jsonl"):
        try:
            if path.stat().st_mtime > since_s:
                count += 1
        except OSError:
            continue
    return count


def _is_gate_open() -> bool:
    """Check if auto-dream should be able to run."""
    if not is_auto_dream_enabled():
        return False
    return True


class AutoDreamManager:
    """Manages automatic memory consolidation."""

    def __init__(self) -> None:
        self._last_scan_ms: float = 0.0
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def maybe_trigger(self) -> None:
        """Check gates and trigger consolidation if conditions are met.

        Designed to be fire-and-forget from the main loop — never raises.
        """
        try:
            await self._maybe_trigger_inner()
        except Exception as e:
            logger.debug(f"[autoDream] maybe_trigger error: {e}")

    async def _maybe_trigger_inner(self) -> None:
        if not _is_gate_open():
            return
        if self._running:
            return

        config = _get_config()
        now_ms = time.time() * 1000

        # Throttle scans
        if now_ms - self._last_scan_ms < SESSION_SCAN_INTERVAL_MS:
            return

        # Gate 1: Time since last consolidation
        last_consolidated = await read_last_consolidated_at()
        hours_since = (now_ms - last_consolidated) / (1000 * 60 * 60)
        if hours_since < config.min_hours:
            return

        self._last_scan_ms = now_ms

        # Gate 2: Enough sessions since last consolidation
        session_count = _count_sessions_since(last_consolidated)
        if session_count < config.min_sessions:
            return

        # Gate 3: Lock
        prior_mtime = await try_acquire_consolidation_lock()
        if prior_mtime is None:
            return

        # Run consolidation in background
        self._running = True
        self._task = asyncio.create_task(self._consolidate_bg(prior_mtime))

    async def _consolidate_bg(self, prior_mtime: float) -> None:
        """Background wrapper — runs consolidation and handles cleanup."""
        try:
            await self._run_consolidation()
            await record_consolidation()
            logger.info("[autoDream] consolidation completed successfully")
        except Exception as e:
            logger.error(f"[autoDream] consolidation failed: {e}")
            await rollback_consolidation_lock(prior_mtime)
        finally:
            self._running = False

    async def _run_consolidation(self) -> None:
        """Run memory consolidation via a worker sub-agent."""
        from src.reasoning.groq_client import GroqReasoner
        from src.agent.loop import _run_sub_agent

        memory_dir = os.environ.get(
            "JARVIS_MEMORY_DIR", os.path.expanduser("~/.jarvis/memory")
        )
        transcript_dir = os.environ.get(
            "JARVIS_TRANSCRIPT_DIR", os.path.expanduser("~/.jarvis/transcripts")
        )

        prompt = build_consolidation_prompt(memory_dir, transcript_dir)
        logger.info("[autoDream] starting memory consolidation via worker agent")

        reasoner = GroqReasoner()
        result = await _run_sub_agent(
            reasoner=reasoner,
            agent_type="worker",
            task=prompt,
            context="Background memory consolidation (auto-dream). "
                    "Read and update memory files, prune stale entries, "
                    "keep MEMORY.md index under 200 lines.",
        )
        logger.debug(f"[autoDream] consolidation result: {result[:500]}")


def init_auto_dream() -> AutoDreamManager:
    """Initialize the auto-dream manager."""
    return AutoDreamManager()

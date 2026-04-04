"""Scheduled task (cron) management for the REPL."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


def format_cron_fire_time(dt: datetime) -> str:
    """Format a datetime for cron fire notification."""
    return dt.strftime("%b %d %I:%M%p").replace("AM", "am").replace("PM", "pm")


class ScheduledTaskManager:
    """Manages scheduled (cron) tasks for the REPL.

    Mounts the scheduler once and tears it down on cleanup. Fired prompts
    go into the command queue as 'later' priority, which the REPL drains
    between turns.

    Equivalent to useScheduledTasks React hook.
    """

    def __init__(
        self,
        is_loading_fn: Callable[[], bool],
        enqueue_fn: Callable[[str], None],
        set_messages: Callable,
        get_app_state: Callable,
        set_app_state: Callable,
        assistant_mode: bool = False,
        is_cron_enabled: Callable[[], bool] = lambda: False,
    ):
        self._is_loading_fn = is_loading_fn
        self._enqueue_fn = enqueue_fn
        self._set_messages = set_messages
        self._get_app_state = get_app_state
        self._set_app_state = set_app_state
        self._assistant_mode = assistant_mode
        self._is_cron_enabled = is_cron_enabled
        self._running = False
        self._tasks: List[Dict[str, Any]] = []

    def start(self) -> None:
        """Start the scheduler."""
        if not self._is_cron_enabled():
            return
        self._running = True

    def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False

    def fire_task(self, task: Dict[str, Any]) -> None:
        """Fire a scheduled task."""
        if not self._running:
            return

        agent_id = task.get("agent_id")
        prompt = task.get("prompt", "")

        if agent_id:
            # Route to specific teammate
            state = self._get_app_state()
            # Find teammate and inject message
            # (simplified - full impl would look up task by agent_id)
            pass
        else:
            # Enqueue for the lead
            fire_time = format_cron_fire_time(datetime.now())
            self._enqueue_fn(prompt)

    def check(self) -> None:
        """Check for tasks that need to fire (called periodically)."""
        if not self._running or not self._is_cron_enabled():
            return
        # Check each task's schedule and fire if due
        for task in self._tasks:
            # Schedule checking logic would go here
            pass

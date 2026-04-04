"""Auto-exit teammate viewing mode when the viewed teammate terminates."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional


class TeammateViewAutoExit:
    """Auto-exits teammate viewing mode when the viewed teammate is killed or errors.

    Users stay viewing completed teammates so they can review the full transcript.

    Equivalent to useTeammateViewAutoExit React hook.
    """

    def __init__(
        self,
        get_app_state: Callable,
        set_app_state: Callable,
        exit_teammate_view: Callable,
    ):
        self._get_app_state = get_app_state
        self._set_app_state = set_app_state
        self._exit_teammate_view = exit_teammate_view

    def check(self) -> None:
        """Check if we should auto-exit teammate view."""
        state = self._get_app_state()
        viewing_task_id = state.get("viewing_agent_task_id")

        if not viewing_task_id:
            return

        tasks = state.get("tasks", {})
        task = tasks.get(viewing_task_id)

        # Task no longer exists
        if task is None:
            self._exit_teammate_view(self._set_app_state)
            return

        # Check if it's a teammate task
        if task.get("type") != "in_process_teammate":
            return

        status = task.get("status")
        error = task.get("error")

        # Auto-exit if killed, failed, has error, or unexpected status
        if status in ("killed", "failed") or error:
            self._exit_teammate_view(self._set_app_state)
        elif status not in ("running", "completed", "pending"):
            self._exit_teammate_view(self._set_app_state)

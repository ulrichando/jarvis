"""Keyboard navigation for background tasks (Shift+Up/Down)."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class BackgroundTaskNavigation:
    """Handles Shift+Up/Down keyboard navigation for background tasks.

    When teammates (swarm) are present, navigates between leader and teammates.
    When only non-teammate background tasks exist, opens the background tasks dialog.

    Equivalent to useBackgroundTaskNavigation React hook.
    """

    def __init__(
        self,
        get_app_state: Callable,
        set_app_state: Callable,
        on_open_background_tasks: Optional[Callable] = None,
    ):
        self._get_app_state = get_app_state
        self._set_app_state = set_app_state
        self._on_open_background_tasks = on_open_background_tasks

    def handle_key(self, key: str, shift: bool = False) -> bool:
        """Handle a key event. Returns True if consumed."""
        state = self._get_app_state()
        view_mode = state.get("view_selection_mode", "none")

        # Escape in viewing mode
        if key == "escape" and view_mode == "viewing-agent":
            task_id = state.get("viewing_agent_task_id")
            if task_id:
                task = state.get("tasks", {}).get(task_id)
                if task and task.get("status") == "running":
                    abort = task.get("current_work_abort_controller")
                    if abort:
                        abort()
                    return True
            self._exit_teammate_view()
            return True

        # Escape in selection mode
        if key == "escape" and view_mode == "selecting-agent":
            self._set_app_state({
                **state,
                "view_selection_mode": "none",
                "selected_ip_agent_index": -1,
            })
            return True

        # Shift+Up/Down navigation
        if shift and key in ("up", "down"):
            teammates = self._get_running_teammates()
            if teammates:
                delta = 1 if key == "down" else -1
                self._step_selection(delta)
            elif self._on_open_background_tasks:
                self._on_open_background_tasks()
            return True

        return False

    def _get_running_teammates(self) -> List[dict]:
        state = self._get_app_state()
        tasks = state.get("tasks", {})
        return [
            t for t in tasks.values()
            if t.get("type") == "in_process_teammate" and t.get("status") == "running"
        ]

    def _step_selection(self, delta: int) -> None:
        state = self._get_app_state()
        teammates = self._get_running_teammates()
        count = len(teammates)
        if count == 0:
            return

        if state.get("expanded_view") != "teammates":
            self._set_app_state({
                **state,
                "expanded_view": "teammates",
                "view_selection_mode": "selecting-agent",
                "selected_ip_agent_index": -1,
            })
            return

        cur = state.get("selected_ip_agent_index", -1)
        max_idx = count
        if delta == 1:
            new = -1 if cur >= max_idx else cur + 1
        else:
            new = max_idx if cur <= -1 else cur - 1

        self._set_app_state({
            **state,
            "selected_ip_agent_index": new,
            "view_selection_mode": "selecting-agent",
        })

    def _exit_teammate_view(self) -> None:
        state = self._get_app_state()
        self._set_app_state({
            **state,
            "view_selection_mode": "none",
            "viewing_agent_task_id": None,
            "selected_ip_agent_index": -1,
        })

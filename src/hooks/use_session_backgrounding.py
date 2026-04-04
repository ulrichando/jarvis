"""Session backgrounding management (Ctrl+B to background/foreground)."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class SessionBackgrounding:
    """Manages session backgrounding (background/foreground tasks).

    Handles:
    - Spawning background tasks for current query
    - Re-backgrounding foregrounded tasks
    - Syncing foregrounded task messages/state to main view

    Equivalent to useSessionBackgrounding React hook.
    """

    def __init__(
        self,
        set_messages: Callable,
        set_is_loading: Callable[[bool], None],
        reset_loading_state: Callable,
        set_abort_controller: Callable,
        on_background_query: Callable,
        get_app_state: Callable,
        set_app_state: Callable,
    ):
        self._set_messages = set_messages
        self._set_is_loading = set_is_loading
        self._reset_loading_state = reset_loading_state
        self._set_abort_controller = set_abort_controller
        self._on_background_query = on_background_query
        self._get_app_state = get_app_state
        self._set_app_state = set_app_state
        self._last_synced_messages_length = 0

    def handle_background_session(self) -> None:
        """Handle backgrounding request (Ctrl+B)."""
        state = self._get_app_state()
        foregrounded_id = state.get("foregrounded_task_id")

        if foregrounded_id:
            # Re-background the foregrounded task
            task = state.get("tasks", {}).get(foregrounded_id)
            if task:
                task["is_backgrounded"] = True
                self._set_app_state({
                    **state,
                    "foregrounded_task_id": None,
                    "tasks": {**state.get("tasks", {}), foregrounded_id: task},
                })
            else:
                self._set_app_state({**state, "foregrounded_task_id": None})

            self._set_messages([])
            self._reset_loading_state()
            self._set_abort_controller(None)
            return

        self._on_background_query()

    def sync_foregrounded_task(self) -> None:
        """Sync foregrounded task's messages and loading state to main view."""
        state = self._get_app_state()
        foregrounded_id = state.get("foregrounded_task_id")

        if not foregrounded_id:
            self._last_synced_messages_length = 0
            return

        task = state.get("tasks", {}).get(foregrounded_id)
        if not task or task.get("type") != "local_agent":
            self._set_app_state({**state, "foregrounded_task_id": None})
            self._reset_loading_state()
            self._last_synced_messages_length = 0
            return

        # Sync messages
        task_messages = task.get("messages", [])
        if len(task_messages) != self._last_synced_messages_length:
            self._last_synced_messages_length = len(task_messages)
            self._set_messages(list(task_messages))

        if task.get("status") == "running":
            self._set_is_loading(True)
        else:
            # Task completed
            task["is_backgrounded"] = True
            self._set_app_state({
                **state,
                "foregrounded_task_id": None,
                "tasks": {**state.get("tasks", {}), foregrounded_id: task},
            })
            self._reset_loading_state()
            self._set_abort_controller(None)
            self._last_synced_messages_length = 0

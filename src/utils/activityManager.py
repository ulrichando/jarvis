"""
Activity manager for tracking user and CLI operation activity.

Automatically deduplicates overlapping activities and provides
separate metrics for user vs CLI active time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, TypeVar

T = TypeVar("T")


@dataclass
class ActivityStates:
    is_user_active: bool
    is_cli_active: bool
    active_operation_count: int


class ActivityManager:
    """
    Handles generic activity tracking for both user and CLI operations.
    Automatically deduplicates overlapping activities and provides
    separate metrics for user vs CLI active time.
    """

    USER_ACTIVITY_TIMEOUT_MS: float = 5000.0  # 5 seconds

    _instance: Optional["ActivityManager"] = None

    def __init__(
        self,
        get_now: Optional[Callable[[], float]] = None,
        get_active_time_counter: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._active_operations: set[str] = set()
        self._last_user_activity_time: float = 0.0
        self._get_now = get_now or (lambda: time.time() * 1000)
        self._get_active_time_counter = get_active_time_counter
        self._last_cli_recorded_time: float = self._get_now()
        self._is_cli_active: bool = False

    @classmethod
    def get_instance(cls) -> "ActivityManager":
        if cls._instance is None:
            cls._instance = ActivityManager()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing purposes)."""
        cls._instance = None

    @classmethod
    def create_instance(
        cls,
        get_now: Optional[Callable[[], float]] = None,
        get_active_time_counter: Optional[Callable[[], Any]] = None,
    ) -> "ActivityManager":
        """Create a new instance with custom options (for testing)."""
        cls._instance = ActivityManager(get_now, get_active_time_counter)
        return cls._instance

    def record_user_activity(self) -> None:
        """Called when user interacts with the CLI (typing, commands, etc.)."""
        if not self._is_cli_active and self._last_user_activity_time != 0:
            now = self._get_now()
            time_since_last_activity = (now - self._last_user_activity_time) / 1000

            if time_since_last_activity > 0:
                counter = (
                    self._get_active_time_counter()
                    if self._get_active_time_counter
                    else None
                )
                if counter is not None:
                    timeout_seconds = self.USER_ACTIVITY_TIMEOUT_MS / 1000
                    if time_since_last_activity < timeout_seconds:
                        counter.add(time_since_last_activity, {"type": "user"})

        self._last_user_activity_time = self._get_now()

    def start_cli_activity(self, operation_id: str) -> None:
        """Starts tracking CLI activity (tool execution, AI response, etc.)."""
        if operation_id in self._active_operations:
            self.end_cli_activity(operation_id)

        was_empty = len(self._active_operations) == 0
        self._active_operations.add(operation_id)

        if was_empty:
            self._is_cli_active = True
            self._last_cli_recorded_time = self._get_now()

    def end_cli_activity(self, operation_id: str) -> None:
        """Stops tracking CLI activity."""
        self._active_operations.discard(operation_id)

        if len(self._active_operations) == 0:
            now = self._get_now()
            time_since_last_record = (now - self._last_cli_recorded_time) / 1000

            if time_since_last_record > 0:
                counter = (
                    self._get_active_time_counter()
                    if self._get_active_time_counter
                    else None
                )
                if counter is not None:
                    counter.add(time_since_last_record, {"type": "cli"})

            self._last_cli_recorded_time = now
            self._is_cli_active = False

    async def track_operation(
        self, operation_id: str, fn: Callable[[], Awaitable[T]]
    ) -> T:
        """Convenience method to track an async operation automatically."""
        self.start_cli_activity(operation_id)
        try:
            return await fn()
        finally:
            self.end_cli_activity(operation_id)

    def get_activity_states(self) -> ActivityStates:
        """Gets current activity states."""
        now = self._get_now()
        time_since_user_activity = (now - self._last_user_activity_time) / 1000
        is_user_active = time_since_user_activity < (
            self.USER_ACTIVITY_TIMEOUT_MS / 1000
        )

        return ActivityStates(
            is_user_active=is_user_active,
            is_cli_active=self._is_cli_active,
            active_operation_count=len(self._active_operations),
        )


# Singleton instance
activity_manager = ActivityManager.get_instance()

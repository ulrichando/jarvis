"""Unified command queue for user input, notifications, and system commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Literal, Optional, Union


QueuePriority = Literal["now", "next", "later"]

PRIORITY_ORDER: dict[QueuePriority, int] = {
    "now": 0,
    "next": 1,
    "later": 2,
}


@dataclass
class QueuedCommand:
    """A command in the queue."""
    value: Union[str, list[dict]]
    mode: str
    priority: QueuePriority = "next"
    is_meta: bool = False
    skip_slash_commands: bool = False
    agent_id: Optional[str] = None
    origin: Optional[dict] = None
    pasted_contents: Optional[dict[int, dict]] = None


class MessageQueueManager:
    """Module-level command queue with subscribe/snapshot interface."""

    def __init__(self) -> None:
        self._queue: list[QueuedCommand] = []
        self._snapshot: tuple[QueuedCommand, ...] = ()
        self._subscribers: list[Callable[[], None]] = []

    def _notify(self) -> None:
        self._snapshot = tuple(self._queue)
        for cb in self._subscribers:
            cb()

    # --- Subscribe / Snapshot ---

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to queue changes. Returns unsubscribe function."""
        self._subscribers.append(callback)

        def unsub() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return unsub

    def get_snapshot(self) -> tuple[QueuedCommand, ...]:
        """Get current snapshot of the command queue."""
        return self._snapshot

    # --- Read operations ---

    def get_queue(self) -> list[QueuedCommand]:
        """Get a copy of the current queue."""
        return list(self._queue)

    @property
    def length(self) -> int:
        return len(self._queue)

    def has_commands(self) -> bool:
        return len(self._queue) > 0

    def recheck(self) -> None:
        """Trigger a re-check by notifying subscribers."""
        if self._queue:
            self._notify()

    # --- Write operations ---

    def enqueue(self, command: QueuedCommand) -> None:
        """Add a command to the queue (default priority: 'next')."""
        self._queue.append(command)
        self._notify()

    def enqueue_notification(self, command: QueuedCommand) -> None:
        """Add a notification to the queue (default priority: 'later')."""
        if command.priority == "next":
            command.priority = "later"
        self._queue.append(command)
        self._notify()

    def dequeue(
        self, filter_fn: Optional[Callable[[QueuedCommand], bool]] = None
    ) -> Optional[QueuedCommand]:
        """Remove and return the highest-priority command."""
        if not self._queue:
            return None

        best_idx = -1
        best_priority = float("inf")
        for i, cmd in enumerate(self._queue):
            if filter_fn and not filter_fn(cmd):
                continue
            p = PRIORITY_ORDER.get(cmd.priority, 1)
            if p < best_priority:
                best_idx = i
                best_priority = p

        if best_idx == -1:
            return None

        dequeued = self._queue.pop(best_idx)
        self._notify()
        return dequeued

    def dequeue_all(self) -> list[QueuedCommand]:
        """Remove and return all commands."""
        if not self._queue:
            return []
        commands = list(self._queue)
        self._queue.clear()
        self._notify()
        return commands

    def peek(
        self, filter_fn: Optional[Callable[[QueuedCommand], bool]] = None
    ) -> Optional[QueuedCommand]:
        """Return the highest-priority command without removing it."""
        if not self._queue:
            return None

        best_idx = -1
        best_priority = float("inf")
        for i, cmd in enumerate(self._queue):
            if filter_fn and not filter_fn(cmd):
                continue
            p = PRIORITY_ORDER.get(cmd.priority, 1)
            if p < best_priority:
                best_idx = i
                best_priority = p

        if best_idx == -1:
            return None
        return self._queue[best_idx]

    def dequeue_all_matching(
        self, predicate: Callable[[QueuedCommand], bool]
    ) -> list[QueuedCommand]:
        """Remove and return all matching commands."""
        matched: list[QueuedCommand] = []
        remaining: list[QueuedCommand] = []
        for cmd in self._queue:
            if predicate(cmd):
                matched.append(cmd)
            else:
                remaining.append(cmd)
        if not matched:
            return []
        self._queue[:] = remaining
        self._notify()
        return matched

    def remove(self, commands_to_remove: list[QueuedCommand]) -> None:
        """Remove specific commands by identity."""
        if not commands_to_remove:
            return
        ids_to_remove = {id(c) for c in commands_to_remove}
        before = len(self._queue)
        self._queue[:] = [c for c in self._queue if id(c) not in ids_to_remove]
        if len(self._queue) != before:
            self._notify()

    def remove_by_filter(
        self, predicate: Callable[[QueuedCommand], bool]
    ) -> list[QueuedCommand]:
        """Remove commands matching a predicate."""
        removed: list[QueuedCommand] = []
        remaining: list[QueuedCommand] = []
        for cmd in self._queue:
            if predicate(cmd):
                removed.append(cmd)
            else:
                remaining.append(cmd)
        if removed:
            self._queue[:] = remaining
            self._notify()
        return removed

    def clear(self) -> None:
        """Clear all commands."""
        if not self._queue:
            return
        self._queue.clear()
        self._notify()

    def reset(self) -> None:
        """Clear all commands and reset snapshot."""
        self._queue.clear()
        self._snapshot = ()

    # --- Priority helpers ---

    def get_commands_by_max_priority(
        self, max_priority: QueuePriority
    ) -> list[QueuedCommand]:
        """Get commands at or above a given priority level."""
        threshold = PRIORITY_ORDER[max_priority]
        return [
            cmd
            for cmd in self._queue
            if PRIORITY_ORDER.get(cmd.priority, 1) <= threshold
        ]


# --- Utility functions ---

NON_EDITABLE_MODES = {"task-notification"}


def is_prompt_input_mode_editable(mode: str) -> bool:
    return mode not in NON_EDITABLE_MODES


def is_queued_command_editable(cmd: QueuedCommand) -> bool:
    return is_prompt_input_mode_editable(cmd.mode) and not cmd.is_meta


def is_slash_command(cmd: QueuedCommand) -> bool:
    """Check if the command is a slash command."""
    return (
        isinstance(cmd.value, str)
        and cmd.value.strip().startswith("/")
        and not cmd.skip_slash_commands
    )


def extract_text_from_value(value: Union[str, list[dict]]) -> str:
    """Extract text from a queued command value."""
    if isinstance(value, str):
        return value
    parts = []
    for block in value:
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)

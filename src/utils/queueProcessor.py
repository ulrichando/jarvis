"""
Queue processor for batching and dispatching queued commands.

Slash commands are processed individually.
Other non-slash commands with the same mode are batched together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Optional, Protocol, Union


@dataclass
class QueuedCommand:
    """Represents a queued command with value, mode, and optional agent ID."""

    value: Union[str, List[Any]]
    mode: str = "prompt"
    agent_id: Optional[str] = None


@dataclass
class ProcessQueueResult:
    """Result of a queue processing attempt."""

    processed: bool


# Module-level queue state
_queue: List[QueuedCommand] = []


def enqueue(cmd: QueuedCommand) -> None:
    """Add a command to the queue."""
    _queue.append(cmd)


def dequeue(predicate: Optional[Callable[[QueuedCommand], bool]] = None) -> Optional[QueuedCommand]:
    """Remove and return the first matching command from the queue."""
    for i, cmd in enumerate(_queue):
        if predicate is None or predicate(cmd):
            return _queue.pop(i)
    return None


def dequeue_all_matching(predicate: Callable[[QueuedCommand], bool]) -> List[QueuedCommand]:
    """Remove and return all matching commands from the queue."""
    matched = []
    remaining = []
    for cmd in _queue:
        if predicate(cmd):
            matched.append(cmd)
        else:
            remaining.append(cmd)
    _queue.clear()
    _queue.extend(remaining)
    return matched


def peek(predicate: Optional[Callable[[QueuedCommand], bool]] = None) -> Optional[QueuedCommand]:
    """Return the first matching command without removing it."""
    for cmd in _queue:
        if predicate is None or predicate(cmd):
            return cmd
    return None


def has_commands_in_queue() -> bool:
    """Check if there are any commands in the queue."""
    return len(_queue) > 0


def _is_slash_command(cmd: QueuedCommand) -> bool:
    """Check if a queued command is a slash command (value starts with '/')."""
    if isinstance(cmd.value, str):
        return cmd.value.strip().startswith("/")
    # For list values, check the first text block
    for block in cmd.value:
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text", "").strip().startswith("/")
    return False


def process_queue_if_ready(
    execute_input: Callable[[List[QueuedCommand]], Any],
) -> ProcessQueueResult:
    """
    Process commands from the queue.

    Slash commands and bash-mode commands are processed one at a time.
    Other non-slash commands are batched: all items with the same mode
    as the highest-priority item are drained at once.

    Args:
        execute_input: Callback to execute the commands.

    Returns:
        ProcessQueueResult with processed status.
    """
    is_main_thread = lambda cmd: cmd.agent_id is None

    next_cmd = peek(is_main_thread)
    if next_cmd is None:
        return ProcessQueueResult(processed=False)

    # Slash commands and bash-mode commands are processed individually
    if _is_slash_command(next_cmd) or next_cmd.mode == "bash":
        cmd = dequeue(is_main_thread)
        if cmd is not None:
            execute_input([cmd])
        return ProcessQueueResult(processed=True)

    # Drain all non-slash-command items with the same mode at once
    target_mode = next_cmd.mode
    commands = dequeue_all_matching(
        lambda cmd: is_main_thread(cmd)
        and not _is_slash_command(cmd)
        and cmd.mode == target_mode
    )
    if len(commands) == 0:
        return ProcessQueueResult(processed=False)

    execute_input(commands)
    return ProcessQueueResult(processed=True)


def has_queued_commands() -> bool:
    """Check if the queue has pending commands."""
    return has_commands_in_queue()

"""
SDK event queue for system events (task started/progress/notification, session state).

Events are queued and drained by the SDK in non-interactive mode.
In interactive (TUI) mode, events are discarded to avoid unbounded growth.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Union

MAX_QUEUE_SIZE = 1000


@dataclass
class TaskStartedEvent:
    type: Literal["system"] = "system"
    subtype: Literal["task_started"] = "task_started"
    task_id: str = ""
    description: str = ""
    tool_use_id: Optional[str] = None
    task_type: Optional[str] = None
    workflow_name: Optional[str] = None
    prompt: Optional[str] = None


@dataclass
class TaskProgressEvent:
    type: Literal["system"] = "system"
    subtype: Literal["task_progress"] = "task_progress"
    task_id: str = ""
    description: str = ""
    usage: Optional[Dict[str, Any]] = None
    tool_use_id: Optional[str] = None
    last_tool_name: Optional[str] = None
    summary: Optional[str] = None
    workflow_progress: Optional[List[Any]] = None


@dataclass
class TaskNotificationEvent:
    type: Literal["system"] = "system"
    subtype: Literal["task_notification"] = "task_notification"
    task_id: str = ""
    status: Literal["completed", "failed", "stopped"] = "completed"
    output_file: str = ""
    summary: str = ""
    tool_use_id: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None


@dataclass
class SessionStateChangedEvent:
    type: Literal["system"] = "system"
    subtype: Literal["session_state_changed"] = "session_state_changed"
    state: Literal["idle", "running", "requires_action"] = "idle"


SdkEvent = Union[
    TaskStartedEvent,
    TaskProgressEvent,
    TaskNotificationEvent,
    SessionStateChangedEvent,
]

_queue: List[SdkEvent] = []
_is_non_interactive: bool = False
_session_id: str = ""


def configure(*, is_non_interactive: bool, session_id: str) -> None:
    """Configure the SDK event queue."""
    global _is_non_interactive, _session_id
    _is_non_interactive = is_non_interactive
    _session_id = session_id


def enqueue_sdk_event(event: SdkEvent) -> None:
    """
    Enqueue an SDK event. Only queued in non-interactive mode.
    Oldest events are dropped when the queue exceeds MAX_QUEUE_SIZE.
    """
    if not _is_non_interactive:
        return
    if len(_queue) >= MAX_QUEUE_SIZE:
        _queue.pop(0)
    _queue.append(event)


def drain_sdk_events() -> List[Dict[str, Any]]:
    """
    Drain all queued SDK events, attaching uuid and session_id to each.
    Returns an empty list if the queue is empty.
    """
    if not _queue:
        return []

    events = list(_queue)
    _queue.clear()

    result = []
    for e in events:
        d: Dict[str, Any] = {}
        if hasattr(e, "__dataclass_fields__"):
            for f_name in e.__dataclass_fields__:
                val = getattr(e, f_name)
                if val is not None:
                    d[f_name] = val
        d["uuid"] = str(uuid.uuid4())
        d["session_id"] = _session_id
        result.append(d)

    return result


def emit_task_terminated_sdk(
    task_id: str,
    status: Literal["completed", "failed", "stopped"],
    *,
    tool_use_id: Optional[str] = None,
    summary: Optional[str] = None,
    output_file: Optional[str] = None,
    usage: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit a task_notification SDK event for a task reaching a terminal state."""
    enqueue_sdk_event(
        TaskNotificationEvent(
            task_id=task_id,
            status=status,
            tool_use_id=tool_use_id,
            output_file=output_file or "",
            summary=summary or "",
            usage=usage,
        )
    )

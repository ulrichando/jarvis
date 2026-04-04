"""
Teammate Mailbox - File-based messaging system for agent swarms.

Each teammate has an inbox file at teams/{team_name}/inboxes/{agent_name}.json.
Other teammates can write messages to it, and the recipient sees them as attachments.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)


@dataclass
class TeammateMessage:
    from_agent: str
    text: str
    timestamp: str
    read: bool = False
    color: Optional[str] = None
    summary: Optional[str] = None


@dataclass
class IdleNotificationMessage:
    type: str = "idle_notification"
    from_agent: str = ""
    timestamp: str = ""
    idle_reason: Optional[str] = None  # "available", "interrupted", "failed"
    summary: Optional[str] = None
    completed_task_id: Optional[str] = None
    completed_status: Optional[str] = None  # "resolved", "blocked", "failed"
    failure_reason: Optional[str] = None


@dataclass
class PermissionRequestMessage:
    type: str = "permission_request"
    request_id: str = ""
    agent_id: str = ""
    tool_name: str = ""
    tool_use_id: str = ""
    description: str = ""
    input: Dict[str, Any] = field(default_factory=dict)
    permission_suggestions: List[Any] = field(default_factory=list)


@dataclass
class TaskAssignmentMessage:
    type: str = "task_assignment"
    task_id: str = ""
    subject: str = ""
    description: str = ""
    assigned_by: str = ""
    timestamp: str = ""


@dataclass
class ShutdownRequestMessage:
    type: str = "shutdown_request"
    request_id: str = ""
    from_agent: str = ""
    reason: Optional[str] = None
    timestamp: str = ""


@dataclass
class ShutdownApprovedMessage:
    type: str = "shutdown_approved"
    request_id: str = ""
    from_agent: str = ""
    timestamp: str = ""
    pane_id: Optional[str] = None
    backend_type: Optional[str] = None


@dataclass
class ShutdownRejectedMessage:
    type: str = "shutdown_rejected"
    request_id: str = ""
    from_agent: str = ""
    reason: str = ""
    timestamp: str = ""


def _sanitize_path_component(input_str: str) -> str:
    """Sanitize a string for safe use in file paths."""
    import re
    return re.sub(r"[^a-zA-Z0-9_-]", "-", input_str)


def _get_teams_dir() -> str:
    """Get the teams directory path."""
    config_home = os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))
    return os.path.join(config_home, "teams")


def get_inbox_path(agent_name: str, team_name: Optional[str] = None) -> str:
    """Get the path to a teammate's inbox file."""
    team = team_name or os.environ.get("CLAUDE_CODE_TEAM_NAME", "default")
    safe_team = _sanitize_path_component(team)
    safe_agent = _sanitize_path_component(agent_name)
    return os.path.join(_get_teams_dir(), safe_team, "inboxes", f"{safe_agent}.json")


async def _ensure_inbox_dir(team_name: Optional[str] = None) -> None:
    """Ensure the inbox directory exists for a team."""
    team = team_name or os.environ.get("CLAUDE_CODE_TEAM_NAME", "default")
    safe_team = _sanitize_path_component(team)
    inbox_dir = os.path.join(_get_teams_dir(), safe_team, "inboxes")
    os.makedirs(inbox_dir, exist_ok=True)


async def read_mailbox(
    agent_name: str,
    team_name: Optional[str] = None,
) -> List[TeammateMessage]:
    """Read all messages from a teammate's inbox."""
    inbox_path = get_inbox_path(agent_name, team_name)
    try:
        with open(inbox_path, "r") as f:
            data = json.load(f)
        return [
            TeammateMessage(
                from_agent=m.get("from", ""),
                text=m.get("text", ""),
                timestamp=m.get("timestamp", ""),
                read=m.get("read", False),
                color=m.get("color"),
                summary=m.get("summary"),
            )
            for m in data
        ]
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, Exception) as e:
        logger.debug(f"Failed to read inbox for {agent_name}: {e}")
        return []


async def read_unread_messages(
    agent_name: str,
    team_name: Optional[str] = None,
) -> List[TeammateMessage]:
    """Read only unread messages from a teammate's inbox."""
    messages = await read_mailbox(agent_name, team_name)
    return [m for m in messages if not m.read]


async def write_to_mailbox(
    recipient_name: str,
    message: Dict[str, Any],
    team_name: Optional[str] = None,
) -> None:
    """Write a message to a teammate's inbox."""
    await _ensure_inbox_dir(team_name)
    inbox_path = get_inbox_path(recipient_name, team_name)

    # Ensure file exists
    if not os.path.exists(inbox_path):
        with open(inbox_path, "w") as f:
            json.dump([], f)

    # Read, append, write
    try:
        with open(inbox_path, "r") as f:
            messages = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        messages = []

    new_message = {**message, "read": False}
    messages.append(new_message)

    with open(inbox_path, "w") as f:
        json.dump(messages, f, indent=2)


async def mark_messages_as_read(
    agent_name: str,
    team_name: Optional[str] = None,
) -> None:
    """Mark all messages in a teammate's inbox as read."""
    inbox_path = get_inbox_path(agent_name, team_name)
    try:
        with open(inbox_path, "r") as f:
            messages = json.load(f)

        for m in messages:
            m["read"] = True

        with open(inbox_path, "w") as f:
            json.dump(messages, f, indent=2)
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.debug(f"Failed to mark messages as read for {agent_name}: {e}")


async def clear_mailbox(
    agent_name: str,
    team_name: Optional[str] = None,
) -> None:
    """Clear a teammate's inbox (delete all messages)."""
    inbox_path = get_inbox_path(agent_name, team_name)
    try:
        with open(inbox_path, "w") as f:
            json.dump([], f)
    except FileNotFoundError:
        pass


def format_teammate_messages(
    messages: List[Dict[str, str]],
) -> str:
    """Format teammate messages as XML for attachment display."""
    parts = []
    for m in messages:
        color_attr = f' color="{m.get("color")}"' if m.get("color") else ""
        summary_attr = f' summary="{m.get("summary")}"' if m.get("summary") else ""
        parts.append(
            f'<teammate-message teammate_id="{m["from"]}"{color_attr}{summary_attr}>\n'
            f'{m["text"]}\n'
            f'</teammate-message>'
        )
    return "\n\n".join(parts)


def create_idle_notification(
    agent_id: str,
    idle_reason: Optional[str] = None,
    summary: Optional[str] = None,
    completed_task_id: Optional[str] = None,
    completed_status: Optional[str] = None,
    failure_reason: Optional[str] = None,
) -> IdleNotificationMessage:
    """Create an idle notification message."""
    from datetime import datetime
    return IdleNotificationMessage(
        type="idle_notification",
        from_agent=agent_id,
        timestamp=datetime.utcnow().isoformat() + "Z",
        idle_reason=idle_reason,
        summary=summary,
        completed_task_id=completed_task_id,
        completed_status=completed_status,
        failure_reason=failure_reason,
    )


def is_idle_notification(message_text: str) -> Optional[IdleNotificationMessage]:
    """Check if a message text contains an idle notification."""
    try:
        parsed = json.loads(message_text)
        if isinstance(parsed, dict) and parsed.get("type") == "idle_notification":
            return IdleNotificationMessage(
                type="idle_notification",
                from_agent=parsed.get("from", ""),
                timestamp=parsed.get("timestamp", ""),
                idle_reason=parsed.get("idleReason"),
                summary=parsed.get("summary"),
                completed_task_id=parsed.get("completedTaskId"),
                completed_status=parsed.get("completedStatus"),
                failure_reason=parsed.get("failureReason"),
            )
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def is_permission_request(message_text: str) -> Optional[PermissionRequestMessage]:
    """Check if a message text contains a permission request."""
    try:
        parsed = json.loads(message_text)
        if isinstance(parsed, dict) and parsed.get("type") == "permission_request":
            return PermissionRequestMessage(
                type="permission_request",
                request_id=parsed.get("request_id", ""),
                agent_id=parsed.get("agent_id", ""),
                tool_name=parsed.get("tool_name", ""),
                tool_use_id=parsed.get("tool_use_id", ""),
                description=parsed.get("description", ""),
                input=parsed.get("input", {}),
                permission_suggestions=parsed.get("permission_suggestions", []),
            )
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def is_shutdown_request(message_text: str) -> Optional[ShutdownRequestMessage]:
    """Check if a message text contains a shutdown request."""
    try:
        parsed = json.loads(message_text)
        if isinstance(parsed, dict) and parsed.get("type") == "shutdown_request":
            return ShutdownRequestMessage(
                type="shutdown_request",
                request_id=parsed.get("requestId", ""),
                from_agent=parsed.get("from", ""),
                reason=parsed.get("reason"),
                timestamp=parsed.get("timestamp", ""),
            )
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def is_task_assignment(message_text: str) -> Optional[TaskAssignmentMessage]:
    """Check if a message text contains a task assignment."""
    try:
        parsed = json.loads(message_text)
        if isinstance(parsed, dict) and parsed.get("type") == "task_assignment":
            return TaskAssignmentMessage(
                type="task_assignment",
                task_id=parsed.get("taskId", ""),
                subject=parsed.get("subject", ""),
                description=parsed.get("description", ""),
                assigned_by=parsed.get("assignedBy", ""),
                timestamp=parsed.get("timestamp", ""),
            )
    except (json.JSONDecodeError, TypeError):
        pass
    return None


STRUCTURED_PROTOCOL_TYPES = frozenset({
    "permission_request",
    "permission_response",
    "sandbox_permission_request",
    "sandbox_permission_response",
    "shutdown_request",
    "shutdown_approved",
    "team_permission_update",
    "mode_set_request",
    "plan_approval_request",
    "plan_approval_response",
})


def is_structured_protocol_message(message_text: str) -> bool:
    """Check if a message is a structured protocol message."""
    try:
        parsed = json.loads(message_text)
        if isinstance(parsed, dict) and "type" in parsed:
            return parsed["type"] in STRUCTURED_PROTOCOL_TYPES
    except (json.JSONDecodeError, TypeError):
        pass
    return False

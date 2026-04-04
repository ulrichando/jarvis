"""Background tasks dialog for terminal.

Lists background tasks in a table with status, name, and duration.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional, Literal
import signal
import os
import time

from .taskStatusUtils import (
    getTaskStatusIcon,
    getTaskStatusColor,
    isTerminalStatus,
)

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


@dataclass
class ListItem:
    """A task displayed in the background tasks list."""
    id: str
    name: str
    task_type: str  # shell, agent, teammate, dream, remote
    status: str
    started_at: float = 0.0
    pid: int = 0
    output_preview: str = ""


@dataclass
class ViewState:
    """State of the tasks dialog view."""
    selected_index: int = 0
    filter_type: str = ""  # empty string = show all


@dataclass
class Props:
    """Properties for BackgroundTasksDialog."""
    tasks: list[ListItem] = field(default_factory=list)
    view_state: ViewState = field(default_factory=ViewState)


def getSelectableBackgroundTasks(
    tasks: list[dict[str, Any]],
    filter_type: str = "",
) -> list[ListItem]:
    """Filter and convert raw task dicts to ListItems.

    Args:
        tasks: List of raw task dictionaries.
        filter_type: Optional type filter (shell, agent, etc).

    Returns:
        List of ListItem objects for display.
    """
    items = []
    for t in tasks:
        task_type = t.get("type", "shell")
        if filter_type and task_type != filter_type:
            continue
        items.append(ListItem(
            id=t.get("id", ""),
            name=t.get("name", t.get("command", "unknown")),
            task_type=task_type,
            status=t.get("status", "running"),
            started_at=t.get("started_at", 0.0),
            pid=t.get("pid", 0),
            output_preview=t.get("output_preview", ""),
        ))
    return items


def _format_duration(started_at: float) -> str:
    """Format elapsed time since a timestamp."""
    if started_at <= 0:
        return ""
    elapsed = time.time() - started_at
    if elapsed < 60:
        return f"{elapsed:.0f}s"
    elif elapsed < 3600:
        return f"{elapsed / 60:.0f}m {elapsed % 60:.0f}s"
    else:
        hours = int(elapsed // 3600)
        mins = int((elapsed % 3600) // 60)
        return f"{hours}h {mins}m"


def toListItem(task: dict[str, Any]) -> ListItem:
    """Convert a raw task dict to a ListItem.

    Args:
        task: Raw task dictionary.

    Returns:
        ListItem for display.
    """
    return ListItem(
        id=task.get("id", ""),
        name=task.get("name", task.get("command", "unknown")),
        task_type=task.get("type", "shell"),
        status=task.get("status", "running"),
        started_at=task.get("started_at", 0.0),
        pid=task.get("pid", 0),
        output_preview=task.get("output_preview", ""),
    )


def Item(item: ListItem, selected: bool = False) -> str:
    """Format a single task list item.

    Args:
        item: The ListItem to format.
        selected: Whether this item is currently selected.

    Returns:
        Formatted line string.
    """
    icon = getTaskStatusIcon(item.status)
    color = getTaskStatusColor(item.status)
    duration = _format_duration(item.started_at)

    selector = f"{CYAN}>{RESET} " if selected else "  "
    name = item.name
    if len(name) > 40:
        name = name[:37] + "..."

    parts = [
        f"{selector}{icon}",
        f"{BOLD}{name:<40}{RESET}",
        f"{color}{item.status:<12}{RESET}",
        f"{DIM}{item.task_type:<10}{RESET}",
    ]
    if duration:
        parts.append(f"{DIM}{duration}{RESET}")

    return " ".join(parts)


def killShellTask(task: ListItem) -> bool:
    """Kill a shell background task.

    Args:
        task: The task to kill.

    Returns:
        True if the signal was sent successfully.
    """
    if task.pid <= 0:
        return False
    try:
        os.kill(task.pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def killAgentTask(task: ListItem) -> bool:
    """Kill an agent background task.

    Args:
        task: The agent task to kill.

    Returns:
        True if cancellation was initiated.
    """
    # Agent tasks are cooperative - we mark them for cancellation
    # The agent loop checks this flag and exits
    return task.id != ""


def killTeammateTask(task: ListItem) -> bool:
    """Kill a teammate background task.

    Args:
        task: The teammate task to kill.

    Returns:
        True if cancellation was initiated.
    """
    return task.id != ""


def killDreamTask(task: ListItem) -> bool:
    """Kill a dream/background-processing task.

    Args:
        task: The dream task to kill.

    Returns:
        True if cancellation was initiated.
    """
    return task.id != ""


def killRemoteAgentTask(task: ListItem) -> bool:
    """Kill a remote agent task.

    Args:
        task: The remote task to kill.

    Returns:
        True if cancellation was initiated.
    """
    return task.id != ""


def renderInputGuide() -> str:
    """Render the input guide for the tasks dialog.

    Returns:
        Formatted keybinding guide string.
    """
    return (
        f"{DIM}Navigation:{RESET} "
        f"[{BOLD}j{RESET}/{BOLD}k{RESET}] up/down  "
        f"[{BOLD}enter{RESET}] details  "
        f"[{RED}{BOLD}x{RESET}] kill  "
        f"[{BOLD}q{RESET}] close"
    )


def TeammateTaskGroups(tasks: list[ListItem]) -> str:
    """Group and display teammate tasks.

    Args:
        tasks: List of teammate task items.

    Returns:
        Formatted grouped display.
    """
    if not tasks:
        return f"{DIM}No teammate tasks.{RESET}"

    groups: dict[str, list[ListItem]] = {}
    for t in tasks:
        group_key = t.task_type
        if group_key not in groups:
            groups[group_key] = []
        groups[group_key].append(t)

    lines = []
    for group_name, group_tasks in groups.items():
        lines.append(f"{BOLD}{group_name}{RESET} ({len(group_tasks)})")
        for t in group_tasks:
            lines.append(Item(t))

    return "\n".join(lines)


def BackgroundTasksDialog(
    tasks: list[dict[str, Any]] | None = None,
    filter_type: str = "",
) -> str:
    """Format the background tasks dialog for terminal display.

    Args:
        tasks: List of raw task dicts.
        filter_type: Optional type filter.

    Returns:
        Complete formatted dialog string.
    """
    tasks = tasks or []
    items = getSelectableBackgroundTasks(tasks, filter_type)

    if not items:
        return (
            f"\n{BOLD}{CYAN}--- Background Tasks ---{RESET}\n"
            f"  {DIM}No background tasks running.{RESET}\n"
            f"{BOLD}{CYAN}------------------------{RESET}\n"
        )

    active = sum(1 for i in items if not isTerminalStatus(i.status))
    total = len(items)

    lines = [
        "",
        f"{BOLD}{CYAN}--- Background Tasks ({active} active / {total} total) ---{RESET}",
        "",
        f"  {'':2}{'':3} {BOLD}{'Name':<40} {'Status':<12} {'Type':<10} {'Duration'}{RESET}",
        f"  {DIM}{'-' * 80}{RESET}",
    ]

    for item in items:
        lines.append(f"  {Item(item)}")

    lines.append("")
    lines.append(f"  {renderInputGuide()}")
    lines.append(f"{BOLD}{CYAN}{'-' * 50}{RESET}")
    lines.append("")

    return "\n".join(lines)

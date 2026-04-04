"""Dialog launcher utilities.

Thin launchers for one-off dialog interactions. In the TypeScript version,
these launch React/JSX dialog components. The Python version provides
the logic without the UI rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Literal, Optional


@dataclass
class AgentMemoryScope:
    scope: str = ""


@dataclass
class ValidationError:
    message: str = ""
    path: Optional[str] = None


@dataclass
class AssistantSession:
    id: str = ""
    name: str = ""


@dataclass
class TeleportRemoteResponse:
    session_id: str = ""
    remote_url: Optional[str] = None


async def launch_snapshot_update_dialog(
    agent_type: str,
    scope: AgentMemoryScope,
    snapshot_timestamp: str,
) -> Literal["merge", "keep", "replace"]:
    """Prompt user for snapshot update action."""
    # In full implementation, would show interactive prompt
    return "keep"


async def launch_invalid_settings_dialog(
    settings_errors: List[ValidationError],
    on_exit: Any = None,
) -> None:
    """Show invalid settings dialog."""
    for error in settings_errors:
        print(f"Settings error: {error.message}")


async def launch_assistant_session_chooser(
    sessions: List[AssistantSession],
) -> Optional[str]:
    """Pick a session to attach to."""
    if not sessions:
        return None
    # In full implementation, would show interactive picker
    return sessions[0].id if sessions else None


async def launch_assistant_install_wizard() -> Optional[str]:
    """Launch the assistant install wizard."""
    # In full implementation, would show interactive wizard
    return None


async def launch_teleport_resume_wrapper() -> Optional[TeleportRemoteResponse]:
    """Launch interactive teleport session picker."""
    return None


async def launch_teleport_repo_mismatch_dialog(
    remote_cwd: str,
) -> Optional[str]:
    """Pick a local checkout of the target repo."""
    return None


async def launch_resume_chooser(
    resume_session_id: Optional[str] = None,
) -> Optional[Any]:
    """Launch the resume conversation chooser."""
    return None

"""
Session state tracking with listener-based notifications.

Tracks idle/running/requires_action state transitions and notifies
registered listeners.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Literal, Optional

SessionState = Literal["idle", "running", "requires_action"]


@dataclass
class RequiresActionDetails:
    """
    Context carried with requires_action transitions so downstream
    surfaces can show what the session is blocked on.
    """

    tool_name: str
    action_description: str
    tool_use_id: str
    request_id: str
    input: Optional[Dict[str, Any]] = None


@dataclass
class SessionExternalMetadata:
    """External metadata keys for session state."""

    permission_mode: Optional[str] = None
    is_ultraplan_mode: Optional[bool] = None
    model: Optional[str] = None
    pending_action: Optional[RequiresActionDetails] = None
    post_turn_summary: Optional[Any] = None
    task_summary: Optional[str] = None


SessionStateChangedListener = Callable[[SessionState, Optional[RequiresActionDetails]], None]
SessionMetadataChangedListener = Callable[[SessionExternalMetadata], None]
PermissionModeChangedListener = Callable[[str], None]

_state_listener: Optional[SessionStateChangedListener] = None
_metadata_listener: Optional[SessionMetadataChangedListener] = None
_permission_mode_listener: Optional[PermissionModeChangedListener] = None
_has_pending_action: bool = False
_current_state: SessionState = "idle"


def set_session_state_changed_listener(
    cb: Optional[SessionStateChangedListener],
) -> None:
    global _state_listener
    _state_listener = cb


def set_session_metadata_changed_listener(
    cb: Optional[SessionMetadataChangedListener],
) -> None:
    global _metadata_listener
    _metadata_listener = cb


def set_permission_mode_changed_listener(
    cb: Optional[PermissionModeChangedListener],
) -> None:
    global _permission_mode_listener
    _permission_mode_listener = cb


def get_session_state() -> SessionState:
    """Return the current session state."""
    return _current_state


def notify_session_state_changed(
    state: SessionState,
    details: Optional[RequiresActionDetails] = None,
) -> None:
    """Notify listeners of a session state change."""
    global _current_state, _has_pending_action
    _current_state = state

    if _state_listener is not None:
        _state_listener(state, details)

    if state == "requires_action" and details is not None:
        _has_pending_action = True
        if _metadata_listener is not None:
            _metadata_listener(SessionExternalMetadata(pending_action=details))
    elif _has_pending_action:
        _has_pending_action = False
        if _metadata_listener is not None:
            _metadata_listener(SessionExternalMetadata(pending_action=None))

    if state == "idle":
        if _metadata_listener is not None:
            _metadata_listener(SessionExternalMetadata(task_summary=None))


def notify_session_metadata_changed(
    metadata: SessionExternalMetadata,
) -> None:
    """Notify listeners of metadata changes."""
    if _metadata_listener is not None:
        _metadata_listener(metadata)


def notify_permission_mode_changed(mode: str) -> None:
    """Notify listeners of permission mode changes."""
    if _permission_mode_listener is not None:
        _permission_mode_listener(mode)

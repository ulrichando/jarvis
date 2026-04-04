"""
Session-scoped environment variables set via /env.
Applied only to spawned child processes (via bash provider env overrides),
not to the REPL process itself.
"""

from __future__ import annotations

from typing import Dict, Mapping

_session_env_vars: Dict[str, str] = {}


def get_session_env_vars() -> Mapping[str, str]:
    """Return a read-only view of session environment variables."""
    return _session_env_vars.copy()


def set_session_env_var(name: str, value: str) -> None:
    """Set a session-scoped environment variable."""
    _session_env_vars[name] = value


def delete_session_env_var(name: str) -> None:
    """Delete a session-scoped environment variable."""
    _session_env_vars.pop(name, None)


def clear_session_env_vars() -> None:
    """Clear all session-scoped environment variables."""
    _session_env_vars.clear()

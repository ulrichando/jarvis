"""Server types — Python equivalent of types.ts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


@dataclass
class ConnectResponse:
    """Schema for session connect response."""
    session_id: str
    ws_url: str
    work_dir: Optional[str] = None


@dataclass
class ServerConfig:
    port: int
    host: str
    auth_token: str
    unix: Optional[str] = None
    idle_timeout_ms: Optional[int] = None
    """Idle timeout for detached sessions (ms). 0 = never expire."""
    max_sessions: Optional[int] = None
    """Maximum number of concurrent sessions."""
    workspace: Optional[str] = None
    """Default workspace directory for sessions that don't specify cwd."""


SessionState = Literal["starting", "running", "detached", "stopping", "stopped"]


@dataclass
class SessionInfo:
    id: str
    status: SessionState
    created_at: float
    work_dir: str
    process: Any = None  # subprocess.Popen or None
    session_key: Optional[str] = None


@dataclass
class SessionIndexEntry:
    """Stable session key -> session metadata.

    Persisted to ~/.jarvis/server-sessions.json so sessions can be
    resumed across server restarts.
    """
    session_id: str
    """Server-assigned session ID."""
    transcript_session_id: str
    """The transcript session ID for --resume."""
    cwd: str
    permission_mode: Optional[str] = None
    created_at: float = 0.0
    last_active_at: float = 0.0


# SessionIndex is simply a dict mapping string keys to SessionIndexEntry
SessionIndex = dict[str, SessionIndexEntry]

"""Concurrent session management."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)

SessionKind = Literal["interactive", "bg", "daemon", "daemon-worker"]
SessionStatus = Literal["busy", "idle", "waiting"]


def _get_sessions_dir() -> str:
    home = os.path.expanduser("~")
    config_dir = os.environ.get("JARVIS_HOME", os.path.join(home, ".claude"))
    return os.path.join(config_dir, "sessions")


def _env_session_kind() -> Optional[SessionKind]:
    k = os.environ.get("CLAUDE_CODE_SESSION_KIND")
    if k in ("bg", "daemon", "daemon-worker"):
        return k  # type: ignore[return-value]
    return None


def is_bg_session() -> bool:
    """True when running inside a background session."""
    return _env_session_kind() == "bg"


async def register_session(
    session_id: str, cwd: str, kind: Optional[SessionKind] = None
) -> bool:
    """Write a PID file for this session.

    Returns True if registered, False if skipped.
    """
    actual_kind = kind or _env_session_kind() or "interactive"
    sessions_dir = _get_sessions_dir()
    pid_file = os.path.join(sessions_dir, f"{os.getpid()}.json")

    try:
        os.makedirs(sessions_dir, mode=0o700, exist_ok=True)
        data = {
            "pid": os.getpid(),
            "sessionId": session_id,
            "cwd": cwd,
            "kind": actual_kind,
            "startedAt": int(__import__("time").time() * 1000),
        }
        with open(pid_file, "w") as f:
            json.dump(data, f)
        os.chmod(pid_file, 0o600)
        return True
    except Exception as e:
        logger.debug(f"Failed to register session: {e}")
        return False


async def unregister_session() -> None:
    """Remove the PID file for this session."""
    sessions_dir = _get_sessions_dir()
    pid_file = os.path.join(sessions_dir, f"{os.getpid()}.json")
    try:
        os.unlink(pid_file)
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.debug(f"Failed to unregister session: {e}")

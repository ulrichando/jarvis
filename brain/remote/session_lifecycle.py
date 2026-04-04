"""
Session lifecycle management for JARVIS online hosting.

Handles session creation, archival, title derivation, and activity tracking.
Modeled after the bridge createSession pattern — sessions can be created
locally (UUID-based) or remotely via BridgeClient when available.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import subprocess
import time
import uuid as uuid_mod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.config import JARVIS_HOME

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SessionInfo:
    """Metadata for a single session."""

    session_id: str
    title: str = ""
    status: str = "active"  # active, archived, failed, interrupted
    created_at: float = 0.0
    archived_at: float = 0.0
    environment_id: str = ""
    model: str = ""
    permission_mode: str = "default"
    source_type: str = ""  # git, local, remote
    source_url: str = ""  # git repo URL
    source_branch: str = ""
    tags: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at,
            "archived_at": self.archived_at,
            "environment_id": self.environment_id,
            "model": self.model,
            "permission_mode": self.permission_mode,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "source_branch": self.source_branch,
            "tags": list(self.tags),
        }


@dataclass
class GitContext:
    """Git repository context extracted from the working directory."""

    repo_url: str = ""
    branch: str = ""
    remote_name: str = "origin"


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def get_git_context() -> GitContext:
    """Extract git info from current directory.

    Returns a GitContext with empty strings on failure (not a git repo, etc.).
    """
    ctx = GitContext()
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            ctx.repo_url = result.stdout.strip()
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            ctx.branch = result.stdout.strip()
    except Exception:
        pass

    return ctx


# ---------------------------------------------------------------------------
# SessionLifecycleManager
# ---------------------------------------------------------------------------

# Word pools for slug generation
ADJECTIVES = [
    "bright", "calm", "curious", "dark", "eager", "fast", "gentle",
    "happy", "keen", "light", "noble", "quiet", "rapid", "sharp",
    "swift", "warm", "wise", "bold", "clear", "deep",
]
NOUNS = [
    "aether", "beacon", "cipher", "delta", "ember", "flux", "glyph",
    "helix", "iris", "jade", "kite", "lambda", "matrix", "nexus",
    "orbit", "prism", "quartz", "realm", "spark", "tide",
]


class SessionLifecycleManager:
    """Create, track, archive, and title sessions.

    Works standalone (local UUID sessions) or with a BridgeClient for
    remote session operations against the hosting API.
    """

    def __init__(self, bridge_client: Any | None = None):
        self._sessions: dict[str, SessionInfo] = {}
        self._bridge: Any | None = bridge_client
        self._title_generation_count: dict[str, int] = {}

    # -- creation / archival ------------------------------------------------

    async def create_session(
        self,
        title: str = "",
        model: str = "",
        events: list | None = None,
        source_url: str = "",
        source_branch: str = "",
    ) -> SessionInfo:
        """Create a new session.

        If a bridge client is available the session is created via the remote
        API.  Otherwise a local session with a UUID-based ID is produced.
        Title is auto-derived from a slug when not provided.
        """
        session_id: str | None = None
        now = time.time()

        if not title:
            title = self.generate_slug()

        # Determine source type
        source_type = ""
        if source_url:
            source_type = "git"
        else:
            git_ctx = get_git_context()
            if git_ctx.repo_url:
                source_type = "git"
                source_url = source_url or git_ctx.repo_url
                source_branch = source_branch or git_ctx.branch

        if self._bridge is not None:
            try:
                remote_id = await self._bridge.create_session(
                    title=title,
                    model=model,
                    events=events or [],
                    source_url=source_url,
                    source_branch=source_branch,
                )
                if remote_id:
                    session_id = remote_id
            except Exception as exc:
                logger.warning("Bridge session creation failed: %s", exc)

        if session_id is None:
            session_id = str(uuid_mod.uuid4())

        info = SessionInfo(
            session_id=session_id,
            title=title,
            status="active",
            created_at=now,
            model=model,
            permission_mode="default",
            source_type=source_type,
            source_url=source_url,
            source_branch=source_branch,
        )
        self._sessions[session_id] = info
        self._title_generation_count[session_id] = 0
        logger.info("Session created: %s (%s)", session_id, title)
        return info

    async def archive_session(self, session_id: str) -> bool:
        """Archive a session (idempotent).

        Returns True on success, False if the session does not exist.
        """
        info = self._sessions.get(session_id)
        if info is None:
            logger.warning("Cannot archive unknown session: %s", session_id)
            return False

        if info.status == "archived":
            return True  # idempotent

        if self._bridge is not None:
            try:
                await self._bridge.archive_session(session_id)
            except Exception as exc:
                logger.warning("Bridge archive failed: %s", exc)

        info.status = "archived"
        info.archived_at = time.time()
        logger.info("Session archived: %s", session_id)
        return True

    # -- queries ------------------------------------------------------------

    def get_session(self, session_id: str) -> SessionInfo | None:
        return self._sessions.get(session_id)

    def get_active_sessions(self) -> list[SessionInfo]:
        return [s for s in self._sessions.values() if s.status == "active"]

    def get_all_sessions(self) -> list[SessionInfo]:
        return list(self._sessions.values())

    # -- title management ---------------------------------------------------

    async def update_title(self, session_id: str, title: str) -> bool:
        """Update session title locally and via bridge if available."""
        info = self._sessions.get(session_id)
        if info is None:
            return False

        info.title = title

        if self._bridge is not None:
            try:
                await self._bridge.update_session_title(session_id, title)
            except Exception as exc:
                logger.warning("Bridge title update failed: %s", exc)

        return True

    @staticmethod
    def derive_title(text: str, max_length: int = 50) -> str:
        """Extract a short title from user message text.

        Strips markdown formatting, takes the first sentence, and truncates.
        """
        # Strip markdown: links, images, bold/italic, headers, code fences
        clean = re.sub(r"!\[.*?\]\(.*?\)", "", text)
        clean = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", clean)
        clean = re.sub(r"[*_`#>~]+", "", clean)
        clean = re.sub(r"```[\s\S]*?```", "", clean)
        clean = clean.strip()

        if not clean:
            return ""

        # First sentence (split on sentence-ending punctuation or newline)
        match = re.split(r"[.!?\n]", clean, maxsplit=1)
        first = match[0].strip() if match else clean.strip()

        if len(first) > max_length:
            first = first[:max_length].rsplit(" ", 1)[0]

        return first.strip()

    @staticmethod
    def generate_slug() -> str:
        """Generate a random word slug like 'bright-aether'."""
        return f"{random.choice(ADJECTIVES)}-{random.choice(NOUNS)}"

    def auto_title(self, session_id: str, messages: list[dict]) -> str:
        """Auto-generate title based on conversation content.

        - Count 1 (first message): derive from first user message.
        - Count 3+: derive from first 3 user messages for richer context.
        - Tracks generation count to avoid redundant updates.
        """
        count = self._title_generation_count.get(session_id, 0)
        user_msgs = [
            m.get("content", "") for m in messages
            if m.get("role") == "user" and m.get("content")
        ]

        if not user_msgs:
            return ""

        title = ""
        if count == 0 and len(user_msgs) >= 1:
            title = self.derive_title(user_msgs[0])
            self._title_generation_count[session_id] = 1
        elif count < 3 and len(user_msgs) >= 3:
            combined = ". ".join(user_msgs[:3])
            title = self.derive_title(combined)
            self._title_generation_count[session_id] = 3

        return title


# ---------------------------------------------------------------------------
# SessionActivityLog
# ---------------------------------------------------------------------------

_VALID_EVENT_TYPES = frozenset({
    "session_start", "session_end", "message", "tool_call",
    "tool_result", "error", "permission_request",
    "permission_response", "mode_change",
})


class SessionActivityLog:
    """Append-only activity log scoped to a single session.

    Entries are buffered in memory and flushed to a JSONL file under
    ``~/.jarvis/session-logs/<session_id>.jsonl``.
    """

    def __init__(self, session_id: str, log_dir: str = ""):
        self.session_id = session_id
        self._log_dir = Path(log_dir) if log_dir else JARVIS_HOME / "session-logs"
        self._entries: list[dict] = []
        self._start_time: float = time.time()

    def log_event(self, event_type: str, data: dict | None = None) -> None:
        """Append an event and flush to disk."""
        entry = {
            "type": event_type,
            "data": data or {},
            "timestamp": time.time(),
        }
        self._entries.append(entry)
        self.flush()

    def get_entries(self, event_type: str = "") -> list[dict]:
        """Return entries, optionally filtered by event type."""
        if not event_type:
            return list(self._entries)
        return [e for e in self._entries if e.get("type") == event_type]

    def flush(self) -> None:
        """Write un-flushed entries to ``{log_dir}/{session_id}.jsonl`` (append)."""
        if not self._entries:
            return
        self._log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._log_dir / f"{self.session_id}.jsonl"
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                for entry in self._entries:
                    fh.write(json.dumps(entry, default=str) + "\n")
        except OSError as exc:
            logger.warning("Failed to flush session log: %s", exc)

    def get_summary(self) -> dict:
        """Summarise session activity."""
        now = time.time()
        return {
            "session_id": self.session_id,
            "event_count": len(self._entries),
            "duration": now - self._start_time,
            "message_count": sum(
                1 for e in self._entries if e["type"] == "message"
            ),
            "tool_count": sum(
                1 for e in self._entries if e["type"] in ("tool_call", "tool_result")
            ),
            "error_count": sum(
                1 for e in self._entries if e["type"] == "error"
            ),
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_lifecycle: SessionLifecycleManager | None = None


def get_lifecycle_manager(bridge_client: Any | None = None) -> SessionLifecycleManager:
    """Return (or create) the module-level SessionLifecycleManager singleton."""
    global _lifecycle
    if _lifecycle is None:
        _lifecycle = SessionLifecycleManager(bridge_client=bridge_client)
    return _lifecycle

"""ACP session lifecycle for the JARVIS adapter.

A session bundles:

  - A unique ``session_id`` (UUID4) the IDE sees.
  - The working directory the IDE chose (rooted in the user's project).
  - A growing chat history (OpenAI-shape messages: ``role`` / ``content``
    plus ``tool_calls`` and tool-result rows).
  - A LiveKit-style tool list materialised from the JARVIS registry.
  - A cancel event the ``cancel`` request flips to stop in-flight work.
  - A small persistence file under
    ``~/.local/share/jarvis/acp_sessions/<id>.json`` so a process bounce
    doesn't lose the conversation transcript.

This module deliberately holds NO ACP / LiveKit imports at module
scope — the ACP server is the only consumer and imports it lazily, so
the test suite can exercise the session manager without a full ACP
stack on hand.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _acp_sessions_dir() -> Path:
    """Return ``<jarvis data dir>/acp_sessions`` (created on demand).

    Uses ``tools.runtime.get_jarvis_data_dir`` so the path follows the
    same cross-platform conventions as the rest of the voice agent's
    state (XDG on Linux/macOS, LOCALAPPDATA on Windows).
    """
    from tools.runtime import get_jarvis_data_dir

    d = get_jarvis_data_dir() / "acp_sessions"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


# ---------------------------------------------------------------------------
# Mode / approval-policy mapping
# ---------------------------------------------------------------------------


MODE_DEFAULT = "default"
MODE_ACCEPT_EDITS = "accept_edits"
MODE_DONT_ASK = "dont_ask"

MODE_TO_APPROVAL_POLICY: Dict[str, str] = {
    MODE_DEFAULT: "ask",
    MODE_ACCEPT_EDITS: "workspace_session",
    MODE_DONT_ASK: "session",
}


# ---------------------------------------------------------------------------
# SessionState
# ---------------------------------------------------------------------------


@dataclass
class SessionState:
    """Mutable per-session bag the server reads + writes."""

    session_id: str
    cwd: str = "."
    model: str = ""
    mode: str = MODE_DEFAULT
    history: List[Dict[str, Any]] = field(default_factory=list)
    cancel_event: Any = None  # threading.Event
    is_running: bool = False
    queued_prompts: List[str] = field(default_factory=list)
    current_prompt_text: str = ""
    interrupted_prompt_text: str = ""
    runtime_lock: Any = field(default_factory=threading.Lock)
    # Cached LiveKit-shaped tool list (built once per session).
    _tools: Any = None
    # Cached system prompt (built once per session, mirrors how the
    # voice agent assembles SOUL + JARVIS_INSTRUCTIONS).
    _system_prompt: str = ""

    def to_persistable_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable snapshot for disk persistence."""
        return {
            "session_id": self.session_id,
            "cwd": self.cwd,
            "model": self.model,
            "mode": self.mode,
            "history": self.history,
            "created_at": time.time(),
        }


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


class SessionManager:
    """Thread-safe in-memory + on-disk store for ACP sessions.

    Sessions live in-memory for the lifetime of the adapter process. A
    minimal JSON snapshot lands under ``acp_sessions/<id>.json`` after
    every write so the IDE can ``session/load`` a prior conversation
    after a process bounce.
    """

    def __init__(self, *, persist: bool = True) -> None:
        self._sessions: Dict[str, SessionState] = {}
        self._lock = threading.Lock()
        self._persist_enabled = persist

    # ---- create / fetch / mutate -------------------------------------------

    def create_session(self, cwd: str = ".") -> SessionState:
        """Spin up a fresh session anchored at *cwd*."""
        session_id = str(uuid.uuid4())
        state = SessionState(
            session_id=session_id,
            cwd=cwd or ".",
            cancel_event=threading.Event(),
        )
        with self._lock:
            self._sessions[session_id] = state
        self._persist(state)
        logger.info("Created ACP session %s (cwd=%s)", session_id, cwd)
        return state

    def get_session(self, session_id: str) -> Optional[SessionState]:
        """Return the session by id, hydrating from disk if needed."""
        with self._lock:
            state = self._sessions.get(session_id)
        if state is not None:
            return state
        return self._restore(session_id)

    def remove_session(self, session_id: str) -> bool:
        """Drop a session from memory + disk; return whether it existed."""
        existed_in_mem = False
        with self._lock:
            existed_in_mem = self._sessions.pop(session_id, None) is not None
        existed_on_disk = self._delete_persisted(session_id)
        return existed_in_mem or existed_on_disk

    def update_cwd(self, session_id: str, cwd: str) -> Optional[SessionState]:
        """Update the working directory the session is anchored to."""
        state = self.get_session(session_id)
        if state is None:
            return None
        state.cwd = cwd or state.cwd
        self._persist(state)
        return state

    def save_session(self, session_id: str) -> None:
        """Persist a session's current state."""
        with self._lock:
            state = self._sessions.get(session_id)
        if state is not None:
            self._persist(state)

    def list_sessions(self, cwd: str | None = None) -> List[Dict[str, Any]]:
        """Return a list of session info dicts for ``session/list``."""
        rows: List[Dict[str, Any]] = []
        seen: set[str] = set()

        with self._lock:
            in_memory = list(self._sessions.values())

        for state in in_memory:
            seen.add(state.session_id)
            if cwd and _normalize_cwd(state.cwd) != _normalize_cwd(cwd):
                continue
            rows.append({
                "session_id": state.session_id,
                "cwd": state.cwd,
                "title": _derive_title(state),
                "updated_at": _format_now_iso(),
                "message_count": len(state.history),
            })

        if self._persist_enabled:
            for f in _acp_sessions_dir().glob("*.json"):
                sid = f.stem
                if sid in seen:
                    continue
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                session_cwd = str(data.get("cwd") or ".")
                if cwd and _normalize_cwd(session_cwd) != _normalize_cwd(cwd):
                    continue
                rows.append({
                    "session_id": sid,
                    "cwd": session_cwd,
                    "title": _derive_title_from_dict(data),
                    "updated_at": _format_iso_from_epoch(data.get("created_at")),
                    "message_count": len(data.get("history") or []),
                })

        rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
        return rows

    def fork_session(self, session_id: str, cwd: str = ".") -> Optional[SessionState]:
        """Deep-copy a session's history into a brand-new session id."""
        import copy

        original = self.get_session(session_id)
        if original is None:
            return None

        new_id = str(uuid.uuid4())
        state = SessionState(
            session_id=new_id,
            cwd=cwd or original.cwd,
            model=original.model,
            mode=original.mode,
            history=copy.deepcopy(original.history),
            cancel_event=threading.Event(),
        )
        with self._lock:
            self._sessions[new_id] = state
        self._persist(state)
        logger.info("Forked ACP session %s -> %s", session_id, new_id)
        return state

    # ---- persistence -------------------------------------------------------

    def _persist(self, state: SessionState) -> None:
        if not self._persist_enabled:
            return
        try:
            path = _acp_sessions_dir() / f"{state.session_id}.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(state.to_persistable_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except Exception:
            logger.debug("Failed to persist ACP session %s", state.session_id, exc_info=True)

    def _restore(self, session_id: str) -> Optional[SessionState]:
        if not self._persist_enabled:
            return None
        path = _acp_sessions_dir() / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.debug("Failed to read ACP session file %s", path, exc_info=True)
            return None
        state = SessionState(
            session_id=session_id,
            cwd=str(data.get("cwd") or "."),
            model=str(data.get("model") or ""),
            mode=str(data.get("mode") or MODE_DEFAULT),
            history=list(data.get("history") or []),
            cancel_event=threading.Event(),
        )
        with self._lock:
            self._sessions[session_id] = state
        logger.info(
            "Restored ACP session %s from disk (%d messages)",
            session_id, len(state.history),
        )
        return state

    def _delete_persisted(self, session_id: str) -> bool:
        if not self._persist_enabled:
            return False
        path = _acp_sessions_dir() / f"{session_id}.json"
        if not path.exists():
            return False
        try:
            path.unlink()
            return True
        except OSError:
            return False


# ---------------------------------------------------------------------------
# Small helpers used by the session manager
# ---------------------------------------------------------------------------


def _normalize_cwd(cwd: str | None) -> str:
    raw = (cwd or ".").strip() or "."
    try:
        return str(Path(raw).expanduser().resolve(strict=False))
    except Exception:
        return raw


def _derive_title(state: SessionState) -> str:
    for msg in state.history:
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return _truncate(content.strip(), 80)
    return _truncate(Path(state.cwd).name or "JARVIS session", 80)


def _derive_title_from_dict(data: Dict[str, Any]) -> str:
    for msg in (data.get("history") or []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return _truncate(content.strip(), 80)
    return _truncate(Path(str(data.get("cwd") or ".")).name or "JARVIS session", 80)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _format_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _format_iso_from_epoch(value: Any) -> str:
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except Exception:
        return _format_now_iso()


# ---------------------------------------------------------------------------
# Tool registry bridge
# ---------------------------------------------------------------------------


# Tool names allowed in an ACP coding session. The voice agent ships
# tools that don't make sense in an IDE (computer_use needs a real
# desktop; vision_analyze expects screenshots from the LiveKit room;
# memory + skills are global). This filter keeps the IDE-facing surface
# focused on the actual coding loop.
ACP_CODING_TOOLS = frozenset({
    "read_file",
    "write_file",
    "patch",
    "search_files",
    "code_search",
    "find_definitions",
    "execute_code",
    "terminal",
    "web_search",
    "web_fetch",
    "web_extract",
    "memory",
    "session_search",
    "todo",
    "schedule",
    "vuln_check",
    "skill_view",
    "skills_list",
    "skill_manage",
    "clarify",
})


def build_acp_tools(tool_filter: Optional[Callable[[str], bool]] = None) -> List[Any]:
    """Return the LiveKit-shaped tool list the supervisor LLM will see.

    Defaults to JARVIS's ``ACP_CODING_TOOLS`` filter. ``tool_filter`` lets
    callers override for tests or for an "everything" mode.
    """
    from tools._adapter import load_all_livekit_tools
    from tools.registry import registry

    if tool_filter is None:
        tool_filter = lambda name: name in ACP_CODING_TOOLS  # noqa: E731

    tools = load_all_livekit_tools()
    filtered: List[Any] = []
    for tool in tools:
        info = getattr(tool, "info", None)
        name = getattr(info, "name", None) if info is not None else None
        # Fall back to the registry to look up the name when info isn't
        # available — keeps tests happy with stub tools.
        if not name and hasattr(tool, "__name__"):
            name = tool.__name__
        if name and tool_filter(name):
            filtered.append(tool)
    # Quick log so the operator sees what landed.
    logger.info(
        "ACP session tool surface: %d/%d tools (%s)",
        len(filtered), len(tools),
        ", ".join(sorted(getattr(getattr(t, "info", None), "name", "?") for t in filtered)),
    )
    return filtered

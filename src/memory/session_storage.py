"""Session storage for persisting conversation history and state.

Provides:
- SessionStorage: per-session JSONL append-only log
- HistoryManager: global history across sessions (for up-arrow browsing)
- TranscriptWriter: human-readable transcripts
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.config import JARVIS_HOME

log = logging.getLogger("jarvis.session")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SessionEntry:
    """A single entry in a session log."""

    session_id: str
    timestamp: float
    role: str  # "user", "jarvis", "system", "tool"
    content: str
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SessionEntry:
        return cls(
            session_id=data.get("session_id", ""),
            timestamp=data.get("timestamp", 0.0),
            role=data.get("role", ""),
            content=data.get("content", ""),
            tool_name=data.get("tool_name", ""),
            tool_args=data.get("tool_args", {}),
            metadata=data.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# SessionStorage — per-session JSONL persistence
# ---------------------------------------------------------------------------

class SessionStorage:
    """In-memory buffer backed by append-only JSONL files on disk.

    Each session gets its own file: ``{storage_dir}/{session_id}.jsonl``.
    Entries accumulate in ``_entries`` (the full session) and ``_pending_flush``
    (not yet written).  ``flush()`` appends only the pending entries.
    """

    def __init__(self, storage_dir: str = "", session_id: str = ""):
        self._storage_dir = Path(storage_dir) if storage_dir else JARVIS_HOME / "sessions"
        self._storage_dir.mkdir(parents=True, exist_ok=True)

        self._session_id: str = session_id or uuid.uuid4().hex
        self._entries: list[SessionEntry] = []
        self._pending_flush: list[SessionEntry] = []
        self._flush_lock = threading.Lock()

    # -- properties ----------------------------------------------------------

    @property
    def session_id(self) -> str:
        return self._session_id

    # -- write path ----------------------------------------------------------

    def add_entry(self, role: str, content: str, **kwargs) -> SessionEntry:
        """Add an entry to the in-memory buffer and pending flush queue."""
        entry = SessionEntry(
            session_id=self._session_id,
            timestamp=time.time(),
            role=role,
            content=content,
            tool_name=kwargs.get("tool_name", ""),
            tool_args=kwargs.get("tool_args", {}),
            metadata=kwargs.get("metadata", {}),
        )
        self._entries.append(entry)
        self._pending_flush.append(entry)
        return entry

    def flush(self) -> None:
        """Write pending entries to the session JSONL file (append-only)."""
        with self._flush_lock:
            if not self._pending_flush:
                return
            batch = list(self._pending_flush)
            self._pending_flush.clear()

        path = self._get_session_file()
        lines = "".join(json.dumps(e.to_dict()) + "\n" for e in batch)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(lines)

    def _get_session_file(self) -> Path:
        return self._storage_dir / f"{self._session_id}.jsonl"

    # -- read path -----------------------------------------------------------

    def get_history(self, limit: int = 100) -> list[SessionEntry]:
        """Return the most recent *limit* entries from this session."""
        return self._entries[-limit:]

    def get_session_entries(self, session_id: str = "") -> list[SessionEntry]:
        """Load all entries for *session_id* (defaults to current session).

        If the requested session is the current one, returns the in-memory
        buffer.  Otherwise reads from disk.
        """
        sid = session_id or self._session_id
        if sid == self._session_id:
            return list(self._entries)
        return self._load_session_from_disk(sid)

    def search_history(self, query: str, limit: int = 20) -> list[SessionEntry]:
        """Substring search across *all* session files on disk."""
        results: list[SessionEntry] = []
        query_lower = query.lower()

        # Search current in-memory session first
        for entry in reversed(self._entries):
            if query_lower in entry.content.lower():
                results.append(entry)
                if len(results) >= limit:
                    return results

        # Then scan other session files
        for path in sorted(self._storage_dir.glob("*.jsonl"), key=os.path.getmtime, reverse=True):
            sid = path.stem
            if sid == self._session_id:
                continue  # already searched in-memory
            for entry in reversed(self._load_session_from_disk(sid)):
                if query_lower in entry.content.lower():
                    results.append(entry)
                    if len(results) >= limit:
                        return results
        return results

    # -- session management --------------------------------------------------

    def get_all_sessions(self) -> list[dict]:
        """List all sessions: [{session_id, start_time, entry_count, last_message}]."""
        sessions: list[dict] = []
        for path in sorted(self._storage_dir.glob("*.jsonl"), key=os.path.getmtime, reverse=True):
            sid = path.stem
            entries = self._load_session_from_disk(sid) if sid != self._session_id else self._entries
            if not entries:
                continue
            sessions.append({
                "session_id": sid,
                "start_time": entries[0].timestamp,
                "entry_count": len(entries),
                "last_message": entries[-1].content[:120] if entries else "",
            })
        return sessions

    def delete_session(self, session_id: str) -> bool:
        """Remove a session file from disk. Returns True if deleted."""
        path = self._storage_dir / f"{session_id}.jsonl"
        if path.exists():
            path.unlink()
            if session_id == self._session_id:
                self._entries.clear()
                self._pending_flush.clear()
            return True
        return False

    # -- export --------------------------------------------------------------

    def export_session(self, session_id: str = "", format: str = "json") -> str:
        """Export a session in *json*, *markdown*, or *text* format."""
        entries = self.get_session_entries(session_id)
        if format == "json":
            return json.dumps([e.to_dict() for e in entries], indent=2)
        elif format == "markdown":
            lines: list[str] = [f"# Session {session_id or self._session_id}\n"]
            for e in entries:
                label = e.role.upper()
                if e.tool_name:
                    label = f"TOOL ({e.tool_name})"
                lines.append(f"### {label} [{_fmt_time(e.timestamp)}]\n")
                lines.append(e.content + "\n")
            return "\n".join(lines)
        else:  # text
            lines = []
            for e in entries:
                prefix = e.role.upper()
                if e.tool_name:
                    prefix = f"TOOL({e.tool_name})"
                lines.append(f"[{_fmt_time(e.timestamp)}] {prefix}: {e.content}")
            return "\n".join(lines)

    # -- undo ----------------------------------------------------------------

    def get_undo_stack(self, count: int = 10) -> list[SessionEntry]:
        """Return the last *count* entries (newest first) for potential undo."""
        return list(reversed(self._entries[-count:]))

    def undo_last(self) -> SessionEntry | None:
        """Remove and return the last entry from both memory and pending."""
        if not self._entries:
            return None
        entry = self._entries.pop()
        # Also remove from pending if it has not been flushed yet
        with self._flush_lock:
            try:
                self._pending_flush.remove(entry)
            except ValueError:
                pass  # already flushed
        return entry

    # -- internals -----------------------------------------------------------

    def _load_session_from_disk(self, session_id: str) -> list[SessionEntry]:
        path = self._storage_dir / f"{session_id}.jsonl"
        entries: list[SessionEntry] = []
        if not path.exists():
            return entries
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(SessionEntry.from_dict(json.loads(line)))
                    except (json.JSONDecodeError, KeyError):
                        log.debug("Skipping malformed JSONL line in %s", path)
        except OSError as exc:
            log.warning("Failed to read session %s: %s", session_id, exc)
        return entries


# ---------------------------------------------------------------------------
# HistoryManager — global prompt history across sessions
# ---------------------------------------------------------------------------

class HistoryManager:
    """Manages ``~/.jarvis/history.jsonl`` — a global, cross-session log of
    user prompts (for up-arrow recall and ctrl-r search).
    """

    def __init__(self, history_file: str = ""):
        self._path = Path(history_file) if history_file else JARVIS_HOME / "history.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._pending: list[dict] = []
        self._lock = threading.Lock()

    def add_to_history(
        self, display: str, session_id: str = "", project: str = ""
    ) -> None:
        """Append a prompt to the global history buffer."""
        entry = {
            "display": display,
            "timestamp": time.time(),
            "session_id": session_id,
            "project": project,
        }
        with self._lock:
            self._pending.append(entry)

    def get_history(self, project: str = "", limit: int = 100) -> list[dict]:
        """Read history entries, optionally filtered by *project*, newest first."""
        entries = self._read_all()
        if project:
            entries = [e for e in entries if e.get("project") == project]
        return list(reversed(entries[-limit:]))

    def get_timestamped_history(self, limit: int = 50) -> list[dict]:
        """De-duplicated history for up-arrow browsing, newest first."""
        seen: set[str] = set()
        result: list[dict] = []
        for entry in reversed(self._read_all()):
            display = entry.get("display", "")
            if display in seen:
                continue
            seen.add(display)
            result.append(entry)
            if len(result) >= limit:
                break
        return result

    def flush(self) -> None:
        """Write pending entries to disk with file-based locking."""
        with self._lock:
            if not self._pending:
                return
            batch = list(self._pending)
            self._pending.clear()

        lines = "".join(json.dumps(e) + "\n" for e in batch)
        acquired = False
        lock_path = self._path.with_suffix(".lock")
        lock_fd = None
        try:
            acquired, lock_fd = self._acquire_lock(lock_path)
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(lines)
        except OSError as exc:
            log.warning("Failed to flush history: %s", exc)
        finally:
            if acquired and lock_fd is not None:
                self._release_lock(lock_fd, lock_path)

    # -- locking helpers -----------------------------------------------------

    @staticmethod
    def _acquire_lock(lock_path: Path, timeout: float = 5.0) -> tuple[bool, int | None]:
        """File-based lock using fcntl. Returns (success, fd)."""
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return True, fd
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        os.close(fd)
                        log.warning("Timed out acquiring history lock")
                        return False, None
                    time.sleep(0.05)
        except OSError as exc:
            log.warning("Lock acquisition failed: %s", exc)
            return False, None

    @staticmethod
    def _release_lock(fd: int, lock_path: Path) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    # -- internals -----------------------------------------------------------

    def _read_all(self) -> list[dict]:
        """Read all entries from the history file plus pending buffer."""
        entries: list[dict] = []
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            except OSError as exc:
                log.warning("Failed to read history: %s", exc)
        with self._lock:
            entries.extend(self._pending)
        return entries


# ---------------------------------------------------------------------------
# TranscriptWriter — human-readable session transcripts
# ---------------------------------------------------------------------------

class TranscriptWriter:
    """Writes a human-readable transcript of a session to disk."""

    def __init__(self, session_id: str, transcript_dir: str = ""):
        self._session_id = session_id
        self._dir = Path(transcript_dir) if transcript_dir else JARVIS_HOME / "transcripts"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / f"{session_id}.md"

    def write_turn(self, role: str, content: str, tool_info: dict | None = None) -> None:
        """Append a turn to the transcript file."""
        ts = _fmt_time(time.time())
        label = role.upper()
        if tool_info and tool_info.get("name"):
            label = f"TOOL ({tool_info['name']})"
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(f"### {label} [{ts}]\n\n")
            fh.write(content.rstrip() + "\n\n")
            if tool_info and tool_info.get("args"):
                fh.write(f"```json\n{json.dumps(tool_info['args'], indent=2)}\n```\n\n")

    def get_transcript(self) -> str:
        """Read the full transcript."""
        if self._path.exists():
            return self._path.read_text(encoding="utf-8")
        return ""

    def get_transcript_path(self) -> str:
        return str(self._path)


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_storage: SessionStorage | None = None
_history: HistoryManager | None = None


def get_session_storage(session_id: str = "") -> SessionStorage:
    """Return the module-level SessionStorage singleton (created on first call)."""
    global _storage
    if _storage is None:
        _storage = SessionStorage(session_id=session_id)
    return _storage


def get_history_manager() -> HistoryManager:
    """Return the module-level HistoryManager singleton."""
    global _history
    if _history is None:
        _history = HistoryManager()
    return _history


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_time(ts: float) -> str:
    """Format a Unix timestamp as a compact local-time string."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))

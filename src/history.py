"""Prompt history management with paste content support."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    AsyncGenerator,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

MAX_HISTORY_ITEMS = 100
MAX_PASTED_CONTENT_LENGTH = 1024


@dataclass
class PastedContent:
    id: int
    type: str  # "text" | "image"
    content: str
    media_type: Optional[str] = None
    filename: Optional[str] = None


@dataclass
class StoredPastedContent:
    id: int
    type: str  # "text" | "image"
    content: Optional[str] = None
    content_hash: Optional[str] = None
    media_type: Optional[str] = None
    filename: Optional[str] = None


@dataclass
class HistoryEntry:
    display: str
    pasted_contents: Dict[int, PastedContent] = field(default_factory=dict)


@dataclass
class LogEntry:
    display: str
    pasted_contents: Dict[int, StoredPastedContent] = field(default_factory=dict)
    timestamp: float = 0.0
    project: str = ""
    session_id: Optional[str] = None


@dataclass
class TimestampedHistoryEntry:
    display: str
    timestamp: float
    _entry: LogEntry = field(repr=False)

    async def resolve(self) -> HistoryEntry:
        return await _log_entry_to_history_entry(self._entry)


def get_pasted_text_ref_num_lines(text: str) -> int:
    """Count newlines in text (preserving original behavior)."""
    return len(re.findall(r"\r\n|\r|\n", text))


def format_pasted_text_ref(id: int, num_lines: int) -> str:
    if num_lines == 0:
        return f"[Pasted text #{id}]"
    return f"[Pasted text #{id} +{num_lines} lines]"


def format_image_ref(id: int) -> str:
    return f"[Image #{id}]"


def parse_references(
    input_text: str,
) -> List[Dict[str, Any]]:
    """Parse pasted text/image references from input."""
    pattern = re.compile(
        r"\[(Pasted text|Image|\.\.\.Truncated text) #(\d+)(?: \+\d+ lines)?(\.)*\]"
    )
    results = []
    for match in pattern.finditer(input_text):
        ref_id = int(match.group(2) or "0")
        if ref_id > 0:
            results.append({
                "id": ref_id,
                "match": match.group(0),
                "index": match.start(),
            })
    return results


def expand_pasted_text_refs(
    input_text: str,
    pasted_contents: Dict[int, PastedContent],
) -> str:
    """Replace [Pasted text #N] placeholders with actual content."""
    refs = parse_references(input_text)
    expanded = input_text
    # Process in reverse order to preserve offsets
    for ref in reversed(refs):
        content = pasted_contents.get(ref["id"])
        if content and content.type == "text":
            idx = ref["index"]
            match_len = len(ref["match"])
            expanded = expanded[:idx] + content.content + expanded[idx + match_len:]
    return expanded


async def _resolve_stored_pasted_content(
    stored: StoredPastedContent,
) -> Optional[PastedContent]:
    """Resolve stored paste content to full PastedContent."""
    if stored.content:
        return PastedContent(
            id=stored.id,
            type=stored.type,
            content=stored.content,
            media_type=stored.media_type,
            filename=stored.filename,
        )
    # Hash-based retrieval not implemented in Python version
    return None


async def _log_entry_to_history_entry(entry: LogEntry) -> HistoryEntry:
    """Convert LogEntry to HistoryEntry by resolving paste store references."""
    pasted_contents: Dict[int, PastedContent] = {}
    for id_str, stored in entry.pasted_contents.items():
        resolved = await _resolve_stored_pasted_content(stored)
        if resolved:
            pasted_contents[int(id_str)] = resolved
    return HistoryEntry(display=entry.display, pasted_contents=pasted_contents)


# Module state
_pending_entries: List[LogEntry] = []
_is_writing = False
_last_added_entry: Optional[LogEntry] = None
_skipped_timestamps: Set[float] = set()
_cleanup_registered = False


def _get_history_path() -> str:
    """Get the path to the history file."""
    config_home = os.environ.get(
        "JARVIS_HOME", os.path.expanduser("~/.jarvis")
    )
    return os.path.join(config_home, "history.jsonl")


def _deserialize_log_entry(line: str) -> LogEntry:
    """Parse a JSON line into a LogEntry."""
    data = json.loads(line)
    pasted = {}
    for k, v in data.get("pastedContents", {}).items():
        pasted[int(k)] = StoredPastedContent(
            id=v.get("id", 0),
            type=v.get("type", "text"),
            content=v.get("content"),
            content_hash=v.get("contentHash"),
            media_type=v.get("mediaType"),
            filename=v.get("filename"),
        )
    return LogEntry(
        display=data.get("display", ""),
        pasted_contents=pasted,
        timestamp=data.get("timestamp", 0),
        project=data.get("project", ""),
        session_id=data.get("sessionId"),
    )


async def get_history() -> AsyncGenerator[HistoryEntry, None]:
    """Get history entries for the current project."""
    history_path = _get_history_path()

    # Yield pending entries first (newest first)
    for entry in reversed(_pending_entries):
        if entry.timestamp not in _skipped_timestamps:
            yield await _log_entry_to_history_entry(entry)

    # Read from history file
    try:
        if os.path.exists(history_path):
            with open(history_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            count = 0
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = _deserialize_log_entry(line)
                    if entry.timestamp not in _skipped_timestamps:
                        yield await _log_entry_to_history_entry(entry)
                        count += 1
                        if count >= MAX_HISTORY_ITEMS:
                            return
                except (json.JSONDecodeError, KeyError):
                    continue
    except FileNotFoundError:
        return


def add_to_history(command: Union[HistoryEntry, str]) -> None:
    """Add a command to the prompt history."""
    global _last_added_entry, _cleanup_registered

    skip = os.environ.get("JARVIS_SKIP_PROMPT_HISTORY",
                          os.environ.get("CLAUDE_CODE_SKIP_PROMPT_HISTORY", ""))
    if skip.lower() in ("1", "true", "yes"):
        return

    if isinstance(command, str):
        entry_data = HistoryEntry(display=command)
    else:
        entry_data = command

    stored_pasted: Dict[int, StoredPastedContent] = {}
    for id_key, content in entry_data.pasted_contents.items():
        if content.type == "image":
            continue
        stored_pasted[id_key] = StoredPastedContent(
            id=content.id,
            type=content.type,
            content=content.content if len(content.content) <= MAX_PASTED_CONTENT_LENGTH else None,
            media_type=content.media_type,
            filename=content.filename,
        )

    log_entry = LogEntry(
        display=entry_data.display,
        pasted_contents=stored_pasted,
        timestamp=time.time() * 1000,
        project=os.getcwd(),
    )

    _pending_entries.append(log_entry)
    _last_added_entry = log_entry

    # Flush in background
    _flush_history_sync()


def _flush_history_sync() -> None:
    """Flush pending entries to disk synchronously."""
    global _pending_entries, _is_writing

    if _is_writing or not _pending_entries:
        return

    _is_writing = True
    try:
        history_path = _get_history_path()
        os.makedirs(os.path.dirname(history_path), exist_ok=True)

        entries_to_write = list(_pending_entries)
        _pending_entries.clear()

        with open(history_path, "a", encoding="utf-8") as f:
            for entry in entries_to_write:
                data = {
                    "display": entry.display,
                    "pastedContents": {
                        str(k): {
                            "id": v.id,
                            "type": v.type,
                            **({"content": v.content} if v.content else {}),
                            **({"contentHash": v.content_hash} if v.content_hash else {}),
                            **({"mediaType": v.media_type} if v.media_type else {}),
                            **({"filename": v.filename} if v.filename else {}),
                        }
                        for k, v in entry.pasted_contents.items()
                    },
                    "timestamp": entry.timestamp,
                    "project": entry.project,
                    **({"sessionId": entry.session_id} if entry.session_id else {}),
                }
                f.write(json.dumps(data) + "\n")
    except Exception:
        pass
    finally:
        _is_writing = False


def clear_pending_history_entries() -> None:
    """Clear all pending history entries."""
    global _last_added_entry
    _pending_entries.clear()
    _last_added_entry = None
    _skipped_timestamps.clear()


def remove_last_from_history() -> None:
    """Undo the most recent addToHistory call."""
    global _last_added_entry

    if _last_added_entry is None:
        return

    entry = _last_added_entry
    _last_added_entry = None

    try:
        idx = len(_pending_entries) - 1 - _pending_entries[::-1].index(entry)
        _pending_entries.pop(idx)
    except ValueError:
        _skipped_timestamps.add(entry.timestamp)

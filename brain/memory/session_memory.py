"""Session Memory -- automatic extraction of key session context.

Ported from Claude Code's SessionMemory service. Periodically extracts
structured notes from the conversation (files touched, errors, user requests)
so long sessions maintain coherent context without growing unbounded.

No LLM dependency -- uses heuristic extraction from message/tool history.
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from brain.config import JARVIS_HOME

log = logging.getLogger("jarvis.memory.session")

# ---------------------------------------------------------------------------
# Extraction template
# ---------------------------------------------------------------------------

EXTRACTION_TEMPLATE = '''# Session Memory
## Files Modified
{files_modified}

## Files Read
{files_read}

## Key Actions
{actions}

## Errors Encountered
{errors}

## Recent User Requests
{requests}

## Tool Usage
{tool_usage}
'''

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class SessionMemoryConfig:
    """Tunables for session memory extraction."""

    enabled: bool = True
    init_token_threshold: int = 10_000       # tokens before first extraction
    update_token_threshold: int = 5_000      # token growth between updates
    tool_call_threshold: int = 10            # tool calls between updates
    memory_dir: str = ""                     # defaults to ~/.jarvis/session-memory/
    max_memory_size: int = 10_000            # max chars in memory file


# ---------------------------------------------------------------------------
# SessionMemory
# ---------------------------------------------------------------------------


class SessionMemory:
    """Tracks and extracts structured session context from conversation history.

    Usage::

        sm = SessionMemory(session_id="abc123")
        sm.load()                          # restore prior state if any
        sm.update_counters(tokens=1200, tool_calls=3)
        if sm.should_extract():
            sm.extract_memory(messages)
        context_messages = sm.inject_into_messages(messages)
    """

    def __init__(
        self,
        config: SessionMemoryConfig | None = None,
        session_id: str = "",
    ):
        self._session_id: str = session_id or f"session-{int(time.time())}"
        self._config: SessionMemoryConfig = config or SessionMemoryConfig()

        self._initialized: bool = False
        self._last_extraction_tokens: int = 0
        self._last_extraction_tool_calls: int = 0
        self._total_tokens: int = 0
        self._total_tool_calls: int = 0
        self._memory_content: str = ""
        self._extracting: bool = False  # prevent concurrent extractions

        # Resolve memory directory
        if not self._config.memory_dir:
            self._config.memory_dir = str(JARVIS_HOME / "session-memory")

    # -- counters -----------------------------------------------------------

    def update_counters(self, tokens: int = 0, tool_calls: int = 0) -> None:
        """Increment running token and tool-call counters."""
        self._total_tokens += tokens
        self._total_tool_calls += tool_calls

    # -- threshold checks ---------------------------------------------------

    def should_extract(self) -> bool:
        """Return True when extraction thresholds are met.

        Before first extraction: total_tokens >= init_token_threshold.
        After first extraction:
          (token growth >= update_token_threshold) OR
          (tool-call growth >= tool_call_threshold).
        """
        if not self._config.enabled:
            return False

        if self._extracting:
            return False

        if not self._initialized:
            return self._total_tokens >= self._config.init_token_threshold

        token_growth = self._total_tokens - self._last_extraction_tokens
        tool_growth = self._total_tool_calls - self._last_extraction_tool_calls

        return (
            token_growth >= self._config.update_token_threshold
            or tool_growth >= self._config.tool_call_threshold
        )

    # -- extraction ---------------------------------------------------------

    def extract_memory(
        self,
        messages: list[dict],
        system_prompt: str = "",
    ) -> str:
        """Build structured memory from conversation messages (heuristic, no LLM).

        Scans messages for file paths in tool calls, errors, user requests,
        and tool usage counts, then formats them into markdown.
        """
        if self._extracting:
            return self._memory_content
        self._extracting = True

        try:
            files_modified: set[str] = set()
            files_read: set[str] = set()
            errors: list[str] = []
            user_requests: list[str] = []
            tool_counts: dict[str, int] = {}
            actions: list[str] = []

            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")

                # --- user messages: capture recent requests ----------------
                if role == "user" and isinstance(content, str) and content.strip():
                    user_requests.append(content.strip()[:100])

                # --- tool results (usually role=tool) ----------------------
                if role == "tool":
                    tool_name = msg.get("name", "unknown")
                    tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

                    result_text = content if isinstance(content, str) else str(content)

                    # Detect errors
                    if any(kw in result_text for kw in ("ERROR", "BLOCKED", "Traceback")):
                        snippet = result_text[:200].replace("\n", " ")
                        errors.append(f"[{tool_name}] {snippet}")

                # --- assistant tool_calls ----------------------------------
                if role == "assistant":
                    tool_calls = msg.get("tool_calls", [])
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        t_name = func.get("name", "")
                        tool_counts[t_name] = tool_counts.get(t_name, 0) + 1

                        try:
                            args = json.loads(func.get("arguments", "{}"))
                        except (json.JSONDecodeError, TypeError):
                            args = {}

                        path = args.get("file_path") or args.get("path") or ""

                        if t_name in ("write_file", "edit_file"):
                            if path:
                                files_modified.add(path)
                                actions.append(f"{t_name}: {path}")
                        elif t_name == "read_file":
                            if path:
                                files_read.add(path)
                        elif t_name == "bash":
                            cmd = args.get("command", "")[:120]
                            if cmd:
                                actions.append(f"bash: {cmd}")

            # Keep only last 5 user requests
            recent_requests = user_requests[-5:]

            # Format sections
            fmt_modified = "\n".join(f"- {p}" for p in sorted(files_modified)) or "None"
            fmt_read = "\n".join(f"- {p}" for p in sorted(files_read)) or "None"
            fmt_actions = "\n".join(f"- {a}" for a in actions[-20:]) or "None"
            fmt_errors = "\n".join(f"- {e}" for e in errors[-10:]) or "None"
            fmt_requests = "\n".join(f"- {r}" for r in recent_requests) or "None"
            fmt_tools = "\n".join(
                f"- {name}: {count}" for name, count in sorted(tool_counts.items())
            ) or "None"

            self._memory_content = EXTRACTION_TEMPLATE.format(
                files_modified=fmt_modified,
                files_read=fmt_read,
                actions=fmt_actions,
                errors=fmt_errors,
                requests=fmt_requests,
                tool_usage=fmt_tools,
            )

            # Truncate if over limit
            if len(self._memory_content) > self._config.max_memory_size:
                self._memory_content = self._memory_content[: self._config.max_memory_size]

            # Update bookkeeping
            self._last_extraction_tokens = self._total_tokens
            self._last_extraction_tool_calls = self._total_tool_calls
            self._initialized = True

            self.save()
            return self._memory_content

        finally:
            self._extracting = False

    # -- accessors ----------------------------------------------------------

    def get_memory(self) -> str:
        """Return the current memory content string."""
        return self._memory_content

    def get_memory_path(self) -> str:
        """Return the file path where this session's memory is persisted."""
        return str(Path(self._config.memory_dir) / f"{self._session_id}.md")

    # -- persistence --------------------------------------------------------

    def save(self) -> None:
        """Write memory content to disk."""
        path = Path(self.get_memory_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._memory_content, encoding="utf-8")
        log.debug("Session memory saved to %s (%d chars)", path, len(self._memory_content))

    def load(self) -> None:
        """Load memory content from disk if the file exists."""
        path = Path(self.get_memory_path())
        if path.is_file():
            self._memory_content = path.read_text(encoding="utf-8")
            self._initialized = bool(self._memory_content)
            log.debug("Session memory loaded from %s (%d chars)", path, len(self._memory_content))

    # -- injection ----------------------------------------------------------

    def inject_into_messages(self, messages: list[dict]) -> list[dict]:
        """Prepend session memory as context after the system prompt.

        Returns a *new* list -- the input is not mutated.
        """
        if not self._memory_content:
            return list(messages)

        memory_msg = {
            "role": "system",
            "content": f"[Session Memory]\n{self._memory_content}",
        }

        result: list[dict] = []
        injected = False
        for msg in messages:
            result.append(msg)
            # Inject right after the first system message
            if not injected and msg.get("role") == "system":
                result.append(memory_msg)
                injected = True

        # If there was no system message, prepend
        if not injected:
            result.insert(0, memory_msg)

        return result


# ---------------------------------------------------------------------------
# Module-level convenience singleton
# ---------------------------------------------------------------------------

_session_memory: SessionMemory | None = None


def get_session_memory(session_id: str = "") -> SessionMemory:
    """Return (and lazily create) the module-level SessionMemory singleton."""
    global _session_memory
    if _session_memory is None:
        _session_memory = SessionMemory(session_id=session_id)
        _session_memory.load()
    return _session_memory

"""
Collapse consecutive read/search tool calls into compact summaries.

Ported from Claude Code's collapseReadSearch.ts — adapted for JARVIS's
tool naming conventions and Python dataclasses.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Tool classification sets
# ---------------------------------------------------------------------------

# Tools whose consecutive calls can be collapsed into a summary
READ_SEARCH_TOOLS: set[str] = {
    "read_file",
    "search_files",
    "web_search",
    "web_fetch",
    "view_screen",
    "tool_search",
    "think",
}

# Tools that modify state — never collapse
WRITE_TOOLS: set[str] = {"write_file", "edit_file", "bash"}

# Tools that are silent/meta — absorbed into a group without incrementing counts
META_TOOLS: set[str] = {"tool_search", "think"}

# ---------------------------------------------------------------------------
# Read-only bash detection (lightweight, avoids importing full validator)
# ---------------------------------------------------------------------------

_READ_ONLY_PREFIXES: tuple[str, ...] = (
    "ls", "cat", "head", "tail", "less", "more",
    "file", "stat", "wc", "du", "df",
    "grep", "egrep", "fgrep", "rg", "ag",
    "find", "fd", "fdfind", "locate", "which", "whereis", "type",
    "sort", "uniq", "tr", "cut", "paste", "join", "comm",
    "diff", "cmp",
    "md5sum", "sha256sum", "sha1sum", "sha512sum",
    "strings", "xxd", "od", "hexdump",
    "pwd", "readlink", "realpath", "basename", "dirname",
    "echo", "printf", "date", "whoami", "uname", "id",
    "hostname", "uptime", "env", "printenv",
    "true", "false", "test",
    "jq", "yq", "xq", "tree", "column",
    "man", "help", "info", "nproc", "arch",
)

_READ_ONLY_GIT_SUBS: set[str] = {
    "log", "diff", "status", "show", "branch", "tag",
    "remote", "config", "rev-parse", "ls-files", "ls-tree",
    "blame", "shortlog", "describe", "rev-list",
    "for-each-ref", "reflog", "cat-file",
}


def _is_read_only_bash(command: str) -> bool:
    """Return True if *command* is very likely a read-only shell invocation."""
    cmd = command.strip()
    if not cmd:
        return False

    # Strip leading sudo
    if cmd.startswith("sudo "):
        cmd = cmd[5:].lstrip()

    # Try to extract the first token
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    if not tokens:
        return False

    first = tokens[0].rsplit("/", 1)[-1]  # basename

    # git sub-command check
    if first == "git" and len(tokens) >= 2:
        return tokens[1] in _READ_ONLY_GIT_SUBS

    return first in _READ_ONLY_PREFIXES


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ToolCallGroup:
    """A consecutive run of tool calls sharing the same read/write nature."""

    tool_calls: list[dict] = field(default_factory=list)
    group_type: str = "read_search"          # "read_search" | "write" | "mixed"
    start_index: int = 0
    end_index: int = 0
    is_collapsible: bool = False


@dataclass
class CollapsedSummary:
    """Compact representation of a collapsed group."""

    tool_count: int = 0
    files_read: list[str] = field(default_factory=list)
    searches_done: list[str] = field(default_factory=list)
    web_fetches: list[str] = field(default_factory=list)
    summary_line: str = ""


# ---------------------------------------------------------------------------
# classify_tool
# ---------------------------------------------------------------------------

def classify_tool(name: str, args: dict | None = None) -> str:
    """Classify a tool call as read / search / write / meta / other.

    Parameters
    ----------
    name:
        Tool name (e.g. ``"read_file"``).
    args:
        Tool arguments dict — only inspected for ``bash`` to determine
        if the command is read-only.

    Returns
    -------
    One of ``"read"``, ``"search"``, ``"write"``, ``"meta"``, ``"other"``.
    """
    if args is None:
        args = {}

    if name == "read_file":
        return "read"
    if name == "view_screen":
        return "read"
    if name == "search_files":
        return "search"
    if name in ("web_search", "web_fetch"):
        return "search"
    if name in ("write_file", "edit_file"):
        return "write"
    if name == "bash":
        command = args.get("command", "")
        return "read" if _is_read_only_bash(command) else "write"
    if name in META_TOOLS:
        return "meta"
    return "other"


# ---------------------------------------------------------------------------
# group_tool_calls
# ---------------------------------------------------------------------------

def group_tool_calls(events: list[dict]) -> list[ToolCallGroup]:
    """Group consecutive tool-call events by their read/write nature.

    Each event dict is expected to have at least::

        {"name": str, "args": dict, "result": ...}

    Returns a list of :class:`ToolCallGroup` instances.
    """
    if not events:
        return []

    groups: list[ToolCallGroup] = []
    current: ToolCallGroup | None = None

    for idx, ev in enumerate(events):
        name = ev.get("name", "")
        args = ev.get("args", {}) or {}
        cls = classify_tool(name, args)

        is_read_search = cls in ("read", "search", "meta")

        if current is None:
            current = ToolCallGroup(
                tool_calls=[ev],
                group_type="read_search" if is_read_search else "write",
                start_index=idx,
                end_index=idx,
                is_collapsible=is_read_search,
            )
        else:
            cur_is_rs = current.group_type == "read_search"
            if is_read_search == cur_is_rs:
                # Same nature — extend current group
                current.tool_calls.append(ev)
                current.end_index = idx
                if not is_read_search:
                    current.is_collapsible = False
            else:
                # Nature changed — close current, start new
                groups.append(current)
                current = ToolCallGroup(
                    tool_calls=[ev],
                    group_type="read_search" if is_read_search else "write",
                    start_index=idx,
                    end_index=idx,
                    is_collapsible=is_read_search,
                )

    if current is not None:
        groups.append(current)

    return groups


# ---------------------------------------------------------------------------
# collapse_group
# ---------------------------------------------------------------------------

def collapse_group(group: ToolCallGroup) -> CollapsedSummary:
    """Generate a :class:`CollapsedSummary` for a collapsible group."""
    files_read: list[str] = []
    searches_done: list[str] = []
    web_fetches: list[str] = []
    tool_count = 0

    for tc in group.tool_calls:
        name = tc.get("name", "")
        args = tc.get("args", {}) or {}

        # Meta tools are absorbed silently — don't count them
        if name in META_TOOLS:
            continue

        tool_count += 1

        if name == "read_file":
            path = args.get("file_path", args.get("path", ""))
            if path:
                files_read.append(path)

        elif name == "search_files":
            pattern = args.get("pattern", args.get("query", ""))
            if pattern:
                searches_done.append(pattern)

        elif name == "web_search":
            query = args.get("query", args.get("pattern", ""))
            if query:
                searches_done.append(query)

        elif name == "web_fetch":
            url = args.get("url", "")
            if url:
                web_fetches.append(url)

        elif name == "view_screen":
            files_read.append("<screen capture>")

        elif name == "bash":
            cmd = args.get("command", "")
            hint = get_bash_hint(cmd)
            if hint:
                searches_done.append(hint)

    # ── Build summary line ──────────────────────────────────────────
    parts: list[str] = []

    if files_read:
        n = len(files_read)
        if n == 1:
            short = files_read[0].rsplit("/", 1)[-1]
            parts.append(f"Read {short}")
        else:
            parts.append(f"Read {n} files")

    if searches_done:
        n = len(searches_done)
        if n == 1:
            parts.append(f"searched '{searches_done[0]}'")
        else:
            parts.append(f"ran {n} searches")

    if web_fetches:
        n = len(web_fetches)
        if n == 1:
            parts.append(f"fetched {web_fetches[0]}")
        else:
            parts.append(f"fetched {n} URLs")

    summary_line = ", ".join(parts) if parts else f"{tool_count} tool calls"

    return CollapsedSummary(
        tool_count=tool_count,
        files_read=files_read,
        searches_done=searches_done,
        web_fetches=web_fetches,
        summary_line=summary_line,
    )


# ---------------------------------------------------------------------------
# format_collapsed_events  (main entry point)
# ---------------------------------------------------------------------------

def format_collapsed_events(
    events: list[dict],
    verbose: bool = False,
) -> list[dict]:
    """Return *events* with collapsible read/search groups replaced by summaries.

    Parameters
    ----------
    events:
        List of tool-call event dicts, each with at least
        ``{"name": str, "args": dict, "result": ...}``.
    verbose:
        When ``True``, no collapsing is performed — events are returned as-is.

    Returns
    -------
    A new list where collapsible groups become a single summary event::

        {
            "type": "collapsed",
            "summary": "Read 3 files, searched 2 patterns",
            "count": 5,
            "details": [ ... original events ... ],
        }

    Write and non-collapsible events are preserved unchanged.
    """
    if verbose or not events:
        return list(events)

    groups = group_tool_calls(events)
    result: list[dict] = []

    for group in groups:
        if group.is_collapsible and len(group.tool_calls) > 1:
            summary = collapse_group(group)
            result.append({
                "type": "collapsed",
                "summary": summary.summary_line,
                "count": summary.tool_count,
                "details": group.tool_calls,
            })
        else:
            # Non-collapsible or single-tool group — keep originals
            result.extend(group.tool_calls)

    return result


# ---------------------------------------------------------------------------
# get_bash_hint
# ---------------------------------------------------------------------------

def get_bash_hint(command: str, max_len: int = 60) -> str:
    """Extract a human-readable hint from a bash command string.

    - Strips ``sudo`` prefix
    - Collapses internal whitespace
    - Truncates to *max_len* characters (with ellipsis)
    """
    cmd = command.strip()
    if not cmd:
        return ""

    # Strip sudo
    if cmd.startswith("sudo "):
        cmd = cmd[5:].lstrip()

    # Collapse whitespace within each line, join lines
    lines = [" ".join(line.split()) for line in cmd.splitlines() if line.strip()]
    hint = " && ".join(lines)

    if len(hint) > max_len:
        return hint[: max_len - 1] + "\u2026"
    return hint

"""Bridge JARVIS's tool registry into the ACP ``tool_call`` shape.

Each ACP ``session/update`` for a tool invocation carries a ``ToolKind``
(``read`` / ``edit`` / ``execute`` / ``search`` / ``fetch`` / ``think`` /
``other``) plus content blocks: text, image, or — for edits — a diff
preview. We map JARVIS's tool names onto those kinds, build a human
title, and render the tool's structured JSON result back into something
the IDE can display.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

import acp
from acp.schema import (
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
    ToolKind,
)


# ---------------------------------------------------------------------------
# JARVIS tool name -> ACP ToolKind
# ---------------------------------------------------------------------------

TOOL_KIND_MAP: Dict[str, ToolKind] = {
    # Files
    "read_file": "read",
    "write_file": "edit",
    "patch": "edit",
    "search_files": "search",
    "code_search": "search",
    "find_definitions": "search",
    # Execution
    "terminal": "execute",
    "execute_code": "execute",
    "process": "execute",
    # Web
    "web_search": "fetch",
    "web_extract": "fetch",
    "web_fetch": "fetch",
    # Browser
    "browser_task": "fetch",
    "browser_navigate": "fetch",
    "browser_click": "execute",
    "browser_type": "execute",
    "browser_snapshot": "read",
    "browser_vision": "read",
    "browser_get_images": "read",
    # Vision / generation
    "vision_analyze": "read",
    "image_generate": "execute",
    "video_generate": "execute",
    "text_to_speech": "execute",
    "computer_use": "execute",
    # Meta / session
    "todo": "other",
    "memory": "other",
    "session_search": "search",
    "schedule": "other",
    "clarify": "other",
    "vuln_check": "search",
    # Skills
    "skill_view": "read",
    "skills_list": "read",
    "skill_manage": "edit",
    # Home Assistant / external
    "ha_list_entities": "fetch",
    "ha_get_state": "fetch",
    "ha_list_services": "fetch",
    "ha_call_service": "execute",
}


# Tools whose JSON results are well-shaped enough that the structured
# completion path produces something readable. Anything not listed here
# falls back to the generic JSON-pretty renderer with a text fence.
_POLISHED_TOOLS = {
    "read_file", "write_file", "patch", "search_files", "code_search",
    "find_definitions", "terminal", "execute_code", "process",
    "web_search", "web_extract", "web_fetch",
    "browser_task", "browser_navigate", "browser_snapshot", "browser_vision",
    "browser_get_images", "todo", "memory", "session_search", "schedule",
    "clarify", "vuln_check", "skill_view", "skills_list", "skill_manage",
    "vision_analyze", "image_generate", "video_generate", "computer_use",
}


def get_tool_kind(tool_name: str) -> ToolKind:
    """Return the ACP ToolKind for a JARVIS tool; defaults to ``other``."""
    return TOOL_KIND_MAP.get(tool_name, "other")


def make_tool_call_id() -> str:
    """Generate a unique ACP tool_call id."""
    return f"tc-{uuid.uuid4().hex[:12]}"


def _text(content: str) -> Any:
    """Wrap ``content`` as an ACP text tool-content block."""
    return acp.tool_content(acp.text_block(content))


def _truncate_text(text: str, limit: int = 5000) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 80)] + f"\n... ({len(text)} chars total, truncated)"


def _fenced_text(text: str, language: str = "") -> str:
    """Markdown fence that survives backticks in *text*."""
    longest = max((len(run) for run in text.split("`")[1::2]), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{text}\n{fence}"


def _json_loads_maybe(value: Optional[str]) -> Any:
    """Best-effort JSON parse; tolerates a trailing human hint after the object."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        decoded, _ = json.JSONDecoder().raw_decode(text)
        return decoded
    except Exception:
        return None


def build_tool_title(tool_name: str, args: Dict[str, Any]) -> str:
    """Return a one-line title for a tool invocation."""
    if tool_name == "terminal":
        cmd = str(args.get("command") or "")
        if len(cmd) > 80:
            cmd = cmd[:77] + "..."
        return f"terminal: {cmd}"
    if tool_name == "read_file":
        return f"read: {args.get('path') or '?'}"
    if tool_name == "write_file":
        return f"write: {args.get('path') or '?'}"
    if tool_name == "patch":
        mode = args.get("mode") or "replace"
        return f"patch ({mode}): {args.get('path') or '?'}"
    if tool_name in ("search_files", "code_search"):
        return f"search: {args.get('pattern') or args.get('query') or '?'}"
    if tool_name == "find_definitions":
        return f"find_definitions: {args.get('symbol') or args.get('query') or '?'}"
    if tool_name == "web_search":
        return f"web search: {args.get('query') or '?'}"
    if tool_name == "web_extract":
        urls = args.get("urls") or []
        if urls:
            return f"extract: {urls[0]}" + (f" (+{len(urls)-1})" if len(urls) > 1 else "")
        return "web extract"
    if tool_name == "web_fetch":
        return f"fetch: {args.get('url') or '?'}"
    if tool_name == "browser_task":
        task = str(args.get("task") or "").strip()
        if len(task) > 80:
            task = task[:77] + "..."
        return f"browser: {task}"
    if tool_name == "browser_navigate":
        return f"navigate: {args.get('url') or '?'}"
    if tool_name == "execute_code":
        code = str(args.get("code") or "").strip()
        first = next((ln.strip() for ln in code.splitlines() if ln.strip()), "")
        if len(first) > 70:
            first = first[:67] + "..."
        return f"python: {first}" if first else "python code"
    if tool_name == "computer_use":
        req = str(args.get("request") or "").strip()
        if len(req) > 80:
            req = req[:77] + "..."
        return f"computer_use: {req}"
    if tool_name == "todo":
        items = args.get("todos")
        if isinstance(items, list):
            return f"todo ({len(items)} item{'s' if len(items) != 1 else ''})"
        return "todo"
    if tool_name == "memory":
        action = str(args.get("action") or "manage").strip() or "manage"
        target = str(args.get("target") or "memory").strip() or "memory"
        return f"memory {action}: {target}"
    if tool_name == "session_search":
        q = str(args.get("query") or "").strip()
        return f"session search: {q}" if q else "recent sessions"
    if tool_name == "skill_view":
        return f"skill: {args.get('name') or '?'}"
    if tool_name == "skills_list":
        cat = str(args.get("category") or "").strip()
        return f"skills list ({cat})" if cat else "skills list"
    if tool_name == "skill_manage":
        action = str(args.get("action") or "manage").strip() or "manage"
        return f"skill {action}: {args.get('name') or '?'}"
    return tool_name


def _tool_result_failed(result: Optional[str], tool_name: Optional[str] = None) -> bool:
    """Conservative detector for tool-level failure in a result string.

    Used to set ACP completion status to ``failed`` vs ``completed``.
    Errors raised from the wrapped handler (``_adapter.py``) prefix with
    ``"Error: <tool> failed: ..."`` — that's the canonical signal a tool
    blew up versus normal text that happens to contain "error".
    """
    if isinstance(result, str) and result.startswith("Error: ") and " failed: " in result:
        return True
    data = _json_loads_maybe(result)
    if not isinstance(data, dict):
        return False
    for key in ("success", "ok"):
        if data.get(key) is False:
            return True
    exit_code = data.get("exit_code", data.get("returncode"))
    if isinstance(exit_code, int) and exit_code != 0:
        return True
    if tool_name in _POLISHED_TOOLS and data.get("error") and not data.get("content"):
        return True
    return False


# ---------------------------------------------------------------------------
# Result formatters — convert JARVIS tool JSON to readable ACP content
# ---------------------------------------------------------------------------


def _format_read_file_result(result: Optional[str], args: Optional[Dict[str, Any]]) -> Optional[str]:
    data = _json_loads_maybe(result)
    if not isinstance(data, dict):
        return None
    if data.get("error") and not data.get("content"):
        return f"Read failed: {data.get('error')}"
    content = data.get("content")
    if not isinstance(content, str):
        return None
    path = str((args or {}).get("path") or data.get("path") or "file").strip()
    header = f"Read {path}"
    total = data.get("total_lines")
    if total is not None:
        header += f" ({total} lines)"
    return _truncate_text(f"{header}\n\n{_fenced_text(content)}", limit=8000)


def _format_search_result(result: Optional[str]) -> Optional[str]:
    data = _json_loads_maybe(result)
    if not isinstance(data, dict):
        return None
    matches = data.get("matches") or data.get("results")
    if isinstance(matches, list):
        total = data.get("total_count", len(matches))
        shown = min(len(matches), 12)
        lines = [
            f"Search results: {total} match{'es' if total != 1 else ''}, showing {shown}",
            "",
        ]
        for m in matches[:shown]:
            if isinstance(m, dict):
                path = m.get("path") or m.get("file") or "?"
                line = m.get("line") or m.get("line_number")
                snippet = str(m.get("content") or m.get("text") or "").strip()
                loc = f"{path}:{line}" if line else str(path)
                lines.append(f"- {loc}")
                if snippet:
                    lines.append(f"  {_truncate_text(snippet, 240)}")
            else:
                lines.append(f"- {m}")
        return _truncate_text("\n".join(lines), limit=7000)
    files = data.get("files")
    if isinstance(files, list):
        total = data.get("total_count", len(files))
        shown = min(len(files), 30)
        lines = [f"Files: {total}, showing {shown}", ""]
        lines.extend(f"- {p}" for p in files[:shown])
        return _truncate_text("\n".join(lines), limit=7000)
    return None


def _format_execute_code_result(result: Optional[str]) -> Optional[str]:
    data = _json_loads_maybe(result)
    if not isinstance(data, dict):
        return result if isinstance(result, str) and result.strip() else None
    parts = []
    exit_code = data.get("exit_code")
    parts.append(f"Exit code: {exit_code}" if exit_code is not None else "Execution complete")
    output = str(data.get("output") or "")
    error = str(data.get("error") or data.get("stderr") or "")
    if output:
        parts.extend(["", "Output:", output])
    if error:
        parts.extend(["", "Error:", error])
    return _truncate_text("\n".join(parts), limit=5000)


def _format_terminal_result(result: Optional[str]) -> Optional[str]:
    return _format_execute_code_result(result)


def _format_todo_result(result: Optional[str]) -> Optional[str]:
    data = _json_loads_maybe(result)
    if not isinstance(data, dict) or not isinstance(data.get("todos"), list):
        return None
    icon = {"completed": "[x]", "in_progress": "[~]", "pending": "[ ]", "cancelled": "[-]"}
    lines = ["Todo list", ""]
    for item in data["todos"]:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "pending")
        content = str(item.get("content") or item.get("id") or "").strip()
        if content:
            lines.append(f"- {icon.get(status, '-')} {content}")
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    if summary:
        lines.extend([
            "",
            f"{summary.get('completed', 0)} done, "
            f"{summary.get('in_progress', 0)} active, "
            f"{summary.get('pending', 0)} pending",
        ])
    return "\n".join(lines)


def _format_edit_result(tool_name: str, result: Optional[str], args: Optional[Dict[str, Any]]) -> Optional[str]:
    data = _json_loads_maybe(result)
    path = str((args or {}).get("path") or "file").strip()
    if isinstance(data, dict):
        if data.get("success") is False or data.get("error"):
            return f"{tool_name} failed for {path}: {data.get('error', 'unknown error')}"
        msg = str(data.get("message") or "").strip()
        replacements = data.get("replacements") or data.get("replacement_count")
        lines = [f"{tool_name} completed" + (f" for `{path}`" if path else "")]
        if msg:
            lines.append(msg)
        if replacements is not None:
            lines.append(f"Replacements: {replacements}")
        return "\n".join(lines)
    if isinstance(result, str) and result.strip():
        return _truncate_text(result, limit=3000)
    return f"{tool_name} completed" + (f" for `{path}`" if path else "")


def _format_memory_result(result: Optional[str], args: Optional[Dict[str, Any]]) -> Optional[str]:
    data = _json_loads_maybe(result)
    if not isinstance(data, dict):
        return None
    action = str((args or {}).get("action") or "memory").strip() or "memory"
    target = str(data.get("target") or (args or {}).get("target") or "memory")
    if data.get("success") is False:
        return f"Memory {action} failed ({target}): {data.get('error', 'unknown error')}"
    lines = [f"Memory {action} ({target})"]
    if data.get("message"):
        lines.append(str(data.get("message")))
    preview = str((args or {}).get("content") or "").strip()
    if preview:
        lines.append(f"Preview: {_truncate_text(preview, 240)}")
    return "\n".join(lines)


def _format_web_search_result(result: Optional[str]) -> Optional[str]:
    data = _json_loads_maybe(result)
    if not isinstance(data, dict):
        return None
    web = data.get("results") or (data.get("data") or {}).get("web") if isinstance(data.get("data"), dict) else data.get("results")
    if not isinstance(web, list):
        return None
    lines = [f"Web results: {len(web)}"]
    for item in web[:10]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("url") or "result").strip()
        url = str(item.get("url") or "").strip()
        desc = str(item.get("description") or item.get("snippet") or "").strip()
        lines.append(f"- {title}" + (f" -- {url}" if url else ""))
        if desc:
            lines.append(f"  {_truncate_text(desc, 240)}")
    return _truncate_text("\n".join(lines), limit=5000)


def _format_browser_result(tool_name: str, result: Optional[str], args: Optional[Dict[str, Any]]) -> Optional[str]:
    data = _json_loads_maybe(result)
    if not isinstance(data, dict):
        return result if isinstance(result, str) and result.strip() else None
    if data.get("success") is False or data.get("error"):
        return f"{tool_name} failed: {data.get('error', 'unknown error')}"
    title = str(data.get("title") or data.get("url") or data.get("status") or tool_name)
    text = str(
        data.get("text")
        or data.get("content")
        or data.get("snapshot")
        or data.get("analysis")
        or data.get("message")
        or ""
    ).strip()
    lines = [title]
    if data.get("url") and data.get("url") != title:
        lines.append(str(data.get("url")))
    if text:
        lines.extend(["", _truncate_text(text, 4000)])
    return _truncate_text("\n".join(lines), limit=6000)


def _format_generic(tool_name: str, result: Optional[str]) -> Optional[str]:
    """Last-resort renderer: pretty-print JSON or return text as-is."""
    data = _json_loads_maybe(result)
    if isinstance(data, dict):
        if data.get("success") is False or data.get("error"):
            return f"{tool_name} failed: {data.get('error', 'unknown error')}"
        try:
            return _truncate_text(json.dumps(data, indent=2, default=str), limit=5000)
        except Exception:
            pass
    if isinstance(result, str) and result.strip():
        return _truncate_text(result, limit=5000)
    return None


def _build_polished_completion_content(
    tool_name: str,
    result: Optional[str],
    function_args: Optional[Dict[str, Any]],
) -> Optional[List[Any]]:
    """Try each polished formatter; fall back to the generic renderer."""
    formatter = {
        "read_file": lambda: _format_read_file_result(result, function_args),
        "write_file": lambda: _format_edit_result(tool_name, result, function_args),
        "patch": lambda: _format_edit_result(tool_name, result, function_args),
        "search_files": lambda: _format_search_result(result),
        "code_search": lambda: _format_search_result(result),
        "find_definitions": lambda: _format_search_result(result),
        "execute_code": lambda: _format_execute_code_result(result),
        "terminal": lambda: _format_terminal_result(result),
        "todo": lambda: _format_todo_result(result),
        "memory": lambda: _format_memory_result(result, function_args),
        "web_search": lambda: _format_web_search_result(result),
        "web_extract": lambda: _format_browser_result(tool_name, result, function_args),
        "web_fetch": lambda: _format_browser_result(tool_name, result, function_args),
        "browser_task": lambda: _format_browser_result(tool_name, result, function_args),
        "browser_navigate": lambda: _format_browser_result(tool_name, result, function_args),
        "browser_snapshot": lambda: _format_browser_result(tool_name, result, function_args),
        "browser_vision": lambda: _format_browser_result(tool_name, result, function_args),
    }.get(tool_name)
    text = formatter() if formatter else _format_generic(tool_name, result)
    if not text:
        return None
    return [_text(text)]


# ---------------------------------------------------------------------------
# Build ACP events (the public surface used by ``events.py`` / ``server.py``)
# ---------------------------------------------------------------------------


def extract_locations(arguments: Dict[str, Any]) -> List[ToolCallLocation]:
    """Pull filesystem locations out of tool arguments."""
    locations: List[ToolCallLocation] = []
    path = arguments.get("path")
    if path:
        line = arguments.get("offset") or arguments.get("line")
        locations.append(ToolCallLocation(path=str(path), line=line))
    return locations


def build_tool_start(
    tool_call_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    *,
    edit_diff: Any = None,
) -> ToolCallStart:
    """Create a ``ToolCallStart`` event for a JARVIS tool invocation."""
    kind = get_tool_kind(tool_name)
    title = build_tool_title(tool_name, arguments)
    locations = extract_locations(arguments)

    if tool_name in ("patch", "write_file"):
        if edit_diff is not None:
            content = [
                acp.tool_diff_content(
                    path=edit_diff.path,
                    old_text=edit_diff.old_text,
                    new_text=edit_diff.new_text,
                )
            ]
        else:
            path = arguments.get("path") or "file"
            content = [_text(f"Preparing edit for {path}. Approval prompt will show the diff.")]
        return acp.start_tool_call(
            tool_call_id, title, kind=kind, content=content, locations=locations,
        )

    if tool_name == "terminal":
        command = arguments.get("command") or ""
        return acp.start_tool_call(
            tool_call_id, title, kind=kind, content=[_text(f"$ {command}")], locations=locations,
        )

    if tool_name == "execute_code":
        code = str(arguments.get("code") or "").strip()
        preview = code[:1200] + (f"\n... ({len(code)} chars, truncated)" if len(code) > 1200 else "")
        body = f"```python\n{preview}\n```" if preview else "Python code"
        return acp.start_tool_call(
            tool_call_id, title, kind=kind, content=[_text(body)], locations=locations,
        )

    if tool_name == "read_file":
        # The title + location already identify the file. Skipping the
        # start-content block means the IDE shows the file body alone on
        # completion, not a synthetic "Reading ..." header above it.
        return acp.start_tool_call(
            tool_call_id, title, kind=kind, content=None, locations=locations,
        )

    if tool_name in ("web_search", "web_extract", "web_fetch"):
        return acp.start_tool_call(
            tool_call_id, title, kind=kind, content=None, locations=locations,
        )

    if tool_name == "todo":
        items = arguments.get("todos")
        if isinstance(items, list):
            preview_lines = ["Updating todo list", ""]
            for item in items[:8]:
                if isinstance(item, dict):
                    preview_lines.append(
                        f"- {item.get('status', 'pending')}: {item.get('content', item.get('id', ''))}"
                    )
            if len(items) > 8:
                preview_lines.append(f"... {len(items) - 8} more")
            content = [_text("\n".join(preview_lines))]
        else:
            content = [_text("Reading todo list")]
        return acp.start_tool_call(
            tool_call_id, title, kind=kind, content=content, locations=locations,
        )

    # Generic fallback — show the arguments as pretty JSON.
    if arguments:
        try:
            body = json.dumps(arguments, indent=2, default=str)
        except (TypeError, ValueError):
            body = str(arguments)
        return acp.start_tool_call(
            tool_call_id, title, kind=kind,
            content=[_text(_truncate_text(body, 1200))],
            locations=locations,
            raw_input=None if tool_name in _POLISHED_TOOLS else arguments,
        )
    return acp.start_tool_call(
        tool_call_id, title, kind=kind, content=None, locations=locations,
    )


def build_tool_complete(
    tool_call_id: str,
    tool_name: str,
    result: Optional[str] = None,
    function_args: Optional[Dict[str, Any]] = None,
    snapshot: Any = None,
) -> ToolCallProgress:
    """Create a ``ToolCallProgress`` event for a completed JARVIS tool call."""
    kind = get_tool_kind(tool_name)
    content = _build_polished_completion_content(tool_name, result, function_args)
    if content is None and isinstance(result, str) and result.strip():
        content = [_text(_truncate_text(result, 5000))]
    is_structured = isinstance(_json_loads_maybe(result), (dict, list))
    return acp.update_tool_call(
        tool_call_id,
        kind=kind,
        status="failed" if _tool_result_failed(result, tool_name) else "completed",
        content=content,
        raw_output=None if (tool_name in _POLISHED_TOOLS or is_structured) else result,
    )

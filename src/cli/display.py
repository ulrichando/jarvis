"""JARVIS CLI Display — clean, modern tool display.

Tool calls show icons + compact descriptions.
Results are collapsible with line counts.
Diffs are colored. Permissions show previews.
Footer shows tokens + cost + elapsed time.
"""

import difflib
import os

from src.constants.figures import (
    LIGHTNING_BOLT, PLAY_ICON, PAUSE_ICON,
    BLACK_CIRCLE, BLOCKQUOTE_BAR, HEAVY_HORIZONTAL,
    DIAMOND_OPEN, DIAMOND_FILLED, REFERENCE_MARK, FLAG_ICON,
    EFFORT_LOW, EFFORT_MEDIUM, EFFORT_HIGH, EFFORT_MAX,
    REFRESH_ARROW, FORK_GLYPH,
)

# ── ANSI Codes ──
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
GREY = "\033[90m"
WHITE = "\033[97m"

# Tool icons by category -- uses unicode figures from src/constants/figures
TOOL_ICONS = {
    "bash": LIGHTNING_BOLT,         # ↯
    "read_file": DIAMOND_OPEN,      # ◇
    "write_file": DIAMOND_FILLED,   # ◆
    "edit_file": DIAMOND_FILLED,    # ◆
    "search_files": REFERENCE_MARK, # ※
    "web_search": REFRESH_ARROW,    # ↻
    "web_fetch": REFRESH_ARROW,     # ↻
    "web_api": REFRESH_ARROW,       # ↻
    "think": BLACK_CIRCLE,          # ●
    "dispatch": FORK_GLYPH,         # ⑂
    "tool_search": REFERENCE_MARK,  # ※
    "computer_use": PLAY_ICON,      # ▶
    "view_screen": PLAY_ICON,       # ▶
    "database": FLAG_ICON,          # ⚑
}

# Effort level display icons (exported for CLI use)
EFFORT_ICONS = {
    "low": EFFORT_LOW,
    "medium": EFFORT_MEDIUM,
    "high": EFFORT_HIGH,
    "max": EFFORT_MAX,
}


def _tw() -> int:
    """Get terminal width."""
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def format_tokens(n: int) -> str:
    """Format token count: 1234 -> '1.2K', 123456 -> '123K'."""
    if n < 1000:
        return str(n)
    if n < 10000:
        return f"{n / 1000:.1f}K"
    if n < 1000000:
        return f"{n // 1000}K"
    return f"{n / 1000000:.1f}M"


def _shorten_path(path: str, max_len: int = 50) -> str:
    """Shorten a file path for display: /home/user/project/src/foo.py -> src/foo.py"""
    if not path:
        return ""
    # Try to make relative to CWD
    try:
        cwd = os.getcwd()
        if path.startswith(cwd):
            path = path[len(cwd):].lstrip("/")
    except Exception:
        pass
    # Truncate middle if still too long
    if len(path) > max_len:
        half = (max_len - 3) // 2
        path = path[:half] + "…" + path[-half:]
    return path


# ── Tool Display ──

def tool_call_line(name: str, args: dict) -> str:
    """Tool call with icon + description. Clean and informative."""
    icon = TOOL_ICONS.get(name, "⚙️")

    if name == "bash":
        cmd = args.get("command", "")
        # Strip sudo prefix for display
        display_cmd = cmd
        if display_cmd.startswith("sudo "):
            display_cmd = display_cmd[5:]
        if len(display_cmd) > 80:
            display_cmd = display_cmd[:77] + "..."
        return f"  {icon} {DIM}Bash{RESET} {display_cmd}"

    elif name == "read_file":
        path = _shorten_path(args.get("path", ""))
        offset = args.get("offset", "")
        limit = args.get("limit", "")
        range_info = ""
        if offset or limit:
            range_info = f" {DIM}(lines {offset or 1}-{(offset or 1) + (limit or 200)}){RESET}"
        return f"  {icon} {DIM}Read{RESET} {path}{range_info}"

    elif name == "write_file":
        path = _shorten_path(args.get("path", ""))
        content = args.get("content", "")
        lines = content.count("\n") + 1 if content else 0
        return f"  {icon} {DIM}Write{RESET} {path} {DIM}({lines} lines){RESET}"

    elif name == "edit_file":
        path = _shorten_path(args.get("path", ""))
        old = args.get("old_string", "")
        old_preview = old.split("\n")[0][:40] if old else ""
        return f"  {icon} {DIM}Edit{RESET} {path}" + (f" {DIM}'{old_preview}'{RESET}" if old_preview else "")

    elif name == "search_files":
        pattern = args.get("pattern", "")
        path = args.get("path", ".")
        mode = args.get("mode", "glob")
        if mode == "grep":
            return f"  {icon} {DIM}Grep{RESET} {pattern} {DIM}in {path}{RESET}"
        return f"  {icon} {DIM}Glob{RESET} {pattern} {DIM}in {path}{RESET}"

    elif name == "web_search":
        query = args.get("query", "")
        return f"  {icon} {DIM}Web search{RESET} {query[:60]}"

    elif name == "web_fetch":
        url = args.get("url", "")
        return f"  {icon} {DIM}Fetch{RESET} {url[:60]}"

    elif name == "web_api":
        method = args.get("method", "GET")
        url = args.get("url", "")
        platform = args.get("platform", "")
        return f"  {icon} {DIM}{method}{RESET} {platform}: {url[:50]}"

    elif name == "think":
        return f"  {icon} {DIM}Thinking...{RESET}"

    elif name == "dispatch":
        agent = args.get("agent_type", "scout")
        task = args.get("task", "")
        return f"  {icon} {DIM}Spawn {agent}{RESET} {task[:50]}"

    elif name == "tool_search":
        query = args.get("query", "")
        return f"  {icon} {DIM}Tool search{RESET} {query}"

    elif name == "computer_use":
        action = args.get("action", "?")
        return f"  {icon} {DIM}Computer{RESET} {action}"

    elif name == "database":
        query = args.get("query", "")[:60]
        return f"  {icon} {DIM}SQL{RESET} {query}"

    elif name.startswith("mcp_"):
        clean_name = name.replace("mcp_", "").replace("__", "/")
        return f"  ⚙️ {DIM}MCP{RESET} {clean_name}"

    else:
        return f"  ⚙️ {DIM}{name}{RESET} {str(args)[:60]}"


def tool_result_line(name: str, result: str, success: bool, elapsed: float) -> str:
    """Tool result with status icon, elapsed time, and collapsible output."""
    if not result or not result.strip():
        return f"  {GREEN}✔{RESET} {DIM}{elapsed:.1f}s{RESET}" if success else ""

    lines = result.strip().split("\n")
    status_icon = f"{GREEN}✔{RESET}" if success else f"{RED}✘{RESET}"
    time_str = f"{DIM}{elapsed:.1f}s{RESET}"

    out = []

    # Status line with summary
    if name == "bash":
        # Show exit code if present
        first = lines[0] if lines else ""
        if first.startswith("exit_code="):
            code = first.split("=")[1].split()[0] if "=" in first else "?"
            if code == "0":
                out.append(f"  {status_icon} {time_str}")
            else:
                out.append(f"  {status_icon} {DIM}exit {code}{RESET} {time_str}")
            lines = lines[1:]  # Skip exit code line from output
        else:
            out.append(f"  {status_icon} {time_str}")
    elif name in ("search_files", "web_search"):
        out.append(f"  {status_icon} {DIM}{len(lines)} results{RESET} {time_str}")
    elif name == "read_file":
        out.append(f"  {status_icon} {DIM}{len(lines)} lines{RESET} {time_str}")
    else:
        out.append(f"  {status_icon} {time_str}")

    # Show output lines (compact: 5 lines max, rest collapsed)
    show_lines = 5
    if lines:
        for line in lines[:show_lines]:
            out.append(f"  {DIM}│{RESET} {line[:120]}")
        if len(lines) > show_lines:
            out.append(f"  {DIM}│ ... {len(lines) - show_lines} more lines{RESET}")

    return "\n".join(out)


def tool_result_preview(result: str, max_lines: int = 8) -> str:
    """Show first N lines of tool output, indented with gutter."""
    if not result.strip():
        return ""
    lines = result.strip().split("\n")
    show = lines[:max_lines]
    out = []
    for line in show:
        out.append(f"  {DIM}│{RESET} {line[:120]}")
    if len(lines) > max_lines:
        out.append(f"  {DIM}│ ... {len(lines) - max_lines} more lines{RESET}")
    return "\n".join(out)


# ── Diff Display ──

def diff_display(old_string: str, new_string: str, path: str) -> str:
    """Render a unified diff. Red for removed, green for added."""
    old_lines = old_string.splitlines()
    new_lines = new_string.splitlines()

    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=2))
    if not diff:
        return ""

    out = [f"  {DIM}{_shorten_path(path)}{RESET}"]
    for line in diff[2:]:  # Skip --- and +++ headers
        if line.startswith("+"):
            out.append(f"  {GREEN}{line}{RESET}")
        elif line.startswith("-"):
            out.append(f"  {RED}{line}{RESET}")
        elif line.startswith("@@"):
            out.append(f"  {CYAN}{line}{RESET}")
        else:
            out.append(f"  {line}")

    return "\n".join(out)


# ── Permission Prompt ──

def permission_prompt(tool_name: str, args: dict) -> str:
    """Permission prompt with action description and preview."""
    if tool_name == "write_file":
        path = _shorten_path(args.get("path", "?"))
        content = args.get("content", "")
        lines = content.count("\n") + 1 if content else 0
        desc = f"Write {lines} lines to {path}"
    elif tool_name == "edit_file":
        path = _shorten_path(args.get("path", "?"))
        desc = f"Edit {path}"
    elif tool_name == "bash":
        cmd = args.get("command", "?")
        if len(cmd) > 80:
            cmd = cmd[:77] + "..."
        desc = f"Run: {cmd}"
    elif tool_name == "dispatch":
        agent = args.get("agent_type", "?")
        task = args.get("task", "")[:50]
        desc = f"Spawn {agent}: {task}"
    else:
        desc = f"{tool_name}: {str(args)[:60]}"

    out = [f"  {YELLOW}⚠ {desc}{RESET}"]

    # Show diff preview for edits
    if tool_name == "edit_file" and args.get("old_string") and args.get("new_string"):
        for line in args["old_string"].splitlines()[:3]:
            out.append(f"  {RED}  - {line[:100]}{RESET}")
        for line in args["new_string"].splitlines()[:3]:
            out.append(f"  {GREEN}  + {line[:100]}{RESET}")

    # Show content preview for writes
    if tool_name == "write_file" and args.get("content"):
        preview_lines = args["content"].split("\n")[:3]
        for line in preview_lines:
            out.append(f"  {DIM}  {line[:100]}{RESET}")
        total = args["content"].count("\n") + 1
        if total > 3:
            out.append(f"  {DIM}  ... {total - 3} more lines{RESET}")

    return "\n".join(out)


# ── Status Bar ──

def status_bar_text(model: str, session: str, tokens_used: int,
                    mode: str, cost: float = 0.0) -> str:
    """Status line with model, tokens, mode, and cost."""
    parts = []
    if model:
        parts.append(model)
    if tokens_used > 0:
        parts.append(f"{format_tokens(tokens_used)} tokens")
    if mode and mode != "normal":
        parts.append(mode)
    if cost > 0.001:
        parts.append(f"${cost:.2f}")
    return f"{DIM}{' · '.join(parts)}{RESET}"


# ── Token Footer ──

def token_footer(tokens: int, tool_count: int, elapsed: float,
                 cost: float = 0.0) -> str:
    """Post-response footer with usage stats and cost."""
    parts = []
    if tokens > 0:
        parts.append(format_tokens(tokens) + " tokens")
    if tool_count > 0:
        parts.append(f"{tool_count} tool{'s' if tool_count != 1 else ''}")
    parts.append(f"{elapsed:.1f}s")
    if cost > 0.001:
        parts.append(f"${cost:.2f}")
    return f"  {DIM}{' · '.join(parts)}{RESET}"


# ── Collapsed Tool Group ──

def collapsed_tool_group(tool_calls: list[dict], verbose: bool = False) -> str:
    """Render a group of consecutive read/search tools as a collapsed summary.

    When verbose=False (default), shows one-line summary like:
      🔍 Read 3 files, searched 2 patterns

    When verbose=True, shows each tool call individually.
    """
    if not tool_calls:
        return ""

    if verbose or len(tool_calls) <= 1:
        return "\n".join(tool_call_line(tc["name"], tc.get("args", {})) for tc in tool_calls)

    # Categorize
    reads = [tc for tc in tool_calls if tc["name"] == "read_file"]
    searches = [tc for tc in tool_calls if tc["name"] in ("search_files", "web_search")]
    others = [tc for tc in tool_calls if tc not in reads and tc not in searches]

    parts = []
    if reads:
        paths = [_shorten_path(tc.get("args", {}).get("path", "")) for tc in reads]
        if len(paths) <= 2:
            parts.append(f"Read {', '.join(paths)}")
        else:
            parts.append(f"Read {len(reads)} files")
    if searches:
        patterns = [tc.get("args", {}).get("pattern", tc.get("args", {}).get("query", "")) for tc in searches]
        if len(patterns) <= 2:
            parts.append(f"searched '{', '.join(p[:30] for p in patterns)}'")
        else:
            parts.append(f"{len(searches)} searches")
    if others:
        parts.append(f"{len(others)} other tools")

    summary = ", ".join(parts)
    return f"  {DIM}🔍 {summary} ({len(tool_calls)} calls){RESET}"

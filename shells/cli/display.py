"""JARVIS CLI Display — clean, minimal tool display matching Claude Code style.

No boxes. No emoji. Just clean indented output with color.
Tool calls are one-line descriptions. Results are collapsible.
"""

import difflib

# ── ANSI Codes ──
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
GREY = "\033[90m"
WHITE = "\033[97m"


def format_tokens(n: int) -> str:
    """Format token count: 1234 -> '1.2k', 123456 -> '123k'."""
    if n < 1000:
        return str(n)
    if n < 10000:
        return f"{n / 1000:.1f}k"
    if n < 1000000:
        return f"{n // 1000}k"
    return f"{n / 1000000:.1f}M"


# ── Tool Display (one-line, clean) ──

def tool_call_line(name: str, args: dict) -> str:
    """One-line tool call description. Matches Claude Code style.

    Examples:
      Read src/main.py
      Edit src/main.py
      Bash ls -la /home/user
      Search *.py for 'def main'
    """
    if name == "bash":
        cmd = args.get("command", "")
        return f"{DIM}  Bash{RESET} {cmd[:80]}"
    elif name == "read_file":
        path = args.get("path", "")
        return f"{DIM}  Read{RESET} {path}"
    elif name == "write_file":
        path = args.get("path", "")
        content = args.get("content", "")
        lines = content.count("\n") + 1 if content else 0
        return f"{DIM}  Write{RESET} {path} {DIM}({lines} lines){RESET}"
    elif name == "edit_file":
        path = args.get("path", "")
        return f"{DIM}  Edit{RESET} {path}"
    elif name == "search_files":
        pattern = args.get("pattern", "")
        path = args.get("path", ".")
        return f"{DIM}  Search{RESET} {pattern} {DIM}in {path}{RESET}"
    elif name == "web_search":
        query = args.get("query", "")
        return f"{DIM}  Search web{RESET} {query[:60]}"
    elif name == "web_fetch":
        url = args.get("url", "")
        return f"{DIM}  Fetch{RESET} {url[:60]}"
    elif name == "think":
        thought = args.get("thought", "")
        return f"{DIM}  Thinking...{RESET}"
    elif name == "dispatch":
        agent = args.get("agent_type", "scout")
        task = args.get("task", "")
        return f"{DIM}  Spawn {agent}{RESET} {task[:50]}"
    else:
        return f"{DIM}  {name}{RESET} {str(args)[:60]}"


def tool_result_line(name: str, result: str, success: bool, elapsed: float) -> str:
    """Tool result — show actual output, not just a summary."""
    if not result or not result.strip():
        return ""

    lines = result.strip().split("\n")
    out = []

    # Show up to 15 lines of actual output (indented, dimmed)
    for line in lines[:15]:
        out.append(f"  {DIM}{line[:120]}{RESET}")
    if len(lines) > 15:
        out.append(f"  {DIM}... {len(lines) - 15} more lines{RESET}")

    return "\n".join(out)


def tool_result_preview(result: str, max_lines: int = 12) -> str:
    """Show first N lines of tool output, indented."""
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

    out = [f"  {DIM}{path}{RESET}"]
    for line in diff[2:]:  # Skip --- and +++ headers
        if line.startswith("+"):
            out.append(f"  {GREEN}{line}{RESET}")
        elif line.startswith("-"):
            out.append(f"  {RED}{line}{RESET}")
        elif line.startswith("@@"):
            out.append(f"  {DIM}{line}{RESET}")
        else:
            out.append(f"  {line}")

    return "\n".join(out)


# ── Permission Prompt ──

def permission_prompt(tool_name: str, args: dict) -> str:
    """Clean permission prompt. No boxes — just the action description."""
    if tool_name == "write_file":
        path = args.get("path", "?")
        content = args.get("content", "")
        lines = content.count("\n") + 1 if content else 0
        desc = f"Write {lines} lines to {path}"
    elif tool_name == "edit_file":
        path = args.get("path", "?")
        desc = f"Edit {path}"
    elif tool_name == "bash":
        cmd = args.get("command", "?")[:80]
        desc = f"Run: {cmd}"
    else:
        desc = f"{tool_name}: {str(args)[:60]}"

    out = [f"  {YELLOW}{desc}{RESET}"]

    # Show diff preview for edits
    if tool_name == "edit_file" and args.get("old_string") and args.get("new_string"):
        for line in args["old_string"].splitlines()[:3]:
            out.append(f"  {RED}- {line}{RESET}")
        for line in args["new_string"].splitlines()[:3]:
            out.append(f"  {GREEN}+ {line}{RESET}")

    return "\n".join(out)


# ── Status Bar ──

def status_bar_text(model: str, session: str, tokens_used: int,
                    mode: str) -> str:
    """Simple status line — no boxes, just dimmed text."""
    parts = []
    if model:
        parts.append(model)
    if tokens_used > 0:
        parts.append(f"{format_tokens(tokens_used)} tokens")
    if mode and mode != "normal":
        parts.append(mode)
    return f"{DIM}{' · '.join(parts)}{RESET}"


# ── Token Footer ──

def token_footer(tokens: int, tool_count: int, elapsed: float) -> str:
    """Post-response footer with usage stats."""
    parts = []
    if tokens > 0:
        parts.append(format_tokens(tokens) + " tokens")
    if tool_count > 0:
        parts.append(f"{tool_count} tool{'s' if tool_count != 1 else ''}")
    parts.append(f"{elapsed:.1f}s")
    return f"  {DIM}{' · '.join(parts)}{RESET}"

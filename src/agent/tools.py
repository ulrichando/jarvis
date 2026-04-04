"""JARVIS Tool Definitions — structured tools the LLM can call.

Inspired by Claude Code / Gemini CLI / Codex CLI tool systems.
Each tool has a JSON schema definition + an execute() function.
The agent loop calls the LLM, which returns tool_calls, we execute them,
feed results back, and loop until the LLM gives a final text response.
"""

import os
import json
import subprocess
import difflib
import shutil
import glob as _glob

from src.sandbox import SandboxConfig, execute_sandboxed, detect_sandbox_capabilities

# ── File read tracking (for edit staleness detection) ─────────────────

_file_read_times: dict[str, float] = {}

# ── Device paths that must never be read (infinite/blocking) ──────────

BLOCKED_DEVICE_PATHS = {
    "/dev/zero", "/dev/random", "/dev/urandom", "/dev/full",
    "/dev/stdin", "/dev/tty", "/dev/console",
    "/dev/stdout", "/dev/stderr",
    "/dev/fd/0", "/dev/fd/1", "/dev/fd/2",
    "/proc/self/fd/0", "/proc/self/fd/1", "/proc/self/fd/2",
}

# ── Image extensions for read_file support ────────────────────────────

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# ── Security: path validation & output limits ─────────────────────────

MAX_OUTPUT_SIZE = 16000  # Max chars returned from bash/read output

ALLOWED_ROOTS = [
    os.path.expanduser("~"),
    "/tmp",
    "/var/log",
    "/etc",  # read-only
    "/opt",
]

BLOCKED_PATHS = [
    "/etc/shadow", "/etc/passwd", "/etc/sudoers",
    "/.ssh/id_rsa", "/.ssh/id_ed25519",
    "/.gnupg/", "/.vault_salt",
]


def _validate_path(path: str, write: bool = False) -> tuple[bool, str]:
    """Validate a file path for safety.

    Returns (is_valid, error_message).
    Prevents path traversal attacks and blocks sensitive files.
    """
    # Resolve to absolute, expanding ~, symlinks, and ..
    resolved = os.path.realpath(os.path.expanduser(path))

    # Block sensitive paths
    for blocked in BLOCKED_PATHS:
        if blocked in resolved:
            return False, f"Access denied: {blocked} is a protected path"

    # For writes, block /etc entirely
    if write and resolved.startswith("/etc"):
        return False, "Write access to /etc is blocked"

    return True, ""


def _sanitize_error_path(path: str) -> str:
    """Sanitize file paths in error messages to avoid leaking sensitive locations."""
    resolved = os.path.realpath(os.path.expanduser(path))
    for blocked in BLOCKED_PATHS:
        if blocked in resolved:
            return "<protected-path>"
    return path


# ── Tool Schemas (Groq/OpenAI function calling format) ──────────────

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a shell command on Kali Linux with full root access via sudo. Use for ANY system operation: installing packages, running security tools (nmap, metasploit, nikto, etc.), managing services, network config, file operations, opening GUI apps, and more. Use 'sudo' for privileged operations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 30, max 300)",
                        "default": 30,
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Use to examine source code, configs, logs, or any text file before making changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative file path to read",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start reading from (1-based). Use for large files.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of lines to read (default 200)",
                        "default": 200,
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file, creating it if needed. Use for creating new files or completely rewriting existing ones.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to write to",
                    },
                    "content": {
                        "type": "string",
                        "description": "The full content to write",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Make a targeted edit to a file by replacing specific text. Use for modifying existing files without rewriting the whole thing. The old_string must match exactly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to edit",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The exact text to find and replace (must be unique in the file)",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The replacement text",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for files by name pattern (glob) or search file contents by regex (ripgrep). Use mode='grep' with output_mode to control result format.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern for file names (e.g. '**/*.py') OR regex pattern for content search",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (default: current directory)",
                        "default": ".",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["glob", "grep"],
                        "description": "Search mode: 'glob' for file names, 'grep' for file contents (uses ripgrep)",
                        "default": "glob",
                    },
                    "output_mode": {
                        "type": "string",
                        "enum": ["content", "files_with_matches", "count"],
                        "description": "For grep mode: 'content' shows matching lines, 'files_with_matches' shows file paths (default), 'count' shows match counts per file",
                        "default": "files_with_matches",
                    },
                    "file_glob": {
                        "type": "string",
                        "description": "Filter searched files by glob (e.g. '*.py', '*.{ts,tsx}'). Only applies in grep mode.",
                    },
                    "file_type": {
                        "type": "string",
                        "description": "Filter by file type (e.g. 'py', 'js', 'rust'). Maps to rg --type. Only applies in grep mode.",
                    },
                    "context": {
                        "type": "integer",
                        "description": "Lines of context around matches (grep content mode only)",
                        "default": 0,
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "Case insensitive search (grep mode only)",
                    },
                    "head_limit": {
                        "type": "integer",
                        "description": "Max results to return (default 250, 0 for unlimited)",
                        "default": 250,
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the internet using DuckDuckGo. Use when you need current information, documentation, or answers not in your training data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results (default 5, max 10)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch and extract text content from a URL. Use to read documentation, articles, or web pages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_api",
            "description": "Make authenticated HTTP API calls to web services (GitHub, Slack, Discord, Jira, etc.). Uses stored tokens from the vault. If no token is stored, prompts user to add one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full API endpoint URL (e.g. https://api.github.com/user/repos)",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                        "description": "HTTP method",
                        "default": "GET",
                    },
                    "platform": {
                        "type": "string",
                        "description": "Platform name for token lookup (github, slack, discord, jira, etc.)",
                    },
                    "body": {
                        "type": "string",
                        "description": "JSON request body (for POST/PUT/PATCH)",
                    },
                    "headers": {
                        "type": "string",
                        "description": "Additional headers as JSON object",
                    },
                },
                "required": ["url", "platform"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "database",
            "description": "Execute SQL queries on SQLite, PostgreSQL, or MySQL databases. Can create, read, update, delete data. Use for any database operation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SQL query to execute (SELECT, INSERT, CREATE TABLE, etc.)",
                    },
                    "database": {
                        "type": "string",
                        "description": "Database path (for SQLite: /path/to/file.db) or connection string (for PostgreSQL: postgresql://user:pass@host/db)",
                    },
                    "db_type": {
                        "type": "string",
                        "enum": ["sqlite", "postgresql", "mysql"],
                        "description": "Database type",
                        "default": "sqlite",
                    },
                },
                "required": ["query", "database"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "computer_use",
            "description": "Control the desktop — click, type, scroll, take screenshots. Use to automate GUI apps, fill forms, click buttons, navigate menus. Take a screenshot first to see what's on screen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["screenshot", "click", "type", "key", "scroll", "move"],
                        "description": "Action to perform",
                    },
                    "x": {"type": "integer", "description": "X coordinate (for click/scroll/move)"},
                    "y": {"type": "integer", "description": "Y coordinate (for click/scroll/move)"},
                    "text": {"type": "string", "description": "Text to type (for type action)"},
                    "key": {"type": "string", "description": "Key to press (for key action, e.g. 'Return', 'ctrl+c', 'alt+Tab')"},
                    "button": {"type": "string", "description": "Mouse button (left/middle/right)"},
                    "direction": {"type": "string", "description": "Scroll direction (up/down)"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_screen",
            "description": "Capture and analyze what's currently on the user's screen. Returns the active window, application name, and visible text via OCR. Use when the user asks what's on their screen, what they're looking at, or what app they're using.",
            "parameters": {
                "type": "object",
                "properties": {
                    "detail": {
                        "type": "string",
                        "enum": ["summary", "full"],
                        "description": "Level of detail: 'summary' for window + app, 'full' for OCR text too",
                        "default": "full",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": "Use this tool to think through complex problems step by step before acting. Write out your reasoning. This doesn't execute anything — it just helps you reason clearly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {
                        "type": "string",
                        "description": "Your step-by-step reasoning",
                    },
                },
                "required": ["thought"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_search",
            "description": "Search for additional tools that may not be loaded yet. Use when you need a capability not in your current toolset, or when the system suggests a deferred tool exists. Returns tool definitions that become available for use.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords to search for (e.g. 'notebook jupyter', 'database sql', 'code intelligence lsp'). Use 'select:tool_name' for exact lookup.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max tools to return (default 5)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dispatch",
            "description": "Spawn a sub-agent to handle a task independently. Built-in: 'scout' (read-only exploration), 'worker' (full access execution), 'planner' (analysis only). Custom agents from ~/.jarvis/agents/ and .jarvis/agents/ are also available. Sub-agents run in isolation and return a summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_type": {
                        "type": "string",
                        "description": "Type of sub-agent. Built-in: scout, worker, planner. Custom agents by name.",
                    },
                    "task": {
                        "type": "string",
                        "description": "Clear, specific task for the sub-agent",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional context from the current conversation to help the sub-agent",
                    },
                },
                "required": ["agent_type", "task"],
            },
        },
    },
]


# ── Tool Execution ──────────────────────────────────────────────────

# Tools allowed in plan/read-only mode
READONLY_TOOLS = {"read_file", "search_files", "web_search", "web_fetch", "think", "dispatch", "view_screen", "tool_search"}

# Bash commands considered safe for read-only mode
READONLY_BASH_PREFIXES = (
    "ls", "cat", "head", "tail", "grep", "find", "wc", "file", "stat",
    "du", "df", "pwd", "echo", "date", "whoami", "uname", "which",
    "git log", "git diff", "git status", "git show", "git branch",
    "python3 -c", "node -e", "env", "printenv",
)


def get_plan_mode_tools() -> list[dict]:
    """Return tool schemas filtered for plan/read-only mode."""
    return [t for t in TOOL_SCHEMAS if t["function"]["name"] in READONLY_TOOLS or t["function"]["name"] == "bash"]


def execute_tool(name: str, args: dict, readonly: bool = False) -> str:
    """Execute a tool by name with given arguments. Returns result string.

    Args:
        readonly: If True, block write operations (plan mode).
    """
    try:
        if readonly and name in ("write_file", "edit_file"):
            return f"BLOCKED: {name} is not allowed in plan mode. Switch to normal mode to make changes."

        if readonly and name == "bash":
            cmd = args.get("command", "").strip()
            if not any(cmd.startswith(p) for p in READONLY_BASH_PREFIXES):
                return f"BLOCKED: Command '{cmd.split()[0]}' is not allowed in plan mode. Only read-only commands are permitted."

        if name == "bash":
            return _exec_bash(args)
        elif name == "read_file":
            return _exec_read(args)
        elif name == "write_file":
            return _exec_write(args)
        elif name == "edit_file":
            return _exec_edit(args)
        elif name == "search_files":
            return _exec_search(args)
        elif name == "web_search":
            return _exec_web_search(args)
        elif name == "web_fetch":
            return _exec_web_fetch(args)
        elif name == "computer_use":
            from src.agent.computer_use import execute_computer_action
            return execute_computer_action(
                args.get("action", "screenshot"),
                x=args.get("x", 0), y=args.get("y", 0),
                text=args.get("text", ""), key=args.get("key", ""),
                button=args.get("button", "left"),
                direction=args.get("direction", "down"),
                amount=args.get("amount", 3),
            )
        elif name == "database":
            return _exec_database(args)
        elif name == "web_api":
            return _exec_web_api(args)
        elif name == "view_screen":
            return _exec_view_screen(args)
        elif name == "tool_search":
            return _exec_tool_search(args)
        elif name == "think":
            return args.get("thought", "")
        elif name == "dispatch":
            return "__DISPATCH__"  # Handled async by agent loop
        elif name.startswith("mcp_"):
            return _exec_mcp_tool(name, args)
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        return f"Error executing {name}: {e}"


def _exec_tool_search(args: dict) -> str:
    """Search for deferred/additional tools."""
    query = args.get("query", "")
    max_results = min(args.get("max_results", 5), 10)

    if not query:
        return "No query provided."

    try:
        from src.agent.tool_registry import search_tools, get_tool_meta

        # Handle "select:name" syntax for exact lookup
        if query.startswith("select:"):
            names = [n.strip() for n in query[7:].split(",")]
            results = []
            for name in names:
                meta = get_tool_meta(name)
                if meta:
                    results.append(meta)
            if not results:
                return f"No tools found matching: {', '.join(names)}"
        else:
            results = search_tools(query, max_results)

        if not results:
            return f"No tools found for query: {query}"

        lines = [f"Found {len(results)} tool(s):\n"]
        for meta in results:
            lines.append(f"**{meta.name}** [{meta.category}]")
            lines.append(f"  {meta.description}")
            if meta.is_read_only:
                lines.append(f"  [read-only, safe for parallel]")
            if meta.is_destructive:
                lines.append(f"  [destructive — use with caution]")
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        return f"Tool search error: {e}"


def _exec_mcp_tool(name: str, args: dict) -> str:
    """Execute an MCP tool via the global MCP manager."""
    try:
        # Access the global manager (set by Brain during init)
        manager = _mcp_manager
        if manager is None:
            return f"MCP not initialized. Cannot call {name}."
        return manager.call_tool(name, args)
    except Exception as e:
        return f"MCP tool error ({name}): {e}"


# Global MCP manager reference (set by Brain.__init__)
_mcp_manager: object = None


def set_mcp_manager(manager):
    """Set the global MCP manager for tool execution."""
    global _mcp_manager
    _mcp_manager = manager


def _exec_view_screen(args: dict) -> str:
    """Capture and describe what's on the user's screen right now."""
    try:
        from src.vision.screen_observer import ScreenObserver
        obs = ScreenObserver(interval=999)  # One-shot, no loop
        ctx = obs.capture_now()
        parts = []
        if ctx.active_window:
            parts.append(f"Active window: {ctx.active_window}")
        if ctx.window_class:
            parts.append(f"Application: {ctx.window_class}")
        detail = args.get("detail", "full")
        if detail == "full" and ctx.screen_text:
            lines = ctx.screen_text.strip().split("\n")[:20]
            text = "\n".join(l for l in lines if l.strip())
            parts.append(f"Visible text on screen:\n{text}")
        if not parts:
            return "Could not capture screen. Display may not be accessible."
        return "\n".join(parts)
    except Exception as e:
        return f"Screen capture failed: {e}"


_GUI_APPS = {
    "google-chrome", "chromium", "firefox", "brave",
    "code", "code-oss", "vscodium",
    "nautilus", "thunar", "dolphin", "nemo",
    "gimp", "inkscape", "blender",
    "vlc", "mpv", "totem",
    "libreoffice", "evince", "okular",
    "xdg-open", "open", "sensible-browser",
    "gedit", "kate", "mousepad",
    "burpsuite", "wireshark", "zenmap",
}

_TERMINAL_APPS = {
    "terminal", "x-terminal-emulator", "gnome-terminal", "konsole",
    "xfce4-terminal", "alacritty", "kitty", "wezterm", "tilix",
}


def _get_display_env() -> dict:
    """Get environment variables needed for GUI/terminal apps."""
    return {
        **os.environ,
        "DISPLAY": os.environ.get("DISPLAY", ":0.0"),
        "XAUTHORITY": os.environ.get("XAUTHORITY", os.path.expanduser("~/.Xauthority")),
        "DBUS_SESSION_BUS_ADDRESS": os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""),
    }


def _launch_in_terminal(command: str) -> str:
    """Open a terminal window and run a command inside it.

    The terminal stays open after the command finishes so user can see output.
    Handles sudo password prompts interactively.
    """
    # Build the inner command: run it, then wait for keypress
    inner = f'bash -c \'{command}; echo; echo "[Done] Press Enter to close"; read\''

    # Try various terminal emulators
    terminals = [
        ("x-terminal-emulator", ["-e"]),
        ("gnome-terminal", ["--"]),
        ("konsole", ["-e"]),
        ("xfce4-terminal", ["-e"]),
        ("alacritty", ["-e"]),
        ("kitty", ["-e"]),
        ("xterm", ["-e"]),
    ]

    import shutil
    for term, flag in terminals:
        if shutil.which(term):
            try:
                cmd = [term] + flag + ["bash", "-c", f'{command}; echo; echo "[Done] Press Enter to close"; read']
                subprocess.Popen(
                    cmd,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=_get_display_env(),
                )
                return f"Opened terminal running: {command}"
            except Exception:
                continue

    return f"No terminal emulator found. Run manually: {command}"


def _exec_bash(args: dict) -> str:
    command = args.get("command", "")
    timeout = min(args.get("timeout", 60), 600)
    if not command:
        return "No command provided."

    cmd_first = command.strip().split()[0].split("/")[-1] if command.strip() else ""

    # Terminal commands: open a terminal and run the command INSIDE it
    if cmd_first in _TERMINAL_APPS:
        # Extract the command to run inside the terminal
        # e.g., "x-terminal-emulator -e sudo apt update" → "sudo apt update"
        parts = command.strip().split()
        inner_cmd = ""
        for i, p in enumerate(parts):
            if p in ("-e", "--"):
                inner_cmd = " ".join(parts[i+1:])
                break
        if not inner_cmd:
            inner_cmd = "bash"  # Just open a shell
        return _launch_in_terminal(inner_cmd)

    # Commands that need an interactive terminal (sudo, apt, etc.)
    interactive_cmds = ["sudo apt", "apt update", "apt upgrade", "apt install",
                        "apt remove", "dpkg", "systemctl"]
    if any(command.strip().startswith(ic) or command.strip().startswith(f"echo 'toor' | {ic}")
           for ic in interactive_cmds):
        return _launch_in_terminal(command)

    # GUI apps: launch detached
    if cmd_first in _GUI_APPS:
        try:
            subprocess.Popen(
                command, shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=_get_display_env(),
            )
            return f"Launched {cmd_first} in background."
        except Exception as e:
            return f"Failed to launch: {e}"

    # Check if sandbox requested
    use_sandbox = not args.get("dangerouslyDisableSandbox", False)

    if use_sandbox:
        try:
            config = SandboxConfig(enabled=True, timeout=timeout)
            result = execute_sandboxed(command, config, cwd=os.getcwd(), timeout=timeout)
            output = ""
            if result["stdout"]:
                output += result["stdout"]
            if result["stderr"]:
                output += ("\n" if output else "") + result["stderr"]
            if not output:
                output = "(no output)"
            sandboxed = result.get("sandboxed", False)
            prefix = f"exit_code={result['returncode']}"
            if sandboxed:
                prefix += " [sandboxed]"
            # Cap output
            if len(output) > MAX_OUTPUT_SIZE:
                half = MAX_OUTPUT_SIZE // 2
                quarter = MAX_OUTPUT_SIZE // 4
                output = output[:half] + "\n\n... (truncated) ...\n\n" + output[-quarter:]
            return f"{prefix}\n{output}"
        except Exception:
            pass  # Fall through to unsandboxed execution

    # Original unsandboxed execution (fallback)
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=os.getcwd(),
            env={**os.environ,
                 "DISPLAY": os.environ.get("DISPLAY", ":0.0"),
                 "XAUTHORITY": os.environ.get("XAUTHORITY", os.path.expanduser("~/.Xauthority")),
                 "DBUS_SESSION_BUS_ADDRESS": os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""),
                 },
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += ("\n" if output else "") + result.stderr
        if not output:
            output = "(no output)"
        # Cap output to prevent context overflow
        if len(output) > MAX_OUTPUT_SIZE:
            half = MAX_OUTPUT_SIZE // 2
            output = output[:half] + "\n\n... (truncated) ...\n\n" + output[-(half // 2):]
        return f"exit_code={result.returncode}\n{output}"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


def _is_blocked_device(path: str) -> bool:
    """Check if a path is a blocked device/proc path."""
    resolved = os.path.realpath(path)
    if resolved in BLOCKED_DEVICE_PATHS:
        return True
    # /proc/<pid>/fd/0-2 are aliases for stdio
    if resolved.startswith("/proc/") and any(
        resolved.endswith(f"/fd/{n}") for n in ("0", "1", "2")
    ):
        return True
    return False


def _detect_binary(path: str) -> bool:
    """Check if file is binary by looking for null bytes in first 8KB."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
        return b"\x00" in chunk
    except Exception:
        return False


def _read_pdf(path: str, offset: int | None, limit: int | None) -> str:
    """Extract text from a PDF file with optional page range."""
    page_start = max(1, offset or 1)
    page_count = limit or 20

    # Try pdftotext first (fast, widely available)
    if shutil.which("pdftotext"):
        try:
            first = str(page_start)
            last = str(page_start + page_count - 1)
            result = subprocess.run(
                ["pdftotext", "-f", first, "-l", last, "-layout", path, "-"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                text = result.stdout
                # Add page markers by splitting on form feeds
                pages = text.split("\f")
                parts = []
                for i, page in enumerate(pages):
                    page_text = page.strip()
                    if page_text:
                        parts.append(f"--- Page {page_start + i} ---\n{page_text}")
                return "\n\n".join(parts) if parts else "(PDF has no extractable text)"
        except Exception:
            pass

    # Fallback to pymupdf (fitz)
    try:
        import fitz  # pymupdf
        doc = fitz.open(path)
        total_pages = len(doc)
        end_page = min(total_pages, page_start - 1 + page_count)
        parts = []
        for i in range(page_start - 1, end_page):
            page = doc[i]
            text = page.get_text().strip()
            if text:
                parts.append(f"--- Page {i + 1} ---\n{text}")
        doc.close()
        result = "\n\n".join(parts) if parts else "(PDF has no extractable text)"
        if end_page < total_pages:
            result += f"\n\n... ({total_pages - end_page} more pages)"
        return result
    except ImportError:
        return (
            f"[PDF file: {path}, {os.path.getsize(path)} bytes] "
            "Install pdftotext or pymupdf (pip install pymupdf) to extract text."
        )
    except Exception as e:
        return f"Error reading PDF: {e}"


def _read_image(path: str) -> str:
    """Return metadata description for an image file."""
    size = os.path.getsize(path)
    dims = ""
    try:
        from PIL import Image
        with Image.open(path) as img:
            w, h = img.size
            dims = f"{w}x{h}, "
    except Exception:
        pass
    return f"[Image file: {path}, {dims}{size} bytes]"


def _read_notebook(path: str) -> str:
    """Parse a Jupyter notebook and format cells."""
    with open(path, "r", encoding="utf-8") as f:
        nb = json.load(f)

    cells = nb.get("cells", [])
    parts = []
    for i, cell in enumerate(cells, 1):
        cell_type = cell.get("cell_type", "unknown")
        source = "".join(cell.get("source", []))

        if cell_type == "code":
            block = f"Cell [{i}] (code):\n```python\n{source}\n```"
            # Extract outputs
            outputs = cell.get("outputs", [])
            out_texts = []
            for out in outputs:
                if "text" in out:
                    out_texts.append("".join(out["text"]))
                elif "data" in out:
                    data = out["data"]
                    if "text/plain" in data:
                        out_texts.append("".join(data["text/plain"]))
                    elif "image/png" in data:
                        out_texts.append("[image output]")
            if out_texts:
                block += f"\nOutput: {''.join(out_texts)}"
            parts.append(block)
        elif cell_type == "markdown":
            parts.append(f"Cell [{i}] (markdown):\n{source}")
        else:
            parts.append(f"Cell [{i}] ({cell_type}):\n{source}")

    return "\n\n".join(parts) if parts else "(Empty notebook)"


def _exec_read(args: dict) -> str:
    path = os.path.expanduser(args.get("path", ""))
    offset = args.get("offset", None)
    limit = args.get("limit", None)

    if not path:
        return "No path provided."

    # Block dangerous device paths
    if _is_blocked_device(path):
        return f"BLOCKED: Cannot read device path {path} (would block or produce infinite output)."

    # Security: validate path before any I/O
    valid, err = _validate_path(path, write=False)
    if not valid:
        return err

    if not os.path.exists(path):
        return f"File not found: {_sanitize_error_path(path)}"
    if os.path.isdir(path):
        entries = os.listdir(path)
        return f"Directory listing ({len(entries)} entries):\n" + "\n".join(sorted(entries))

    resolved = os.path.realpath(path)
    ext = os.path.splitext(resolved)[1].lower()

    # PDF support
    if ext == ".pdf":
        result = _read_pdf(resolved, offset, limit)
        _file_read_times[resolved] = os.path.getmtime(resolved)
        if len(result) > MAX_OUTPUT_SIZE:
            half = MAX_OUTPUT_SIZE // 2
            result = result[:half] + "\n\n... (truncated) ...\n\n" + result[-(half // 2):]
        return result

    # Image support
    if ext in IMAGE_EXTENSIONS:
        _file_read_times[resolved] = os.path.getmtime(resolved)
        return _read_image(resolved)

    # Jupyter notebook support
    if ext == ".ipynb":
        try:
            result = _read_notebook(resolved)
            _file_read_times[resolved] = os.path.getmtime(resolved)
            if len(result) > MAX_OUTPUT_SIZE:
                half = MAX_OUTPUT_SIZE // 2
                result = result[:half] + "\n\n... (truncated) ...\n\n" + result[-(half // 2):]
            return result
        except Exception as e:
            return f"Error reading notebook: {e}"

    # Binary file detection
    if _detect_binary(resolved):
        size = os.path.getsize(resolved)
        return f"[Binary file: {path}, {size} bytes]"

    # Text file reading with encoding detection
    try:
        encoding_used = "utf-8"
        try:
            with open(resolved, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except UnicodeDecodeError:
            encoding_used = "latin-1"
            with open(resolved, "r", encoding="latin-1") as f:
                lines = f.readlines()

        # Track read time for staleness detection
        _file_read_times[resolved] = os.path.getmtime(resolved)

        total = len(lines)
        start = max(0, ((offset or 1) - 1))
        end = min(total, start + (limit or 200))
        chunk = lines[start:end]

        # Add line numbers
        numbered = [f"{i + start + 1:4d} | {line.rstrip()}" for i, line in enumerate(chunk)]
        result = "\n".join(numbered)
        if end < total:
            result += f"\n\n... ({total - end} more lines)"
        if encoding_used != "utf-8":
            result = f"[Encoding: {encoding_used}]\n{result}"

        # Cap output size
        if len(result) > MAX_OUTPUT_SIZE:
            half = MAX_OUTPUT_SIZE // 2
            result = result[:half] + "\n\n... (truncated) ...\n\n" + result[-(half // 2):]

        return result
    except Exception as e:
        return f"Error reading {_sanitize_error_path(path)}: {e}"


def _exec_write(args: dict) -> str:
    path = os.path.expanduser(args.get("path", ""))
    content = args.get("content", "")

    if not path:
        return "No path provided."

    # Security: validate path for write access
    valid, err = _validate_path(path, write=True)
    if not valid:
        return err

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        resolved = os.path.realpath(path)
        extra_info = ""

        # If file already exists, handle backup and line ending preservation
        if os.path.exists(resolved):
            # Create .bak backup
            bak_path = resolved + ".bak"
            try:
                shutil.copy2(resolved, bak_path)
                extra_info = f" (backup: {bak_path})"
            except Exception:
                pass  # Non-fatal if backup fails

            # Detect existing line endings and match them
            try:
                with open(resolved, "rb") as f:
                    raw = f.read(8192)
                if b"\r\n" in raw:
                    # File uses CRLF — convert content to match
                    content = content.replace("\r\n", "\n").replace("\n", "\r\n")
            except Exception:
                pass

        with open(path, "w") as f:
            f.write(content)

        # Track the write time for staleness detection
        _file_read_times[resolved] = os.path.getmtime(resolved)

        lines = content.count("\n") + 1
        return f"Wrote {lines} lines to {path}{extra_info}"
    except Exception as e:
        return f"Error writing {_sanitize_error_path(path)}: {e}"


def _normalize_curly_quotes(s: str) -> str:
    """Replace curly/smart quotes with straight ASCII equivalents."""
    return (s
            .replace("\u2018", "'").replace("\u2019", "'")   # ' '
            .replace("\u201c", '"').replace("\u201d", '"'))   # " "


def _exec_edit(args: dict) -> str:
    path = os.path.expanduser(args.get("path", ""))
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")

    if not path or not old_string:
        return "Need path and old_string."

    # Security: validate path for write access
    valid, err = _validate_path(path, write=True)
    if not valid:
        return err

    if not os.path.exists(path):
        return f"File not found: {_sanitize_error_path(path)}"

    resolved = os.path.realpath(path)

    # Staleness detection: check if file was modified since last read
    if resolved in _file_read_times:
        current_mtime = os.path.getmtime(resolved)
        if current_mtime > _file_read_times[resolved]:
            return (
                f"File was modified externally since last read. "
                f"Read it again first before editing."
            )

    try:
        with open(path, "r") as f:
            content = f.read()

        # Try to find old_string directly
        count = content.count(old_string)

        # If not found, try curly quote normalization
        actual_old = old_string
        if count == 0:
            normalized_content = _normalize_curly_quotes(content)
            normalized_old = _normalize_curly_quotes(old_string)
            norm_count = normalized_content.count(normalized_old)
            if norm_count == 1:
                # Find the original text that matches after normalization
                # by scanning through content for the matching region
                norm_idx = normalized_content.index(normalized_old)
                actual_old = content[norm_idx:norm_idx + len(normalized_old)]
                # Verify: the normalized version of actual_old should equal normalized_old
                if _normalize_curly_quotes(actual_old) == normalized_old:
                    count = 1
                else:
                    count = 0
            elif norm_count > 1:
                return f"old_string matches {norm_count} locations (after quote normalization). Provide more context to make it unique."

        if count == 0:
            return f"old_string not found in {_sanitize_error_path(path)}. Read the file first to get the exact text."
        if count > 1:
            return f"old_string matches {count} locations. Provide more context to make it unique."

        new_content = content.replace(actual_old, new_string, 1)
        with open(path, "w") as f:
            f.write(new_content)

        # Update tracked mtime after successful edit
        _file_read_times[resolved] = os.path.getmtime(resolved)

        # Generate unified diff snippet for context
        old_lines = content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{os.path.basename(path)}",
            tofile=f"b/{os.path.basename(path)}",
            n=3,  # 3 lines of context
        ))
        if diff:
            # Limit diff output to avoid flooding
            diff_text = "".join(diff[:50])
            if len(diff) > 50:
                diff_text += "\n... (diff truncated)"
            return f"Edited {path} successfully.\n\n{diff_text}"

        return f"Edited {path} successfully."
    except Exception as e:
        return f"Error editing {_sanitize_error_path(path)}: {e}"


def _exec_search(args: dict) -> str:
    pattern = args.get("pattern", "")
    path = os.path.expanduser(args.get("path", "."))
    mode = args.get("mode", "glob")

    if not pattern:
        return "No pattern provided."

    if mode == "glob":
        try:
            matches = _glob.glob(os.path.join(path, pattern), recursive=True)
            matches = sorted(matches)[:50]  # Cap results
            if not matches:
                return f"No files matching '{pattern}' in {path}"
            return f"Found {len(matches)} files:\n" + "\n".join(matches)
        except Exception as e:
            return f"Glob error: {e}"

    elif mode == "grep":
        try:
            from src.agent.ripgrep import RipgrepConfig, search as rg_search

            config = RipgrepConfig(
                pattern=pattern,
                path=path,
                glob=args.get("file_glob", ""),
                file_type=args.get("file_type", ""),
                output_mode=args.get("output_mode", "files_with_matches"),
                context=args.get("context", 0),
                case_insensitive=args.get("case_insensitive", False),
                head_limit=args.get("head_limit", 250),
            )
            result = rg_search(config)
            return result.output
        except Exception as e:
            return f"Search error: {e}"

    return f"Unknown search mode: {mode}"


def _exec_web_search(args: dict) -> str:
    query = args.get("query", "")
    max_results = min(args.get("max_results", 5), 10)

    if not query:
        return "No query provided."

    try:
        from src.internet.search import web_search
        results = web_search(query, max_results)
        if not results:
            return "No results found."
        lines = []
        for r in results:
            lines.append(f"**{r['title']}**")
            lines.append(f"  {r['url']}")
            lines.append(f"  {r['body'][:400]}")
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        return f"Search error: {e}"


def _exec_database(args: dict) -> str:
    """Execute SQL queries on databases."""
    query = args.get("query", "").strip()
    database = args.get("database", "").strip()
    db_type = args.get("db_type", "sqlite").lower()

    if not query:
        return "No SQL query provided."
    if not database:
        return "No database path/connection string provided."

    # Block destructive operations on system databases
    db_lower = database.lower()
    if any(p in db_lower for p in ["/etc/", "/var/lib/", "/usr/", "system"]):
        return "BLOCKED: Cannot modify system databases."

    try:
        if db_type == "sqlite":
            import sqlite3
            db_path = os.path.expanduser(database)
            conn = sqlite3.connect(db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query)

            # SELECT queries return results
            if query.strip().upper().startswith("SELECT") or query.strip().upper().startswith("PRAGMA"):
                rows = cursor.fetchmany(100)  # Cap at 100 rows
                if not rows:
                    conn.close()
                    return "Query returned 0 rows."
                # Format as table
                columns = [d[0] for d in cursor.description]
                lines = [" | ".join(columns)]
                lines.append("-" * len(lines[0]))
                for row in rows:
                    lines.append(" | ".join(str(v) for v in row))
                total = cursor.execute(f"SELECT COUNT(*) FROM ({query})").fetchone()[0] if len(rows) >= 100 else len(rows)
                conn.close()
                result = "\n".join(lines)
                if len(rows) >= 100:
                    result += f"\n... showing 100 of {total} rows"
                return result
            else:
                # INSERT/UPDATE/DELETE/CREATE
                conn.commit()
                affected = cursor.rowcount
                conn.close()
                return f"OK. {affected} row(s) affected."

        elif db_type == "postgresql":
            try:
                import psycopg2
                conn = psycopg2.connect(database)
                cursor = conn.cursor()
                cursor.execute(query)
                if cursor.description:
                    columns = [d[0] for d in cursor.description]
                    rows = cursor.fetchmany(100)
                    lines = [" | ".join(columns)]
                    lines.append("-" * len(lines[0]))
                    for row in rows:
                        lines.append(" | ".join(str(v) for v in row))
                    conn.close()
                    return "\n".join(lines)
                else:
                    conn.commit()
                    affected = cursor.rowcount
                    conn.close()
                    return f"OK. {affected} row(s) affected."
            except ImportError:
                return "PostgreSQL support requires: pip install psycopg2-binary"

        elif db_type == "mysql":
            try:
                import mysql.connector
                # Parse connection string or use as host
                conn = mysql.connector.connect(host=database)
                cursor = conn.cursor()
                cursor.execute(query)
                if cursor.description:
                    columns = [d[0] for d in cursor.description]
                    rows = cursor.fetchmany(100)
                    lines = [" | ".join(columns)]
                    lines.append("-" * len(lines[0]))
                    for row in rows:
                        lines.append(" | ".join(str(v) for v in row))
                    conn.close()
                    return "\n".join(lines)
                else:
                    conn.commit()
                    affected = cursor.rowcount
                    conn.close()
                    return f"OK. {affected} row(s) affected."
            except ImportError:
                return "MySQL support requires: pip install mysql-connector-python"
        else:
            return f"Unknown db_type: {db_type}. Use sqlite, postgresql, or mysql."

    except Exception as e:
        return f"Database error: {e}"


def _exec_web_api(args: dict) -> str:
    """Make authenticated HTTP API calls using stored tokens."""
    import urllib.request
    import urllib.error

    url = args.get("url", "")
    method = args.get("method", "GET").upper()
    platform = args.get("platform", "").lower()
    body = args.get("body", "")
    extra_headers = args.get("headers", "")

    if not url:
        return "No URL provided."
    if not platform:
        return "No platform specified. Use: github, slack, discord, jira, etc."

    # Get token from vault
    try:
        from src.vault.tokens import TokenVault
        vault = TokenVault()
        token_data = vault.get_with_extra(platform)
    except Exception:
        token_data = None

    if not token_data:
        return (f"No token stored for '{platform}'. "
                f"Ask the user to provide one, then store it with:\n"
                f"  /config vault store {platform} <token>\n"
                f"Or tell the user to add it to ~/.jarvis/vault.json")

    token = token_data.get("token", "")
    extra = token_data.get("extra", {})

    # Build auth header based on platform conventions
    auth_headers = {}
    if platform == "github":
        auth_headers["Authorization"] = f"Bearer {token}"
        auth_headers["Accept"] = "application/vnd.github+json"
        auth_headers["X-GitHub-Api-Version"] = "2022-11-28"
    elif platform in ("slack", "discord"):
        auth_headers["Authorization"] = f"Bearer {token}"
    elif platform == "jira":
        # Jira uses email:token as basic auth
        email = extra.get("email", "")
        if email:
            import base64
            creds = base64.b64encode(f"{email}:{token}".encode()).decode()
            auth_headers["Authorization"] = f"Basic {creds}"
        else:
            auth_headers["Authorization"] = f"Bearer {token}"
    elif platform == "openai":
        auth_headers["Authorization"] = f"Bearer {token}"
    else:
        # Default: Bearer token
        auth_headers["Authorization"] = f"Bearer {token}"

    auth_headers["Content-Type"] = "application/json"
    auth_headers["User-Agent"] = "JARVIS/2.0"

    # Merge extra headers
    if extra_headers:
        try:
            auth_headers.update(json.loads(extra_headers))
        except Exception:
            pass

    # Make the request
    try:
        data = body.encode() if body else None
        req = urllib.request.Request(url, data=data, headers=auth_headers, method=method)
        resp = urllib.request.urlopen(req, timeout=30)
        result = resp.read().decode()

        # Try to pretty-print JSON
        try:
            parsed = json.loads(result)
            result = json.dumps(parsed, indent=2)
        except Exception:
            pass

        if len(result) > 10000:
            result = result[:10000] + "\n... (truncated)"

        return f"HTTP {resp.status}\n{result}"
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:500]
        except Exception:
            pass
        return f"HTTP {e.code}: {e.reason}\n{body}"
    except Exception as e:
        return f"API error: {e}"


def _exec_web_fetch(args: dict) -> str:
    url = args.get("url", "")
    if not url:
        return "No URL provided."

    try:
        from src.internet.scraper import fetch_page
        content = fetch_page(url)
        if content:
            # Cap at 5000 chars
            if len(content) > 5000:
                content = content[:5000] + "\n\n... (truncated)"
            return content
        return "No content extracted."
    except Exception as e:
        return f"Fetch error: {e}"
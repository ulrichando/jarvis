"""JARVIS Tool Definitions — structured tools the LLM can call.

Inspired by Claude Code / Gemini CLI / Codex CLI tool systems.
Each tool has a JSON schema definition + an execute() function.
The agent loop calls the LLM, which returns tool_calls, we execute them,
feed results back, and loop until the LLM gives a final text response.
"""

import os
import subprocess
import glob as _glob

from brain.sandbox import SandboxConfig, execute_sandboxed, detect_sandbox_capabilities

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
            "description": "Search for files by name pattern (glob) or search file contents by regex. Use to find relevant files in a project.",
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
                        "description": "Search mode: 'glob' for file names, 'grep' for file contents",
                        "default": "glob",
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
            "name": "dispatch",
            "description": "Spawn a sub-agent to handle a task independently. Use 'scout' for read-only exploration (find files, read code). Use 'worker' for tasks that modify state (edit files, run commands, install things). Use 'planner' for analysis and creating structured plans. Sub-agents run in isolation and return a summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_type": {
                        "type": "string",
                        "enum": ["scout", "worker", "planner"],
                        "description": "Type of sub-agent: scout (read-only), worker (full access), planner (analysis only)",
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
READONLY_TOOLS = {"read_file", "search_files", "web_search", "web_fetch", "think", "dispatch", "view_screen"}

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
        elif name == "view_screen":
            return _exec_view_screen(args)
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
        from brain.vision.screen_observer import ScreenObserver
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


def _exec_bash(args: dict) -> str:
    command = args.get("command", "")
    timeout = min(args.get("timeout", 60), 600)  # Up to 10 minutes
    if not command:
        return "No command provided."

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


def _exec_read(args: dict) -> str:
    path = os.path.expanduser(args.get("path", ""))
    offset = args.get("offset", 1)
    limit = args.get("limit", 200)

    if not path:
        return "No path provided."

    # Security: validate path before any I/O
    valid, err = _validate_path(path, write=False)
    if not valid:
        return err

    if not os.path.exists(path):
        return f"File not found: {_sanitize_error_path(path)}"
    if os.path.isdir(path):
        # List directory contents
        entries = os.listdir(path)
        return f"Directory listing ({len(entries)} entries):\n" + "\n".join(sorted(entries))

    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()

        total = len(lines)
        start = max(0, (offset or 1) - 1)
        end = min(total, start + (limit or 200))
        chunk = lines[start:end]

        # Add line numbers
        numbered = [f"{i + start + 1:4d} | {line.rstrip()}" for i, line in enumerate(chunk)]
        result = "\n".join(numbered)
        if end < total:
            result += f"\n\n... ({total - end} more lines)"

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
        with open(path, "w") as f:
            f.write(content)
        lines = content.count("\n") + 1
        return f"Wrote {lines} lines to {path}"
    except Exception as e:
        return f"Error writing {_sanitize_error_path(path)}: {e}"


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

    try:
        with open(path, "r") as f:
            content = f.read()

        count = content.count(old_string)
        if count == 0:
            return f"old_string not found in {_sanitize_error_path(path)}. Read the file first to get the exact text."
        if count > 1:
            return f"old_string matches {count} locations. Provide more context to make it unique."

        new_content = content.replace(old_string, new_string, 1)
        with open(path, "w") as f:
            f.write(new_content)
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
            result = subprocess.run(
                ["grep", "-rn", "--include=*", "-l", pattern, path],
                capture_output=True, text=True, timeout=15,
            )
            files = result.stdout.strip()
            if not files:
                # Try with content
                result = subprocess.run(
                    ["grep", "-rn", pattern, path],
                    capture_output=True, text=True, timeout=15,
                )
                output = result.stdout.strip()
                if not output:
                    return f"No matches for '{pattern}' in {path}"
                lines = output.split("\n")[:30]
                return "\n".join(lines)
            return files
        except Exception as e:
            return f"Grep error: {e}"

    return f"Unknown search mode: {mode}"


def _exec_web_search(args: dict) -> str:
    query = args.get("query", "")
    max_results = min(args.get("max_results", 5), 10)

    if not query:
        return "No query provided."

    try:
        from brain.internet.search import web_search
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


def _exec_web_fetch(args: dict) -> str:
    url = args.get("url", "")
    if not url:
        return "No URL provided."

    try:
        from brain.internet.scraper import fetch_page
        content = fetch_page(url)
        if content:
            # Cap at 5000 chars
            if len(content) > 5000:
                content = content[:5000] + "\n\n... (truncated)"
            return content
        return "No content extracted."
    except Exception as e:
        return f"Fetch error: {e}"
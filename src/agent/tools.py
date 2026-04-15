"""JARVIS Tool Definitions — structured tools the LLM can call.

JARVIS tool system.
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

# ── BashTool security & validation imports ───────────────────────────
from src.tools.BashTool.bashSecurity import (
    BLOCKED_PATTERNS as BASH_BLOCKED_PATTERNS,
    DANGEROUS_RM_PATHS,
    validate_bash_command as validate_bash_security,
)
from src.tools.BashTool.commandSemantics import interpret_command_result as _interpret_bash_result
from src.tools.BashTool.sedValidation import check_sed_constraints as _check_sed_constraints
from src.tools.BashTool.readOnlyValidation import validate_read_only as _validate_read_only
from src.tools.BashTool.destructiveCommandWarning import (
    get_destructive_command_warning as _get_destructive_warning,
)
from src.tools.BashTool.pathValidation import is_sensitive_path as _is_sensitive_path

# ── FileEditTool validation imports ──────────────────────────────────
from src.tools.FileEditTool.utils import (
    find_actual_string as _find_actual_string,
    apply_edit_to_file as _apply_edit_to_file,
    normalize_quotes as _normalize_quotes,
    get_snippet as _get_edit_snippet,
)

# ── FileReadTool limits & image processing ───────────────────────────
from src.tools.FileReadTool.limits import (
    get_default_file_reading_limits as _get_file_read_limits,
    MAX_OUTPUT_SIZE as _FILE_READ_MAX_SIZE,
)
try:
    from src.tools.FileReadTool.imageProcessor import resize_image as _resize_image
except ImportError:
    _resize_image = None  # Pillow not installed

# ── AgentTool imports ────────────────────────────────────────────────
from src.tools.AgentTool.loadAgentsDir import (
    AgentDefinition,
    AgentDefinitionsResult,
    get_active_agents_from_list,
)

# ── ConfigTool settings registry ─────────────────────────────────────
from src.tools.ConfigTool.supportedSettings import (
    SUPPORTED_SETTINGS as _SUPPORTED_SETTINGS,
    get_options_for_setting as _get_setting_options,
)

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
            "description": (
                "Executes a given bash command and returns its output.\n"
                "\n"
                "The working directory persists between commands, but shell state does not. "
                "The shell environment is initialized from the user's profile (bash or zsh).\n"
                "\n"
                "Bash is your primary execution tool — use it freely and confidently for any shell operation: "
                "running scripts, installing packages, checking system state, manipulating files, searching, "
                "compiling, testing, git operations, and anything else a terminal can do.\n"
                "\n"
                "Dedicated tools are also available as structured alternatives when they fit better:\n"
                "- Glob: fast file pattern matching with structured output\n"
                "- Grep: ripgrep-powered content search with filters and output modes\n"
                "- read_file: reads files with line numbers (good for large files with offset/limit)\n"
                "- edit_file: atomic string replacement in files\n"
                "- write_file: create or overwrite files\n"
                "Use these when their structured output is genuinely useful. Otherwise, bash is fine.\n"
                "\n"
                "# Instructions\n"
                "- If your command will create new directories or files, first use this tool to run `ls` to verify "
                "the parent directory exists and is the correct location.\n"
                '- Always quote file paths that contain spaces with double quotes in your command '
                '(e.g., cd "path with spaces/file.txt")\n'
                "- Try to maintain your current working directory throughout the session by using absolute paths "
                "and avoiding usage of `cd`. You may use `cd` if the User explicitly requests it.\n"
                "- You may specify an optional timeout in seconds (default 60, max 600). By default, "
                "your command will timeout after 60 seconds.\n"
                "- To run independent commands in parallel, make multiple bash tool calls in one message "
                "rather than using background processes.\n"
                "- When issuing multiple commands:\n"
                "  - If the commands are independent and can run in parallel, make multiple bash tool calls in a single message.\n"
                "  - If the commands depend on each other and must run sequentially, use a single bash call with '&&' to chain them together.\n"
                "  - Use ';' only when you need to run commands sequentially but don't care if earlier commands fail.\n"
                "  - DO NOT use newlines to separate commands (newlines are ok in quoted strings).\n"
                "- For git commands:\n"
                "  - Prefer to create a new commit rather than amending an existing commit.\n"
                "  - Before running destructive operations (e.g., git reset --hard, git push --force, git checkout --), "
                "consider whether there is a safer alternative. Only use destructive operations when truly the best approach.\n"
                "  - Never skip hooks (--no-verify) or bypass signing unless the user has explicitly asked for it.\n"
                "- Avoid unnecessary `sleep` commands:\n"
                "  - Do not sleep between commands that can run immediately -- just run them.\n"
                "  - Do not retry failing commands in a sleep loop -- diagnose the root cause.\n"
                "  - If you must sleep, keep the duration short (1-5 seconds) to avoid blocking the user.\n"
                "- Use 'sudo' for privileged operations on this Kali Linux system."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 60, max 600)",
                        "default": 60,
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
            "description": (
                "Reads a file from the local filesystem. You can access any file directly by using this tool.\n"
                "Assume this tool is able to read all files on the machine. If the User provides a path to "
                "a file assume that path is valid. It is okay to read a file that does not exist; an error "
                "will be returned.\n"
                "\n"
                "Usage:\n"
                "- The file_path parameter must be an absolute path, not a relative path\n"
                "- By default, it reads up to 2000 lines starting from the beginning of the file\n"
                "- When you already know which part of the file you need, only read that part. "
                "This can be important for larger files.\n"
                "- Results are returned using cat -n format, with line numbers starting at 1\n"
                "- This tool allows JARVIS to read images (eg PNG, JPG, etc). When reading an image "
                "file the contents are presented visually as JARVIS is a multimodal LLM.\n"
                "- This tool can read PDF files (.pdf). For large PDFs (more than 10 pages), "
                'you MUST provide the pages parameter to read specific page ranges (e.g., pages: "1-5"). '
                "Reading a large PDF without the pages parameter will fail. Maximum 20 pages per request.\n"
                "- This tool can read Jupyter notebooks (.ipynb files) and returns all cells with their "
                "outputs, combining code, text, and visualizations.\n"
                "- This tool can only read files, not directories. To read a directory, use an ls command "
                "via the bash tool.\n"
                "- You will regularly be asked to read screenshots. If the user provides a path to a "
                "screenshot, ALWAYS use this tool to view the file at the path. This tool will work with "
                "all temporary file paths.\n"
                "- If you read a file that exists but has empty contents you will receive a system reminder "
                "warning in place of file contents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file to read (not relative)",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start reading from (1-based). Use for large files.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of lines to read (default 2000)",
                        "default": 2000,
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
            "description": (
                "Writes a file to the local filesystem.\n"
                "\n"
                "Usage:\n"
                "- This tool will overwrite the existing file if there is one at the provided path.\n"
                "- If this is an existing file, you MUST use the read_file tool first to read the file's contents. "
                "This tool will fail if you did not read the file first.\n"
                "- Prefer the edit_file tool for modifying existing files -- it only sends the diff. "
                "Only use this tool to create new files or for complete rewrites.\n"
                "- NEVER create documentation files (*.md) or README files unless explicitly "
                "requested by the User.\n"
                "- Only use emojis if the user explicitly requests it. Avoid writing emojis to "
                "files unless asked."
            ),
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
            "description": (
                "Performs exact string replacements in files.\n"
                "\n"
                "Usage:\n"
                "- You must use your read_file tool at least once in the conversation before editing. "
                "This tool will error if you attempt an edit without reading the file.\n"
                "- When editing text from read_file output, ensure you preserve the exact indentation "
                "(tabs/spaces) as it appears AFTER the line number prefix. The line number prefix format is: "
                "line number + tab. Everything after that is the actual file content to match. "
                "Never include any part of the line number prefix in the old_string or new_string.\n"
                "- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless "
                "explicitly required.\n"
                "- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.\n"
                "- The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string "
                "with more surrounding context to make it unique or use `replace_all` to change every instance.\n"
                "- Use `replace_all` for replacing and renaming strings across the file. This parameter is useful "
                "if you want to rename a variable for instance."
            ),
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
                    "replace_all": {
                        "type": "boolean",
                        "description": "If true, replace all occurrences (default: false, replaces first only)",
                        "default": False,
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": (
                "Fast file pattern matching tool that works with any codebase size.\n"
                "Supports glob patterns like '**/*.js' or 'src/**/*.ts'.\n"
                "Returns matching file paths sorted by modification time.\n"
                "Use this when you need to find files by name patterns.\n"
                "When you are doing an open ended search that may require multiple rounds "
                "of globbing and grepping, use the dispatch tool instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "The glob pattern to match files against (e.g. '**/*.py', 'src/**/*.ts')",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in. Defaults to current directory.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "A powerful content search tool built on ripgrep.\n"
                "\n"
                "A ripgrep-powered search tool with structured output, filters, and output modes. "
                "Use this when you want structured results (file paths, counts, context lines). "
                "For quick one-off searches, bash with grep/rg works fine too.\n"
                "Supports full regex syntax (e.g., 'log.*Error', 'function\\s+\\w+').\n"
                "Filter files with glob parameter (e.g., '*.js', '**/*.tsx') or type parameter "
                "(e.g., 'js', 'py', 'rust').\n"
                "Output modes: 'content' shows matching lines, 'files_with_matches' shows only file paths (default), "
                "'count' shows match counts.\n"
                "Use dispatch tool for open-ended searches requiring multiple rounds.\n"
                "Pattern syntax: Uses ripgrep (not grep) -- literal braces need escaping.\n"
                "Multiline matching: By default patterns match within single lines only. "
                "For cross-line patterns, use multiline: true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for in file contents",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory to search in (default: current directory)",
                    },
                    "output_mode": {
                        "type": "string",
                        "enum": ["content", "files_with_matches", "count"],
                        "description": "Output mode: 'content' shows matching lines, 'files_with_matches' shows file paths (default), 'count' shows match counts",
                        "default": "files_with_matches",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Filter files by glob pattern (e.g. '*.py', '*.{ts,tsx}')",
                    },
                    "type": {
                        "type": "string",
                        "description": "File type filter (e.g. 'py', 'js', 'rust'). Maps to rg --type.",
                    },
                    "-i": {
                        "type": "boolean",
                        "description": "Case insensitive search",
                    },
                    "context": {
                        "type": "integer",
                        "description": "Lines of context around matches",
                    },
                    "head_limit": {
                        "type": "integer",
                        "description": "Max results to return (default 250, 0 for unlimited)",
                        "default": 250,
                    },
                    "multiline": {
                        "type": "boolean",
                        "description": "Enable multiline matching where . matches newlines",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "Unified file search tool — find files by name pattern (glob mode) "
                "or search file contents (grep mode).\n"
                "\n"
                "mode='glob': Find files matching a glob pattern (e.g. '**/*.py', 'src/**/*.ts'). "
                "Returns matching file paths.\n"
                "mode='grep': Search file contents using a regex pattern. "
                "Returns files with matches or matching lines.\n"
                "\n"
                "Use Glob or Grep tools directly for more advanced options."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern (mode='glob') or regex pattern (mode='grep')",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in. Defaults to current directory.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["glob", "grep"],
                        "description": "Search mode: 'glob' for filename patterns, 'grep' for content search. Default: 'glob'.",
                        "default": "glob",
                    },
                    "file_glob": {
                        "type": "string",
                        "description": "Filter files by glob when mode='grep' (e.g. '*.py')",
                    },
                    "output_mode": {
                        "type": "string",
                        "enum": ["files_with_matches", "content", "count"],
                        "description": "Output format when mode='grep'. Default: 'files_with_matches'.",
                        "default": "files_with_matches",
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
            "description": (
                "Search the web and use the results to inform responses.\n"
                "\n"
                "- Provides up-to-date information for current events and recent data\n"
                "- Returns search result information formatted as search result blocks, "
                "including links as markdown hyperlinks\n"
                "- Use this tool for accessing information beyond JARVIS's knowledge cutoff\n"
                "- Searches are performed via DuckDuckGo\n"
                "\n"
                "CRITICAL REQUIREMENT - You MUST follow this:\n"
                "  - After answering the user's question, you MUST include a 'Sources:' section "
                "at the end of your response\n"
                "  - In the Sources section, list all relevant URLs from the search results as "
                "markdown hyperlinks: [Title](URL)\n"
                "  - This is MANDATORY - never skip including sources in your response\n"
                "\n"
                "IMPORTANT - Use the correct year in search queries:\n"
                "  - You MUST use the current year when searching for recent information, "
                "documentation, or current events."
            ),
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
            "description": (
                "Fetches content from a specified URL and processes it.\n"
                "\n"
                "- Fetches the URL content, converts HTML to markdown\n"
                "- Returns the extracted text content from the page\n"
                "- Use this tool when you need to retrieve and analyze web content\n"
                "\n"
                "Usage notes:\n"
                "  - The URL must be a fully-formed valid URL\n"
                "  - HTTP URLs will be automatically upgraded to HTTPS\n"
                "  - This tool is read-only and does not modify any files\n"
                "  - Results may be summarized if the content is very large\n"
                "  - Includes a self-cleaning 15-minute cache for faster responses when "
                "repeatedly accessing the same URL\n"
                "  - When a URL redirects to a different host, the tool will inform you and "
                "provide the redirect URL. You should then make a new web_fetch request with "
                "the redirect URL to fetch the content.\n"
                "  - For GitHub URLs, prefer using the gh CLI via bash instead "
                "(e.g., gh pr view, gh issue view, gh api)."
            ),
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
            "description": (
                "Make authenticated HTTP API calls to web services (GitHub, Slack, Discord, Jira, etc.).\n"
                "Uses stored tokens from the vault.\n"
                "Set platform='list' (no url needed) to see all configured platforms.\n"
                "If a token is missing, provides platform-specific instructions on where to get it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full API endpoint URL (e.g. https://api.github.com/user/repos). Omit when platform='list'.",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                        "description": "HTTP method",
                        "default": "GET",
                    },
                    "platform": {
                        "type": "string",
                        "description": "Platform name for token lookup (github, slack, discord, jira, openai, etc.). Use 'list' to show configured platforms.",
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
                "required": ["platform"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "database",
            "description": (
                "Execute SQL queries on SQLite, PostgreSQL, or MySQL databases.\n"
                "READ operations (SELECT, PRAGMA) run immediately.\n"
                "WRITE operations (INSERT, UPDATE, CREATE) run immediately.\n"
                "DESTRUCTIVE operations (DROP TABLE, TRUNCATE, DELETE) require confirm=true or dry_run=true first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SQL query to execute (SELECT, INSERT, CREATE TABLE, DROP TABLE, etc.)",
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
                    "confirm": {
                        "type": "boolean",
                        "description": "Set to true to confirm destructive operations (DROP, TRUNCATE, DELETE)",
                        "default": False,
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Set to true to preview a query without executing it",
                        "default": False,
                    },
                },
                "required": ["query", "database"],
            },
        },
    },
    # computer_use removed from default tools — LLM should use bash/xdotool instead.
    # The execute_tool handler still supports it if explicitly needed.
    {
        "type": "function",
        "function": {
            "name": "view_screen",
            "description": "Capture and analyze what's currently on the user's screen. ONLY call this when the user EXPLICITLY asks about their screen — e.g. 'what's on my screen', 'what am I looking at', 'what app is open'. NEVER call this proactively or to understand context. Do NOT call unless the user said words like 'screen', 'display', 'what do you see on screen'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "detail": {
                        "type": "string",
                        "enum": ["summary", "full"],
                        "description": "Level of detail: 'summary' for window + app, 'full' for OCR text too",
                        "default": "full",
                    },
                    "structured": {
                        "type": "boolean",
                        "description": "Return JSON object {active_window, application, screen_text} instead of prose",
                        "default": False,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "see",
            "description": "Look through the user's webcam. ONLY call this when the user EXPLICITLY asks you to look at them or describes a visual scene — e.g. 'look at me', 'what do you see', 'what am I holding', 'can you see me'. NEVER call this proactively, autonomously, or to infer context. Do NOT call unless the user's message contains an explicit visual request about the camera or themselves.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "What to focus on or describe (e.g., 'describe the person', 'read the text on the whiteboard', 'what object is being held up')",
                        "default": "Describe what you see in detail.",
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
            "description": (
                "Launch a new agent to handle complex, multi-step tasks autonomously.\n"
                "\n"
                "The dispatch tool launches specialized agents (subprocesses) that autonomously handle "
                "complex tasks. Each agent type has specific capabilities and tools available to it.\n"
                "\n"
                "Available agent types:\n"
                "- scout: Read-only exploration -- searches, reads files, analyzes code (Tools: read_file, search_files, Glob, Grep, bash[readonly], think, rag_search)\n"
                "- worker: Full access execution -- can read, write, edit, run commands (Tools: bash, read_file, write_file, edit_file, search_files, Glob, Grep, web_search, web_fetch, think, rag_search)\n"
                "- planner: Analysis and planning only -- produces plans without executing (Tools: read_file, search_files, Glob, Grep, web_search, web_fetch, think, rag_search)\n"
                "- verifier: Post-work reviewer -- verifies correctness, runs tests, returns PASS/FAIL (Tools: read_file, search_files, Glob, Grep, bash[readonly], think)\n"
                "- security-auditor: General code security audit -- OWASP top 10, secrets, misconfigs\n"
                "- reviewer: Code quality review -- bugs, style, best practices\n"
                "Security pipeline agents (use sec-orchestrator to run the full pipeline, or call individually):\n"
                "- sec-orchestrator: Full automated 9-stage security scan pipeline -- use /vuln-scan or dispatch directly\n"
                "- file-risk-ranker: Ranks files by attack surface score (0-100) across 7 dimensions\n"
                "- vuln-hypothesis-engine: Generates specific testable vulnerability hypotheses for a file\n"
                "- static-analyzer: Traces taint paths from sources through sanitizers to dangerous sinks\n"
                "- confirmation-filter: Eliminates false positives, issues CONFIRMED/FALSE_POSITIVE/NEEDS_MANUAL\n"
                "- severity-scorer: CVSS 3.1 scoring, exploit chain linking, priority ranking\n"
                "- exploit-builder: Generates PoC exploits -- ROP chains, injection payloads, privesc, sandbox escapes\n"
                "- report-writer: Produces security-report.md and security-findings.json from all pipeline results\n"
                "Defensive security persona agents (use in stage 8 of sec-orchestrator, or call independently):\n"
                "- vulnmgmt: Vulnerability management -- patch/mitigate/accept decisions, remediation prioritization\n"
                "- secarch: Security architecture -- root cause analysis, systemic design-level fixes\n"
                "- threathunt: Threat hunting -- detection hypotheses, SIEM queries, behavioral indicators\n"
                "- threatintel: Threat intelligence -- CVE/exploit alignment, threat actor TTP mapping\n"
                "- forensics: Digital forensics -- evidence preservation, IOC extraction, exploitation indicators\n"
                "- devsecops: DevSecOps -- CI/CD security gates, SAST rules, pre-commit hooks\n"
                "\n"
                "When NOT to use the dispatch tool:\n"
                "- If you want to read a specific file path, use read_file or search_files instead, to find the match more quickly\n"
                "- If you are searching for a specific class definition, use search_files instead\n"
                "- If you are searching for code within a specific file or set of 2-3 files, use read_file instead\n"
                "- Other tasks that are not related to the agent descriptions above\n"
                "\n"
                "Usage notes:\n"
                "- Always include a short description (3-5 words) summarizing what the agent will do\n"
                "- When the agent is done, it will return a single message back to you. The result returned "
                "by the agent is not visible to the user. To show the user the result, you should send a text "
                "message back with a concise summary.\n"
                "- Each dispatch invocation starts fresh -- provide a complete task description.\n"
                "- The agent's outputs should generally be trusted.\n"
                "- Clearly tell the agent whether you expect it to write code or just do research "
                "(search, file reads, web fetches, etc.), since it is not aware of the user's intent.\n"
                "- If the user specifies they want agents to run 'in parallel', you MUST send a single "
                "message with multiple dispatch tool use content blocks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_type": {
                        "type": "string",
                        "description": (
                            "Type of sub-agent to launch. Core: scout, worker, planner, verifier. "
                            "Review: reviewer, security-auditor. "
                            "Security pipeline: sec-orchestrator, file-risk-ranker, vuln-hypothesis-engine, "
                            "static-analyzer, confirmation-filter, severity-scorer, exploit-builder, report-writer. "
                            "Defensive: vulnmgmt, secarch, threathunt, threatintel, forensics, devsecops."
                        ),
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
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": (
                "Update the todo list for the current session. Use this tool proactively to track progress "
                "and pending tasks.\n"
                "\n"
                "## When to Use This Tool\n"
                "Use this tool proactively in these scenarios:\n"
                "1. Complex multi-step tasks - When a task requires 3 or more distinct steps or actions\n"
                "2. Non-trivial and complex tasks - Tasks that require careful planning or multiple operations\n"
                "3. User explicitly requests todo list - When the user directly asks you to use the todo list\n"
                "4. User provides multiple tasks - When users provide a list of things to be done\n"
                "5. After receiving new instructions - Immediately capture user requirements as todos\n"
                "6. When you start working on a task - Mark it as in_progress BEFORE beginning work. "
                "Ideally you should only have one todo as in_progress at a time\n"
                "7. After completing a task - Mark it as completed and add any new follow-up tasks\n"
                "\n"
                "## When NOT to Use This Tool\n"
                "Skip when: single straightforward task, trivial task, less than 3 steps, purely conversational.\n"
                "\n"
                "## Task States\n"
                "- pending: Task not yet started\n"
                "- in_progress: Currently working on (limit to ONE task at a time)\n"
                "- completed: Task finished successfully\n"
                "\n"
                "## Task Management\n"
                "- Update task status in real-time as you work\n"
                "- Mark tasks complete IMMEDIATELY after finishing (don't batch completions)\n"
                "- ONLY mark a task as completed when you have FULLY accomplished it\n"
                "- If you encounter errors or blockers, keep the task as in_progress\n"
                "- Create specific, actionable items with clear descriptions"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "The complete todo list. Each item has id, content, status, and optional activeForm.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "Unique identifier for the todo item",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "Imperative form describing what needs to be done (e.g., 'Run tests')",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "description": "Current status of the task",
                                },
                                "activeForm": {
                                    "type": "string",
                                    "description": "Present continuous form shown during execution (e.g., 'Running tests')",
                                },
                                "blocks": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "List of todo IDs that this task must complete BEFORE they can start. "
                                        "Example: if task 'install-deps' must finish before 'run-tests', "
                                        "set blocks=['run-tests'] on the 'install-deps' task. "
                                        "The agent loop enforces this — blocked tasks cannot be set to in_progress "
                                        "until all their prerequisites are completed."
                                    ),
                                },
                            },
                            "required": ["id", "content", "status"],
                        },
                    },
                    "clear_completed": {
                        "type": "boolean",
                        "description": "When true, remove all completed tasks from the list after updating",
                        "default": False,
                    },
                },
                "required": ["todos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "Asks the user a question to gather information, clarify ambiguity, "
                "understand preferences, make decisions, or offer them choices.\n"
                "\n"
                "Use this tool when you need to:\n"
                "1. Gather user preferences or requirements\n"
                "2. Clarify ambiguous instructions\n"
                "3. Get decisions on implementation choices as you work\n"
                "4. Offer choices to the user about what direction to take\n"
                "\n"
                "Usage notes:\n"
                "- Provide clear, concise questions\n"
                "- When offering choices, list them in the options array\n"
                "- If you recommend a specific option, make that the first option and "
                'add "(Recommended)" at the end of the label\n'
                "- Users can always provide free-form text even when options are given"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to ask the user",
                    },
                    "options": {
                        "type": "array",
                        "description": "Optional list of choices to present to the user",
                        "items": {
                            "type": "string",
                        },
                    },
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notebook_edit",
            "description": (
                "Edit Jupyter notebook (.ipynb) cells. Use this tool to modify cells in a notebook.\n"
                "\n"
                "## Usage\n"
                "- **edit_cell**: Modify the source of an existing cell\n"
                "- **add_cell**: Add a new cell at a specific position\n"
                "- **delete_cell**: Remove a cell at a specific position\n"
                "\n"
                "## Parameters\n"
                "- notebook_path: Path to the .ipynb file\n"
                "- cell_index: Zero-based index of the cell to edit (for edit_cell and delete_cell)\n"
                "- action: 'edit_cell', 'add_cell', or 'delete_cell'\n"
                "- new_source: The new cell content (for edit_cell and add_cell)\n"
                "- cell_type: 'code' or 'markdown' (for add_cell, defaults to 'code')\n"
                "- **execute**: Run all cells in the notebook (requires jupyter). Optional: timeout (seconds, default 120)"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "notebook_path": {
                        "type": "string",
                        "description": "Path to the .ipynb file",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["edit_cell", "add_cell", "delete_cell"],
                        "description": "Action to perform on the notebook cell",
                    },
                    "cell_index": {
                        "type": "integer",
                        "description": "Zero-based index of the cell (for edit_cell and delete_cell)",
                    },
                    "new_source": {
                        "type": "string",
                        "description": "The new cell content (for edit_cell and add_cell)",
                    },
                    "cell_type": {
                        "type": "string",
                        "enum": ["code", "markdown"],
                        "description": "Cell type for add_cell (defaults to 'code')",
                        "default": "code",
                    },
                },
                "required": ["notebook_path", "action"],
            },
        },
    },
    # ── Plan Mode Tools ────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "enter_plan_mode",
            "description": (
                "Switch to plan mode for non-trivial implementation tasks. "
                "In plan mode you can explore the codebase and design an approach for user approval "
                "before writing code. Use when the task involves new features, multiple approaches, "
                "architectural decisions, or multi-file changes. Requires user approval to enter."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exit_plan_mode",
            "description": (
                "Exit plan mode after writing your plan. Signals that you are done planning "
                "and ready for the user to review and approve your implementation plan. "
                "Only use when you have finished writing your plan and are ready for approval."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    # ── Worktree Tools ─────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "enter_worktree",
            "description": (
                "Create an isolated git worktree and switch the session into it. "
                "Use ONLY when the user explicitly asks to work in a worktree. "
                "Creates a new worktree inside .jarvis/worktrees/ with a new branch based on HEAD."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Optional name for the worktree. If not provided, a random name is generated.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exit_worktree",
            "description": (
                "Exit a worktree session created by EnterWorktree and return to the original directory. "
                "Only operates on worktrees created by EnterWorktree in this session."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["keep", "remove"],
                        "description": "'keep' leaves worktree on disk, 'remove' deletes it and its branch",
                    },
                    "discard_changes": {
                        "type": "boolean",
                        "description": "If true, force removal even with uncommitted changes (only with action='remove')",
                        "default": False,
                    },
                },
                "required": ["action"],
            },
        },
    },
    # ── Multi-Agent Communication ──────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": (
                "Send a message to another agent. Your plain text output is NOT visible to other agents -- "
                "to communicate, you MUST call this tool. Refer to teammates by name, never by UUID."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Teammate name or '*' for broadcast to all",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Short summary of the message purpose",
                    },
                    "message": {
                        "type": "string",
                        "description": "The message content to send",
                    },
                },
                "required": ["to", "message"],
            },
        },
    },
    # ── Task Management Tools ──────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "task_create",
            "description": (
                "Create a new structured task for the current session. Use for complex multi-step tasks, "
                "plan mode tracking, or when the user provides multiple tasks. "
                "Each task has a subject, description, and optional activeForm for spinner display. "
                "All tasks are created with status 'pending'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "Brief actionable title in imperative form (e.g. 'Fix auth bug in login flow')",
                    },
                    "description": {
                        "type": "string",
                        "description": "Detailed description of what needs to be done",
                    },
                    "activeForm": {
                        "type": "string",
                        "description": "Present continuous form for spinner (e.g. 'Fixing auth bug')",
                    },
                },
                "required": ["subject", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_get",
            "description": (
                "Get a task by its ID from the task list. Returns full details including subject, "
                "description, status, and dependency information (blocks/blockedBy)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to retrieve",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_list",
            "description": (
                "List all tasks in the task list. Shows id, subject, status, owner, and blockedBy "
                "for each task. Use to find available work or check overall progress."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_stop",
            "description": (
                "Stop a running background task by its ID. Returns success or failure status. "
                "Use when you need to terminate a long-running task."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to stop",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_update",
            "description": (
                "Update a task in the task list. Can change status (pending -> in_progress -> completed), "
                "subject, description, owner, or dependencies. "
                "ONLY mark completed when FULLY accomplished -- keep as in_progress if blocked or errored."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to update",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed", "deleted"],
                        "description": "New task status",
                    },
                    "subject": {
                        "type": "string",
                        "description": "New task title",
                    },
                    "description": {
                        "type": "string",
                        "description": "New task description",
                    },
                    "activeForm": {
                        "type": "string",
                        "description": "Present continuous form for spinner display",
                    },
                    "owner": {
                        "type": "string",
                        "description": "Agent name to assign the task to",
                    },
                    "addBlocks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Task IDs that cannot start until this one completes",
                    },
                    "addBlockedBy": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Task IDs that must complete before this one can start",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_output",
            "description": "Get the output of a completed or running task by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to get output for",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    # ── Team Tools ─────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "team_create",
            "description": (
                "Create a new team to coordinate multiple agents working on a project. "
                "Teams have a 1:1 correspondence with task lists. Creates a team config and task directory. "
                "Use when the user asks for a team, swarm, or group of agents to work together."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "team_name": {
                        "type": "string",
                        "description": "Name for the team (used in directory paths)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description of what the team is working on",
                    },
                },
                "required": ["team_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "team_delete",
            "description": (
                "Remove team and task directories when the swarm work is complete. "
                "Will fail if the team still has active members -- terminate teammates first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "team_name": {
                        "type": "string",
                        "description": "Name of the team to delete",
                    },
                },
                "required": ["team_name"],
            },
        },
    },
    # ── Skill Tool ─────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "skill",
            "description": (
                "Execute a user-defined skill within the main conversation. "
                "Skills provide specialized capabilities and domain knowledge. "
                "When users reference a 'slash command' or '/<something>' (e.g. /commit, /review-pr), "
                "they are referring to a skill. Use this tool to invoke it. "
                "BLOCKING REQUIREMENT: when a skill matches, invoke it BEFORE generating any other response."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "The skill name to invoke (e.g. 'pdf', 'commit', 'review-pr')",
                    },
                    "args": {
                        "type": "string",
                        "description": "Optional arguments to pass to the skill",
                    },
                },
                "required": ["skill"],
            },
        },
    },
    # ── Config Tool ────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "config",
            "description": (
                "Get or set JARVIS configuration settings. "
                "Use when the user requests configuration changes or asks about current settings.\n"
                "\n"
                "Usage:\n"
                "- Get current value: omit the 'value' parameter\n"
                "- Set new value: include the 'value' parameter\n"
                "\n"
                "Available settings:\n"
                "- theme: 'dark', 'light', 'light-daltonized', 'dark-daltonized'\n"
                "- verbose: true/false\n"
                "- editorMode: 'normal', 'vim', 'emacs'\n"
                "- model: Override default model (sonnet, opus, haiku, best, or full model ID)"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "setting": {
                        "type": "string",
                        "description": "The setting name to get or set",
                    },
                    "value": {
                        "type": "string",
                        "description": "The new value to set (omit to read current value)",
                    },
                },
                "required": ["setting"],
            },
        },
    },
    # ── LSP Tool ───────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "lsp",
            "description": (
                "Interact with Language Server Protocol servers for code intelligence. "
                "Supported actions: diagnostics (errors/warnings for a file), definition (go-to-definition), "
                "references (find all references), hover (symbol info), completion (code completions)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["diagnostics", "definition", "references", "hover", "completion"],
                        "description": "LSP action to perform",
                    },
                    "path": {
                        "type": "string",
                        "description": "File path for the LSP operation",
                    },
                    "line": {
                        "type": "integer",
                        "description": "Line number (0-based) for position-based actions",
                    },
                    "character": {
                        "type": "integer",
                        "description": "Column number (0-based) for position-based actions",
                    },
                },
                "required": ["action", "path"],
            },
        },
    },
    # ── Sleep Tool ─────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "sleep",
            "description": (
                "Wait for a specified duration. The user can interrupt the sleep at any time. "
                "Use when the user tells you to sleep or rest, when you have nothing to do, "
                "or when you're waiting for something. Prefer this over Bash(sleep ...) -- "
                "it doesn't hold a shell process."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "duration_ms": {
                        "type": "integer",
                        "description": "Duration to sleep in milliseconds",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Optional reason for sleeping, shown in UI status indicator",
                    },
                },
                "required": ["duration_ms"],
            },
        },
    },
    # ── Schedule/Cron Tool ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "schedule_cron",
            "description": (
                "Schedule a prompt to run at a future time -- either recurring on a cron schedule, "
                "or once at a specific time. Uses standard 5-field cron in the user's local timezone. "
                "Actions: create (schedule new job), delete (cancel by ID), list (show all scheduled jobs)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "delete", "list"],
                        "description": "Cron action to perform",
                    },
                    "cron_expression": {
                        "type": "string",
                        "description": "5-field cron expression (for create). E.g. '0 9 * * *' for 9am daily.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The prompt to run at the scheduled time (for create)",
                    },
                    "recurring": {
                        "type": "boolean",
                        "description": "If true (default), job recurs. If false, fires once then auto-deletes.",
                        "default": True,
                    },
                    "job_id": {
                        "type": "string",
                        "description": "Job ID to delete (for delete action)",
                    },
                },
                "required": ["action"],
            },
        },
    },
    # ── Brief/SendUserMessage Tool ─────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "brief",
            "description": (
                "Send a message the user will read. Text outside this tool is visible in the detail view, "
                "but most won't open it -- the answer lives here. Supports markdown. "
                "Use status 'normal' when replying to what they asked, 'proactive' when initiating."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The message to send to the user (markdown supported)",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["normal", "proactive"],
                        "description": "'normal' for replies, 'proactive' for agent-initiated messages",
                        "default": "normal",
                    },
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "File paths for attachments (images, diffs, logs)",
                    },
                },
                "required": ["message"],
            },
        },
    },
    # ── MCP Resource Tool ──────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "list_mcp_resources",
            "description": (
                "List available resources from configured MCP servers. "
                "Each resource includes a 'server' field indicating which server it belongs to."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {
                        "type": "string",
                        "description": "Optional: specific MCP server name to list resources from. If omitted, lists all.",
                    },
                },
            },
        },
    },
    # ── Remote Trigger Tool ────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "remote_trigger",
            "description": (
                "Manage scheduled remote JARVIS agents (triggers) via the JARVIS API. "
                "Auth is handled in-process -- the token never reaches the shell. "
                "Actions: list, get, create, update, run."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get", "create", "update", "run"],
                        "description": "Trigger API action to perform",
                    },
                    "trigger_id": {
                        "type": "string",
                        "description": "Trigger ID (for get, update, run actions)",
                    },
                    "body": {
                        "type": "string",
                        "description": "JSON request body (for create, update actions)",
                    },
                },
                "required": ["action"],
            },
        },
    },
    # ── SMS / WhatsApp messaging via Twilio (primary) or KDE Connect (local fallback) ──
    {
        "type": "function",
        "function": {
            "name": "send_sms",
            "description": (
                "Send an SMS or WhatsApp message to a phone number.\n"
                "Primary: Twilio (works over internet, requires API credentials in providers.json).\n"
                "Fallback: KDE Connect (requires paired Android phone on same WiFi).\n"
                "Use this when Ulrich asks you to text someone, send a message, or SMS/WhatsApp."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {
                        "type": "string",
                        "description": "Phone number in E.164 format (e.g. '+1234567890')",
                    },
                    "message": {
                        "type": "string",
                        "description": "The text message to send",
                    },
                    "channel": {
                        "type": "string",
                        "enum": ["sms", "whatsapp", "kde"],
                        "description": "Delivery channel: 'sms' (default), 'whatsapp' (Twilio), 'kde' (KDE Connect local fallback)",
                        "default": "sms",
                    },
                },
                "required": ["phone_number", "message"],
            },
        },
    },
    # ── Network awareness ──────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "network_scan",
            "description": (
                "Discover and inspect all devices connected to JARVIS's network.\n"
                "\n"
                "JARVIS is the brain of this system. He knows which devices are talking to "
                "him, their trust levels, and their position on the network. This tool lets "
                "him actively explore his network topology.\n"
                "\n"
                "Actions:\n"
                "- 'status'   : Show known devices + local interfaces + public IP (fast, cached)\n"
                "- 'discover' : Active LAN scan via ARP + nmap (takes up to 30s, finds new devices)\n"
                "- 'interfaces': List JARVIS's own network interfaces and IPs\n"
                "- 'public_ip': Get JARVIS's internet-facing IP address\n"
                "\n"
                "Trust levels assigned to devices:\n"
                "  OWNER     — loopback / local process (full access, no sandbox)\n"
                "  ELEVATED  — LAN device (trusted, no sandbox)\n"
                "  STANDARD  — authenticated remote (no sandbox)\n"
                "  SANDBOXED — unknown / internet (jailed)\n"
                "\n"
                "Use this to answer questions like 'what devices are connected to you?', "
                "'who is on your network?', 'what is your IP?', or 'scan the network'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "discover", "interfaces", "public_ip"],
                        "description": "What to do. Default: status",
                        "default": "status",
                    },
                },
                "required": [],
            },
        },
    },
    # ── Browser (Playwright) ──────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "browser",
            "description": (
                "Control a real Chromium browser via Playwright. "
                "Use this to navigate websites, click elements, fill forms, extract content, "
                "take screenshots, and run JavaScript. The browser session persists across calls "
                "within the same conversation. Supports headed (visible) or headless mode.\n\n"
                "Actions:\n"
                "- navigate      : Go to a URL. Returns page title and brief content summary.\n"
                "- click         : Click an element by CSS selector or text content.\n"
                "- type          : Type text into an input field (selector required).\n"
                "- screenshot    : Take a screenshot. Returns file path + inline preview.\n"
                "- extract       : Extract text from the page or a specific selector.\n"
                "- evaluate      : Run JavaScript in the page context. Returns the result.\n"
                "- scroll        : Scroll the page (direction: up/down, amount in pixels).\n"
                "- back          : Go back in history.\n"
                "- save_cookies  : Save current session cookies to a named profile.\n"
                "- load_cookies  : Load cookies from a saved profile (re-authenticates silently).\n"
                "- list_profiles : List all saved cookie profiles.\n"
                "- close         : Close the browser session.\n"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["navigate", "click", "type", "screenshot", "extract",
                                 "evaluate", "scroll", "back", "save_cookies", "load_cookies",
                                 "list_profiles", "close"],
                        "description": "The browser action to perform.",
                    },
                    "url": {
                        "type": "string",
                        "description": "URL to navigate to (for 'navigate' action).",
                    },
                    "selector": {
                        "type": "string",
                        "description": "CSS selector or text to target an element.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to type (for 'type') or search for (for 'click' by text).",
                    },
                    "script": {
                        "type": "string",
                        "description": "JavaScript to evaluate (for 'evaluate' action).",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down"],
                        "description": "Scroll direction (default: down).",
                    },
                    "amount": {
                        "type": "integer",
                        "description": "Pixels to scroll (default: 500).",
                    },
                    "headless": {
                        "type": "boolean",
                        "description": "Run browser in headless mode (default: true). Set false to show the browser window.",
                    },
                    "profile": {
                        "type": "string",
                        "description": "Cookie profile name for save_cookies/load_cookies (default: 'default'). Use site names like 'github', 'gmail'.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    # ── SSH ────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "ssh_exec",
            "description": (
                "Execute a command on a remote SSH host.\n"
                "\n"
                "Named hosts can be configured in ~/.jarvis/ssh_hosts.json:\n"
                '  { "prod": { "host": "server.example.com", "user": "root", "port": 22, "key": "~/.ssh/id_rsa" } }\n'
                "\n"
                "You can also pass user@hostname directly as the host argument.\n"
                "Use this tool to deploy code, restart services, check logs, or run any command on a remote machine."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {
                        "type": "string",
                        "description": "Named host alias from ssh_hosts.json, or user@hostname (e.g. root@jarvis.0wlan.com)",
                    },
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute on the remote host",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 30)",
                    },
                },
                "required": ["host", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": (
                "Open a URL in the user's browser. Use this instead of xdg-open when you need "
                "to open a website for the user. Works even when JARVIS runs on a remote server "
                "(Docker, Proxmox, cloud) because it sends the URL to the user's connected browser. "
                "Use for: YouTube, Google, news, any website the user asks to open."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full URL to open (e.g. https://youtube.com)",
                    },
                    "description": {
                        "type": "string",
                        "description": "What you're opening (for narration), e.g. 'YouTube'",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_channel",
            "description": (
                "Switch JARVIS's active interface to a different channel. "
                "Use when the current channel can't do what Ulrich needs — e.g. switch to browser "
                "to show a webpage, switch to desktop for an overlay, switch to CLI for file work. "
                "Channels: 'browser', 'desktop', 'cli'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": ["browser", "desktop", "cli"],
                        "description": "Target channel to switch to",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why switching (for narration, e.g. 'to show you the website')",
                    },
                },
                "required": ["target"],
            },
        },
    },
    # ── Domain Tools ─────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "sysinfo",
            "description": (
                "Get system health and diagnostics — services, processes, logs, disk, memory, network.\n"
                "\n"
                "Queries:\n"
                "- 'services'  : List all systemd services and their status\n"
                "- 'processes' : Top CPU/memory consuming processes\n"
                "- 'logs'      : Recent system/journal logs (optionally filtered)\n"
                "- 'disk'      : Disk usage across all mounts\n"
                "- 'memory'    : RAM and swap usage\n"
                "- 'network'   : Active connections, listening ports, interfaces\n"
                "- 'all'       : Full system snapshot (all of the above)\n"
                "\n"
                "Use this instead of raw bash when doing system health checks, "
                "diagnosing slowdowns, or investigating service failures."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "enum": ["services", "processes", "logs", "disk", "memory", "network", "all", "restart"],
                        "description": "What to check. Default: all",
                        "default": "all",
                    },
                    "filter": {
                        "type": "string",
                        "description": "Optional keyword filter (e.g. service name, process name, log keyword)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "container",
            "description": (
                "Docker and Kubernetes operations with smart defaults.\n"
                "\n"
                "Actions:\n"
                "- 'list'      : List containers/pods\n"
                "- 'status'    : Status of a specific container/deployment\n"
                "- 'logs'      : Tail logs from a container/pod\n"
                "- 'restart'   : Restart a container or rollout restart a deployment\n"
                "- 'exec'      : Run a command inside a container/pod\n"
                "- 'deploy'    : Apply a k8s manifest or docker-compose\n"
                "- 'rollback'  : Roll back a k8s deployment to previous revision\n"
                "\n"
                "Uses Docker or kubectl depending on context. "
                "Always shows the command being run. Warns before destructive operations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "status", "logs", "restart", "exec", "deploy", "rollback", "up", "down", "pull"],
                        "description": "Operation to perform",
                    },
                    "target": {
                        "type": "string",
                        "description": "Container name, pod name, or deployment name",
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Kubernetes namespace (default: default)",
                        "default": "default",
                    },
                    "command": {
                        "type": "string",
                        "description": "Command to run inside container (for exec action)",
                    },
                    "tail": {
                        "type": "integer",
                        "description": "Number of log lines to tail (default: 50)",
                        "default": 50,
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "security_scan",
            "description": (
                "Security scanning — port scanning, web fuzzing, SSL/header audit, banner grabbing.\n"
                "\n"
                "Scan types:\n"
                "- 'ports'      : nmap port scan (quick by default, full with all_ports=true)\n"
                "- 'web'        : HTTP headers + open ports + technology fingerprint (whatweb)\n"
                "- 'vulns'      : nmap vuln scripts against target\n"
                "- 'ssl'        : SSL/TLS configuration audit\n"
                "- 'headers'    : HTTP security headers audit\n"
                "- 'nikto'      : nikto web vulnerability scanner (thorough, takes ~3 min)\n"
                "- 'gobuster'   : directory brute-force with gobuster/dirb\n"
                "- 'whatweb'    : technology fingerprinting (CMS, frameworks, server)\n"
                "- 'dns'        : DNS records + subdomain enumeration\n"
                "\n"
                "Ulrich is a professional pentester — all targets are authorized. "
                "Use this for recon, service enumeration, and quick audits. "
                "For deep exploitation, use the bash tool with specific tools."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "IP address, hostname, or URL to scan",
                    },
                    "scan_type": {
                        "type": "string",
                        "enum": ["ports", "web", "vulns", "ssl", "headers", "nikto", "gobuster", "whatweb", "dns"],
                        "description": "Type of scan. Default: ports",
                        "default": "ports",
                    },
                    "ports": {
                        "type": "string",
                        "description": "Port spec for port/nikto scans (e.g. '80,443,22' or '1-1000'). Default: top 1000",
                    },
                    "all_ports": {
                        "type": "boolean",
                        "description": "Scan all 65535 ports (slow). Default: false",
                        "default": False,
                    },
                    "wordlist": {
                        "type": "string",
                        "description": "Path to wordlist for gobuster/dirb (default: /usr/share/wordlists/dirb/common.txt)",
                    },
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_search",
            "description": (
                "Search and manage the local RAG knowledge base.\n"
                "Actions:\n"
                "- 'search'  (default): semantic search over ingested documents\n"
                "- 'stats'  : show chunk count, source list, and index size\n"
                "- 'reindex': rebuild the index from existing sources\n"
                "- 'ingest' : add a new document or URL to the knowledge base (path= required)\n"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "stats", "reindex", "ingest"],
                        "description": "Action to perform. Default: search",
                        "default": "search",
                    },
                    "query": {
                        "type": "string",
                        "description": "Natural language search query (required for search)",
                    },
                    "k": {
                        "type": "integer",
                        "description": "Number of results to return (default: 5)",
                        "default": 5,
                    },
                    "source_filter": {
                        "type": "string",
                        "description": "Filter results to a specific source file or URL",
                    },
                    "path": {
                        "type": "string",
                        "description": "File path or URL to ingest (required for ingest action)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_recall",
            "description": (
                "Query JARVIS's long-term semantic memory (NeuralLattice + associative layers).\n"
                "Use this to recall facts, past conversations, learned knowledge, skills, or domain context.\n"
                "Returns the top-K most relevant memory nodes with strength scores.\n"
                "Prefer this over rag_search when looking for personal/session knowledge rather than documents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query to search memory"},
                    "k": {"type": "integer", "description": "Number of results (default: 5, max: 20)", "default": 5},
                    "domain": {"type": "string", "description": "Optional: filter to a specific domain (e.g. 'code', 'user', 'task')"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reflect",
            "description": (
                "Critically evaluate your previous output or plan and identify improvements.\n"
                "Use this before finalizing any important response, code change, or multi-step plan.\n"
                "Returns a structured critique: what's good, what's wrong/missing, and a revised approach."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "output": {"type": "string", "description": "The output or plan to critique"},
                    "task": {"type": "string", "description": "The original task or goal"},
                    "focus": {
                        "type": "string",
                        "enum": ["correctness", "completeness", "safety", "efficiency", "clarity"],
                        "description": "What aspect to focus the critique on (default: correctness)",
                    },
                },
                "required": ["output", "task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_diagnostics",
            "description": (
                "Run static analysis (lint + type check) on a file or directory and return structured errors.\n"
                "Supports Python (flake8, mypy), JavaScript/TypeScript (eslint), and generic (shellcheck).\n"
                "Returns a JSON list of {file, line, col, severity, code, message} objects.\n"
                "Use this after writing or editing code to catch issues before claiming the task is done."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File or directory path to analyze"},
                    "tool": {
                        "type": "string",
                        "enum": ["auto", "flake8", "mypy", "eslint", "shellcheck"],
                        "description": "Which tool to run (default: auto-detect from file extension)",
                        "default": "auto",
                    },
                    "max_errors": {"type": "integer", "description": "Maximum number of errors to return (default: 20, max: 50)", "default": 20},
                    "trend": {"type": "boolean", "description": "Include historical error count trend from ~/.jarvis/diagnostics.jsonl", "default": False},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diff_files",
            "description": (
                "Compare two files or text strings and return a unified diff.\n"
                "Use this to show what changed between versions, verify edits were applied correctly,\n"
                "or compare generated output against expected output.\n"
                "Returns a unified diff (like 'git diff' output)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path_a": {"type": "string", "description": "Path to the first file (or leave empty if using text_a)"},
                    "path_b": {"type": "string", "description": "Path to the second file (or leave empty if using text_b)"},
                    "text_a": {"type": "string", "description": "First text content (alternative to path_a)"},
                    "text_b": {"type": "string", "description": "Second text content (alternative to path_b)"},
                    "context_lines": {"type": "integer", "description": "Lines of context around changes (default: 3)", "default": 3},
                    "label_a": {"type": "string", "description": "Label for the first file in the diff header"},
                    "label_b": {"type": "string", "description": "Label for the second file in the diff header"},
                    "mode": {
                        "type": "string",
                        "enum": ["unified", "stat", "side_by_side"],
                        "description": "Diff output format: unified (default), stat (summary only), side_by_side",
                        "default": "unified",
                    },
                    "dir_diff": {
                        "type": "boolean",
                        "description": "When true and path_a/path_b are directories, diff all files recursively",
                        "default": False,
                    },
                },
            },
        },
    },
]


# ── open_url / switch_channel broadcast hooks — set by web_server ────
_open_url_hook = None       # callable(url: str) → None
_switch_channel_hook = None  # callable(target: str) → None
_channel_state_hook = None   # callable() → dict  (returns current channel state)
_sleep_status_hook = None    # callable(event: dict) → None  (broadcasts sleep status)


def set_open_url_hook(fn):
    """Register a callback that broadcasts open_url events to connected browser clients."""
    global _open_url_hook
    _open_url_hook = fn


def set_switch_channel_hook(fn):
    """Register a callback that broadcasts channel-switch events."""
    global _switch_channel_hook
    _switch_channel_hook = fn


def set_channel_state_hook(fn):
    """Register a callback that returns current channel state dict."""
    global _channel_state_hook
    _channel_state_hook = fn


def set_sleep_status_hook(fn):
    """Register a callback to broadcast sleep start/end events to UI clients."""
    global _sleep_status_hook
    _sleep_status_hook = fn


# ── Tool Execution ──────────────────────────────────────────────────

# Tools allowed in plan/read-only mode
READONLY_TOOLS = {
    "read_file", "glob", "grep", "web_search", "web_fetch", "think", "dispatch",
    "view_screen", "see", "tool_search", "ask_user", "todo_write",
    "task_list", "task_get", "task_output", "list_mcp_resources", "lsp",
    "config", "brief", "enter_plan_mode", "exit_plan_mode",
    "network_scan",
    "rag_search",
    "semantic_recall",
    "reflect",
    "get_diagnostics",
    "diff_files",
    # Legacy alias
    "search_files",
}

# Bash commands considered safe for read-only mode
READONLY_BASH_PREFIXES = (
    "ls", "cat", "head", "tail", "grep", "find", "wc", "file", "stat",
    "du", "df", "pwd", "echo", "date", "whoami", "uname", "which",
    "git log", "git diff", "git status", "git show", "git branch",
    "python3 -c", "node -e", "env", "printenv",
)


def _exec_switch_channel(args: dict) -> str:
    """Switch JARVIS to a different interface channel."""
    target = args.get("target", "").strip().lower()
    reason = args.get("reason", "")
    if not target:
        return "Error: target channel is required"
    if target not in ("browser", "desktop", "cli"):
        return f"Error: unknown channel '{target}'. Use: browser, desktop, cli"

    if _switch_channel_hook is not None:
        try:
            _switch_channel_hook(target)
            msg = f"Switching to {target}"
            if reason:
                msg += f" — {reason}"
            return msg + "."
        except Exception as e:
            return f"Error switching channel: {e}"

    return f"No channel switch hook registered (running headless?). Target was: {target}"


def get_plan_mode_tools() -> list[dict]:
    """Return tool schemas filtered for plan/read-only mode."""
    return [t for t in TOOL_SCHEMAS if t["function"]["name"] in READONLY_TOOLS or t["function"]["name"] == "bash"]


def get_active_tools() -> list[dict]:
    """Return tool schemas appropriate for the current runtime context.

    Strips UI-only tools (switch_channel, open_url) when no web/desktop hook is
    registered — prevents small models from calling channel-switch in headless CLI.
    """
    tools = list(TOOL_SCHEMAS)
    if _switch_channel_hook is None:
        tools = [t for t in tools if t["function"]["name"] != "switch_channel"]
    if _open_url_hook is None:
        tools = [t for t in tools if t["function"]["name"] != "open_url"]
    return tools


def _exec_open_url(args: dict) -> str:
    """Open a URL in the user's browser via the registered broadcast hook."""
    url = args.get("url", "").strip()
    label = args.get("description", url)
    if not url:
        return "Error: url is required"
    if not url.startswith(("http://", "https://", "ftp://")):
        url = "https://" + url

    # Use web_server broadcast hook if available (server/cloud mode)
    if _open_url_hook is not None:
        try:
            _open_url_hook(url)
            return f"Opening {label} in browser."
        except Exception as e:
            return f"Error broadcasting open_url: {e}"

    # Fallback: xdg-open for local mode
    try:
        env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0.0")}
        subprocess.Popen(
            ["xdg-open", url], start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        )
        return f"Opening {label}."
    except Exception as e:
        return f"Error opening URL: {e}"


def _exec_semantic_recall(args: dict) -> str:
    """Query JARVIS NeuralLattice + associative memory layers."""
    query = args.get("query", "").strip()
    k = min(int(args.get("k", 5)), 20)
    domain = args.get("domain", "")
    if not query:
        return "No query provided."
    try:
        from src.memory.store import MemoryStore
        store = MemoryStore()
        if domain:
            nodes = store.recall_domain(domain, top_k=k)
            query_nodes = store.recall(query, top_k=k)
            seen = {n.id for n in nodes}
            for n in query_nodes:
                if n.id not in seen:
                    nodes.append(n)
                    seen.add(n.id)
            nodes = nodes[:k]
        else:
            nodes = store.recall(query, top_k=k)
        if not nodes:
            return "No memories found for that query."
        lines = [f"[{i+1}] (strength={getattr(n,'strength',0):.2f}) {n.content}" for i, n in enumerate(nodes)]
        return "\n".join(lines)
    except Exception as e:
        return f"semantic_recall error: {e}"


def _exec_reflect(args: dict) -> str:
    """Self-critique heuristic — no LLM call to avoid recursion."""
    import json as _json
    import re as _re
    output = args.get("output", "").strip()
    task = args.get("task", "").strip()
    focus = args.get("focus", "correctness")
    if not output or not task:
        return "Both 'output' and 'task' are required."

    issues = []
    suggestions = []
    out_lower = output.lower()
    task_lower = task.lower()

    # ── Universal checks (all focus modes) ────────────────────────────
    if len(output) < 20:
        issues.append("Output is very short — may be incomplete")
        suggestions.append("Expand the response to fully address the task")

    # Leftover placeholders
    _PLACEHOLDERS = ["todo", "fixme", "xxx", "placeholder", "your code here",
                     "insert here", "...", "pass  #", "raise notimplementederror"]
    for p in _PLACEHOLDERS:
        if p in out_lower:
            issues.append(f"Unresolved placeholder or stub detected: '{p}'")
            suggestions.append("Replace all placeholders with real implementations")
            break

    # ── Correctness ────────────────────────────────────────────────────
    if focus == "correctness":
        if any(kw in task_lower for kw in ["fix", "debug", "implement", "write", "create", "build"]):
            if "```" not in output and "def " not in output and "class " not in output:
                issues.append("Task requires code but no code block was produced")
                suggestions.append("Include actual code, not just a description")
            if "error" in task_lower and "try" not in out_lower and "except" not in out_lower:
                issues.append("Error-handling task may be missing exception handling")
        # Check for common Python mistakes in code blocks
        if "```python" in output or "def " in output:
            if "print(" in output and "return" not in output and "def " in output:
                issues.append("Function uses print() but has no return statement — likely should return a value")
            if "except:" in output and "except Exception" not in output:
                issues.append("Bare 'except:' catches everything including KeyboardInterrupt — use 'except Exception:'")

    # ── Safety ─────────────────────────────────────────────────────────
    elif focus == "safety":
        _DANGEROUS = [
            ("rm -rf", "recursive delete"),
            ("drop table", "SQL table drop"),
            ("delete from", "SQL delete"),
            ("truncate", "SQL truncate"),
            ("os.remove", "file deletion"),
            ("shutil.rmtree", "directory tree removal"),
            ("subprocess.call.*shell=true", "shell injection risk"),
            ("eval(", "arbitrary code execution"),
            ("exec(", "arbitrary code execution"),
            ("pickle.loads", "unsafe deserialization"),
        ]
        for pattern, label in _DANGEROUS:
            if _re.search(pattern, out_lower):
                issues.append(f"Potentially destructive: {label} ({pattern.split('(')[0]}) — confirm intent")
        if not issues:
            suggestions.append("No obviously dangerous operations detected")

    # ── Completeness ───────────────────────────────────────────────────
    elif focus == "completeness":
        # Keyword coverage
        _STOP = {"a", "an", "the", "is", "are", "was", "were", "to", "for", "of", "in", "on", "and", "or"}
        task_words = {w for w in _re.findall(r'\w+', task_lower) if w not in _STOP and len(w) > 3}
        output_words = {w for w in _re.findall(r'\w+', out_lower)}
        missed = task_words - output_words
        coverage = 1.0 - len(missed) / max(len(task_words), 1)
        if coverage < 0.5:
            issues.append(f"Output covers only ~{int(coverage*100)}% of task keywords — missing: {', '.join(list(missed)[:6])}")
            suggestions.append("Revisit the task and address all specified requirements")
        # Check for numbered list tasks
        if _re.search(r'\b[1-9]\.\s', task) and not _re.search(r'\b[1-9]\.\s', output):
            issues.append("Task has multiple numbered items but output doesn't appear to address each one")
            suggestions.append("Structure the response to address each numbered item explicitly")

    # ── Efficiency ─────────────────────────────────────────────────────
    elif focus == "efficiency":
        code_lines = [l for l in output.splitlines() if l.strip() and not l.strip().startswith("#")]
        # Nested loop detection
        indent_levels = []
        in_loop = False
        for line in code_lines:
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if stripped.startswith(("for ", "while ")):
                if in_loop:
                    issues.append("Nested loop detected — consider if O(n²) complexity is necessary")
                    suggestions.append("Look for a single-pass or hash-based approach")
                    break
                in_loop = True
                indent_levels = [indent]
            elif in_loop and indent <= (indent_levels[0] if indent_levels else 0):
                in_loop = False
                indent_levels = []
        # String concatenation in loop
        if _re.search(r'(for |while ).*\n.*\+=.*["\']', output):
            issues.append("String concatenation inside loop — use list + ''.join() instead")
            suggestions.append("Collect parts in a list and join after the loop")
        # Repeated attribute lookup in loop
        if _re.search(r'(for |while )[\s\S]{0,200}\.split\(|\.lower\(|\.strip\(', output):
            pass  # Common, not necessarily a problem
        if not issues:
            suggestions.append("No obvious efficiency issues detected")

    # ── Clarity ────────────────────────────────────────────────────────
    elif focus == "clarity":
        sentences = _re.split(r'(?<=[.!?])\s+', output)
        long_sentences = [s for s in sentences if len(s.split()) > 40]
        if long_sentences:
            issues.append(f"{len(long_sentences)} sentence(s) exceed 40 words — consider breaking them up")
            suggestions.append("Split long sentences into shorter, focused statements")
        # Jargon without explanation
        _JARGON = ["asynchronous", "idempotent", "polymorphism", "coroutine", "closure", "monadic"]
        used_jargon = [j for j in _JARGON if j in out_lower]
        if len(used_jargon) >= 3:
            issues.append(f"Dense technical jargon: {', '.join(used_jargon[:4])} — ensure the audience will follow")
        # Missing code comments
        if ("def " in output or "class " in output) and "#" not in output:
            issues.append("Code has no comments — complex logic may be hard to follow")
            suggestions.append("Add inline comments for non-obvious logic")
        if not issues:
            suggestions.append("Output appears clear and readable")

    result = {
        "focus": focus,
        "issues_found": len(issues),
        "issues": issues if issues else ["No obvious issues detected"],
        "suggestions": suggestions if suggestions else ["Output looks reasonable — proceed"],
        "verdict": "REVISE" if issues else "PROCEED",
    }
    return _json.dumps(result, indent=2)


def _exec_get_diagnostics(args: dict) -> str:
    """Run static analysis on a file or directory, with trend tracking."""
    import subprocess, json as _json, re as _re, datetime as _dt
    path = args.get("path", "").strip()
    tool = args.get("tool", "auto")
    max_errors = min(int(args.get("max_errors", 20)), 50)
    show_trend = args.get("trend", False)

    if not path:
        return "No path provided."
    if not os.path.exists(path):
        return f"Path not found: {path}"

    if tool == "auto":
        if path.endswith((".js", ".ts", ".tsx", ".jsx")):
            tool = "eslint"
        elif path.endswith(".sh"):
            tool = "shellcheck"
        else:
            tool = "flake8"

    errors = []

    try:
        if tool == "flake8":
            result = subprocess.run(
                ["flake8", "--format=%(path)s:%(row)d:%(col)d: %(code)s %(text)s",
                 "--max-line-length=120", path],
                capture_output=True, text=True, timeout=30
            )
            for line in (result.stdout + result.stderr).splitlines():
                m = _re.match(r"(.+?):(\d+):(\d+):\s*([A-Z]\d+)\s+(.+)", line)
                if m:
                    errors.append({
                        "file": m.group(1), "line": int(m.group(2)), "col": int(m.group(3)),
                        "severity": "warning" if m.group(4).startswith("W") else "error",
                        "code": m.group(4), "message": m.group(5).strip(),
                    })

        elif tool == "mypy":
            result = subprocess.run(
                ["mypy", "--no-error-summary", "--show-column-numbers", path],
                capture_output=True, text=True, timeout=60
            )
            for line in result.stdout.splitlines():
                m = _re.match(r"(.+?):(\d+):(\d+):\s*(error|warning|note):\s+(.+)", line)
                if m:
                    errors.append({
                        "file": m.group(1), "line": int(m.group(2)), "col": int(m.group(3)),
                        "severity": m.group(4), "code": "mypy", "message": m.group(5).strip(),
                    })

        elif tool == "shellcheck":
            result = subprocess.run(
                ["shellcheck", "--format=json", path],
                capture_output=True, text=True, timeout=30
            )
            try:
                raw = _json.loads(result.stdout)
                for item in raw:
                    errors.append({
                        "file": item.get("file", path), "line": item.get("line", 0),
                        "col": item.get("column", 0),
                        "severity": item.get("level", "warning"),
                        "code": f"SC{item.get('code', 0)}", "message": item.get("message", ""),
                    })
            except _json.JSONDecodeError:
                pass

        elif tool == "eslint":
            result = subprocess.run(
                ["eslint", "--format=json", path],
                capture_output=True, text=True, timeout=30
            )
            try:
                raw = _json.loads(result.stdout)
                for file_result in raw:
                    for msg in file_result.get("messages", []):
                        errors.append({
                            "file": file_result.get("filePath", path),
                            "line": msg.get("line", 0), "col": msg.get("column", 0),
                            "severity": "error" if msg.get("severity") == 2 else "warning",
                            "code": msg.get("ruleId", "eslint"), "message": msg.get("message", ""),
                        })
            except _json.JSONDecodeError:
                pass

    except FileNotFoundError:
        return f"Tool '{tool}' not found. Install it: pip install {tool} (or npm install -g eslint)"
    except subprocess.TimeoutExpired:
        return f"Diagnostics timed out for {path}"

    errors = errors[:max_errors]
    status = "clean" if not errors else "issues_found"

    # Persist to diagnostics.jsonl for trend tracking
    try:
        from src.config import JARVIS_HOME
        diag_file = JARVIS_HOME / "diagnostics.jsonl"
        JARVIS_HOME.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": _dt.datetime.utcnow().isoformat() + "Z",
            "path": path,
            "tool": tool,
            "error_count": len(errors),
            "status": status,
        }
        with open(diag_file, "a", encoding="utf-8") as _df:
            _df.write(_json.dumps(record) + "\n")
    except Exception:
        pass

    result = {
        "status": status,
        "tool": tool,
        "path": path,
        "error_count": len(errors),
        "errors": errors,
    }

    # Include trend data if requested
    if show_trend:
        try:
            from src.config import JARVIS_HOME
            diag_file = JARVIS_HOME / "diagnostics.jsonl"
            if diag_file.exists():
                history = []
                with open(diag_file, encoding="utf-8") as _df:
                    for line in _df:
                        try:
                            rec = _json.loads(line)
                            if rec.get("path") == path and rec.get("tool") == tool:
                                history.append(rec)
                        except Exception:
                            pass
                # Last 10 runs
                history = history[-10:]
                if len(history) >= 2:
                    prev_count = history[-2]["error_count"]
                    curr_count = history[-1]["error_count"]
                    delta = curr_count - prev_count
                    result["trend"] = {
                        "history": [{"timestamp": r["timestamp"], "error_count": r["error_count"]} for r in history],
                        "delta": delta,
                        "direction": "improving" if delta < 0 else ("worsening" if delta > 0 else "stable"),
                    }
        except Exception:
            pass

    return _json.dumps(result, indent=2)


def _exec_diff_files(args: dict) -> str:
    """Return a diff between two files, text strings, or directories."""
    import difflib, shutil as _shutil, subprocess as _sp
    path_a = args.get("path_a", "").strip()
    path_b = args.get("path_b", "").strip()
    text_a = args.get("text_a", "")
    text_b = args.get("text_b", "")
    context = min(int(args.get("context_lines", 3)), 20)
    label_a = args.get("label_a", path_a or "a")
    label_b = args.get("label_b", path_b or "b")
    mode = args.get("mode", "unified")
    dir_diff = args.get("dir_diff", False)

    # ── Directory diff ─────────────────────────────────────────────────────
    if dir_diff and path_a and path_b:
        if not os.path.isdir(path_a):
            return f"Not a directory: {path_a}"
        if not os.path.isdir(path_b):
            return f"Not a directory: {path_b}"
        # Prefer git diff --no-index for pretty output
        if _shutil.which("git"):
            try:
                r = _sp.run(
                    ["git", "diff", "--no-index", "--stat" if mode == "stat" else "--unified=" + str(context),
                     path_a, path_b],
                    capture_output=True, text=True, timeout=30,
                )
                out = r.stdout or r.stderr or "No differences found."
                if len(out) > 16000:
                    out = out[:16000] + "\n... (diff truncated)"
                return out
            except Exception:
                pass
        # Fallback: manual file-by-file diff
        import glob as _g
        files_a = {os.path.relpath(f, path_a) for f in _g.glob(os.path.join(path_a, "**"), recursive=True) if os.path.isfile(f)}
        files_b = {os.path.relpath(f, path_b) for f in _g.glob(os.path.join(path_b, "**"), recursive=True) if os.path.isfile(f)}
        all_files = sorted(files_a | files_b)
        parts = []
        for rel in all_files:
            fa = os.path.join(path_a, rel)
            fb = os.path.join(path_b, rel)
            la = open(fa).readlines() if os.path.exists(fa) else []
            lb = open(fb).readlines() if os.path.exists(fb) else []
            diff = list(difflib.unified_diff(la, lb, fromfile=f"a/{rel}", tofile=f"b/{rel}", n=context))
            if diff:
                parts.append("".join(diff))
        result = "\n".join(parts) or "Directories are identical."
        if len(result) > 16000:
            result = result[:16000] + "\n... (diff truncated)"
        return result

    # ── File/text diff ─────────────────────────────────────────────────────
    # Try git diff --no-index for file-to-file comparisons (pretty colors, real paths)
    if path_a and path_b and _shutil.which("git") and mode in ("unified", "stat"):
        if not os.path.exists(path_a):
            return f"File not found: {path_a}"
        if not os.path.exists(path_b):
            return f"File not found: {path_b}"
        try:
            git_args = ["git", "diff", "--no-index"]
            if mode == "stat":
                git_args.append("--stat")
            else:
                git_args.append(f"--unified={context}")
            git_args += [path_a, path_b]
            r = _sp.run(git_args, capture_output=True, text=True, timeout=30)
            out = r.stdout
            if not out and r.returncode == 0:
                return "Files are identical — no differences found."
            if len(out) > 16000:
                out = out[:16000] + "\n... (diff truncated)"
            return out
        except Exception:
            pass  # fall through to Python difflib

    lines_a, lines_b = [], []

    if path_a:
        if not os.path.exists(path_a):
            return f"File not found: {path_a}"
        try:
            with open(path_a, encoding="utf-8", errors="replace") as f:
                lines_a = f.readlines()
        except Exception as e:
            return f"Cannot read {path_a}: {e}"
    elif text_a:
        lines_a = [l + "\n" for l in text_a.splitlines()]

    if path_b:
        if not os.path.exists(path_b):
            return f"File not found: {path_b}"
        try:
            with open(path_b, encoding="utf-8", errors="replace") as f:
                lines_b = f.readlines()
        except Exception as e:
            return f"Cannot read {path_b}: {e}"
    elif text_b:
        lines_b = [l + "\n" for l in text_b.splitlines()]

    if not lines_a and not lines_b:
        return "No input provided. Use path_a/path_b or text_a/text_b."

    if mode == "stat":
        # Summary: insertions/deletions count
        diff = list(difflib.unified_diff(lines_a, lines_b, fromfile=label_a, tofile=label_b, n=0))
        insertions = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
        deletions = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
        if not diff:
            return "Files are identical — no differences found."
        return f"{label_a} → {label_b}\n+{insertions} insertions, -{deletions} deletions"

    elif mode == "side_by_side":
        diff = list(difflib.ndiff(lines_a, lines_b))
        width = 60
        out_lines = []
        for line in diff[:200]:
            tag = line[:2]
            content = line[2:].rstrip("\n")
            if tag == "  ":
                out_lines.append(f"  {content:<{width}}  {content}")
            elif tag == "- ":
                out_lines.append(f"< {content:<{width}}  {'':}")
            elif tag == "+ ":
                out_lines.append(f"  {'':.<{width}}  > {content}")
        result = "\n".join(out_lines)
        if not result:
            return "Files are identical — no differences found."
        if len(result) > 16000:
            result = result[:16000] + "\n... (diff truncated)"
        return result

    else:  # unified (default)
        diff = list(difflib.unified_diff(
            lines_a, lines_b,
            fromfile=label_a, tofile=label_b,
            n=context,
        ))
        if not diff:
            return "Files are identical — no differences found."
        result = "".join(diff)
        if len(result) > 16000:
            result = result[:16000] + "\n... (diff truncated)"
        return result


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
            # Use rich read-only validation from BashTool module
            ro_error = _validate_read_only(cmd)
            if ro_error:
                # Also check our simple prefix allowlist as fallback
                if not any(cmd.startswith(p) for p in READONLY_BASH_PREFIXES):
                    return f"BLOCKED: {ro_error} Only read-only commands are permitted in plan mode."

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
        elif name == "glob":
            return _exec_glob(args)
        elif name == "grep":
            return _exec_grep(args)
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
        elif name == "open_url":
            return _exec_open_url(args)
        elif name == "switch_channel":
            return _exec_switch_channel(args)
        elif name == "web_api":
            return _exec_web_api(args)
        elif name == "view_screen":
            return _exec_view_screen(args)
        elif name == "see":
            return _exec_see(args)
        elif name == "tool_search":
            return _exec_tool_search(args)
        elif name == "think":
            return args.get("thought", "")
        elif name == "todo_write":
            return _exec_todo_write(args)
        elif name == "ask_user":
            return "__ASK_USER__"  # Handled by agent loop (needs async user input)
        elif name == "notebook_edit":
            return _exec_notebook_edit(args)
        elif name == "ssh_exec":
            return _exec_ssh(args)
        elif name == "dispatch":
            return "__DISPATCH__"  # Handled async by agent loop
        # ── Plan Mode ──────────────────────────────────────────────────
        elif name == "enter_plan_mode":
            return "__PLAN_MODE_ENTER__"  # Handled by agent loop
        elif name == "exit_plan_mode":
            return "__PLAN_MODE_EXIT__"  # Handled by agent loop
        # ── Worktree ───────────────────────────────────────────────────
        elif name == "enter_worktree":
            return "__WORKTREE_ENTER__"  # Handled by agent loop
        elif name == "exit_worktree":
            return "__WORKTREE_EXIT__"  # Handled by agent loop
        # ── Multi-Agent ────────────────────────────────────────────────
        elif name == "send_message":
            return "__SEND_MESSAGE__"  # Handled by agent loop
        # ── Task Management ────────────────────────────────────────────
        elif name == "task_create":
            return _exec_task_create(args)
        elif name == "task_get":
            return _exec_task_get(args)
        elif name == "task_list":
            return _exec_task_list(args)
        elif name == "task_stop":
            return _exec_task_stop(args)
        elif name == "task_update":
            return _exec_task_update(args)
        elif name == "task_output":
            return _exec_task_output(args)
        # ── Team Tools ─────────────────────────────────────────────────
        elif name == "team_create":
            return "__TEAM_CREATE__"  # Handled by agent loop
        elif name == "team_delete":
            return "__TEAM_DELETE__"  # Handled by agent loop
        # ── Skill ──────────────────────────────────────────────────────
        elif name == "skill":
            return "__SKILL__"  # Handled by agent loop
        # ── Config ─────────────────────────────────────────────────────
        elif name == "config":
            return _exec_config(args)
        # ── LSP ────────────────────────────────────────────────────────
        elif name == "lsp":
            return "__LSP__"  # Handled by agent loop (requires LSP server)
        # ── Sleep ──────────────────────────────────────────────────────
        elif name == "sleep":
            return _exec_sleep(args)
        # ── Cron/Schedule ──────────────────────────────────────────────
        elif name == "schedule_cron":
            return "__CRON__"  # Handled by agent loop
        # ── BriefTool (SendUserMessage) ────────────────────────────────
        elif name == "brief":
            return args.get("message", "")
        # ── MCP Resources ─────────────────────────────────────────────
        elif name == "list_mcp_resources":
            return _exec_list_mcp_resources(args)
        # ── Remote Trigger ─────────────────────────────────────────────
        elif name == "remote_trigger":
            return "__REMOTE_TRIGGER__"  # Handled by agent loop
        elif name == "send_sms":
            return _exec_send_sms(args)
        elif name == "network_scan":
            return _exec_network_scan(args)
        elif name == "sysinfo":
            return _exec_sysinfo(args)
        elif name == "container":
            return _exec_container(args)
        elif name == "security_scan":
            return _exec_security_scan(args)
        elif name == "rag_search":
            return _exec_rag_search(args)
        elif name == "semantic_recall":
            return _exec_semantic_recall(args)
        elif name == "reflect":
            return _exec_reflect(args)
        elif name == "get_diagnostics":
            return _exec_get_diagnostics(args)
        elif name == "diff_files":
            return _exec_diff_files(args)
        elif name == "browser":
            return _exec_browser(args)
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
        detail = args.get("detail", "full")
        structured = args.get("structured", False)

        if structured:
            import json as _json
            data = {
                "active_window": ctx.active_window or None,
                "application": ctx.window_class or None,
                "screen_text": ctx.screen_text.strip() if ctx.screen_text else None,
            }
            return _json.dumps(data, indent=2)

        parts = []
        if ctx.active_window:
            parts.append(f"Active window: {ctx.active_window}")
        if ctx.window_class:
            parts.append(f"Application: {ctx.window_class}")
        if detail == "full" and ctx.screen_text:
            # No arbitrary line cap — return all meaningful text, capped at 8K chars
            text = "\n".join(l for l in ctx.screen_text.strip().split("\n") if l.strip())
            if len(text) > 8000:
                text = text[:8000] + "\n... (screen text truncated)"
            parts.append(f"Visible text on screen:\n{text}")
        if not parts:
            return "Could not capture screen. Display may not be accessible."
        return "\n".join(parts)
    except Exception as e:
        return f"Screen capture failed: {e}"


def _exec_see(args: dict) -> str:
    """Look through the webcam — returns a description of what JARVIS sees."""
    prompt = args.get("prompt", "Describe what you see in detail.")
    import time as _time

    frame_b64 = None
    source = ""

    # 1. Try latest WebSocket frame (from desktop/browser camera stream)
    try:
        from src.server import _latest_camera_frame
        if _latest_camera_frame.get("frame") and _time.time() - _latest_camera_frame.get("timestamp", 0) < 10:
            frame_b64 = _latest_camera_frame["frame"]
            source = "camera stream"
    except ImportError:
        pass

    # 2. Try direct webcam capture via OpenCV (RGB + IR for face queries)
    if not frame_b64:
        try:
            from src.vision.camera import capture_to_base64, is_camera_available, has_ir_camera, IR_CAMERA, RGB_CAMERA
            # Use IR camera for face-related queries
            _face_words = ["face", "who", "person", "identity", "recognize", "look at me"]
            _use_ir = has_ir_camera() and any(w in prompt.lower() for w in _face_words)
            cam_id = IR_CAMERA if _use_ir else RGB_CAMERA
            if is_camera_available(cam_id):
                frame_b64 = capture_to_base64(cam_id)
                source = "IR camera" if _use_ir else "webcam"
            elif is_camera_available(RGB_CAMERA):
                frame_b64 = capture_to_base64(RGB_CAMERA)
                source = "webcam"
        except Exception:
            pass

    # 3. Try screen capture as last resort
    if not frame_b64:
        try:
            import mss, base64, io
            from PIL import Image
            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[1])
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                img.thumbnail((1024, 768))
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=70)
                frame_b64 = base64.b64encode(buf.getvalue()).decode()
                source = "screen capture"
        except Exception:
            pass

    if not frame_b64:
        return "No camera or screen available. Say 'turn on camera' to start the webcam."

    # Send to vision-capable model
    try:
        from src.reasoning.providers import ProviderRegistry
        import asyncio

        registry = ProviderRegistry()
        full_prompt = f"{prompt}\n(Source: {source})"

        # Run async vision query from sync context
        async def _query():
            return await registry.query_vision(frame_b64, full_prompt)

        try:
            loop = asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result, provider = pool.submit(lambda: asyncio.run(_query())).result(timeout=30)
        except RuntimeError:
            result, provider = asyncio.run(_query())

        if result:
            return f"[{source}] {result}"
        return f"Vision model returned no description. (source: {source})"
    except Exception as e:
        # Fall back to local CV analysis
        try:
            import base64
            from src.vision.describe import analyze_image, describe_analysis
            img_data = base64.b64decode(frame_b64)
            tmp_path = "/tmp/jarvis_see_frame.jpg"
            with open(tmp_path, "wb") as f:
                f.write(img_data)
            analysis = analyze_image(tmp_path)
            return f"[{source}, local analysis] {describe_analysis(analysis)}"
        except Exception:
            return f"Vision failed: {e}"


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
    """Get environment variables needed for GUI/terminal apps.

    Auto-detects DISPLAY and DBUS_SESSION_BUS_ADDRESS when not set in the
    server process environment (e.g. when JARVIS runs as a systemd service).
    """
    display = os.environ.get("DISPLAY", "")
    if not display:
        # Find active X display from lock files (/tmp/.X<N>-lock)
        import glob as _g
        import re as _re
        locks = sorted(_g.glob("/tmp/.X*-lock"))
        if locks:
            m = _re.search(r"/tmp/\.X(\d+)-lock", locks[0])
            display = f":{m.group(1)}" if m else ":0"
        else:
            display = ":0"

    dbus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if not dbus:
        # Try systemd user session bus socket (most common on modern Linux)
        runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        bus_socket = os.path.join(runtime, "bus")
        if os.path.exists(bus_socket):
            dbus = f"unix:path={bus_socket}"

    return {
        **os.environ,
        "DISPLAY": display,
        "XAUTHORITY": os.environ.get("XAUTHORITY", os.path.expanduser("~/.Xauthority")),
        "DBUS_SESSION_BUS_ADDRESS": dbus,
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
    timeout = min(int(args.get("timeout", 60) or 60), 600)
    if not command:
        return "No command provided."

    _full_access = bool(os.environ.get("JARVIS_NO_SANDBOX"))

    if not _full_access:
        # ── Layer 1: blocked patterns (pipe-to-shell, dangerous rm) ──────
        security_error = validate_bash_security(command)
        if security_error:
            return f"BLOCKED: {security_error}"

        # ── Layer 2: sed safety ───────────────────────────────────────────
        cmd_first_word = command.strip().split()[0] if command.strip() else ""
        if cmd_first_word == "sed" or " sed " in command or command.strip().startswith("sed "):
            sed_result = _check_sed_constraints(command)
            if sed_result.behavior == "ask":
                return f"BLOCKED: {sed_result.message}"

        # ── Layer 3: sensitive path detection ────────────────────────────
        for token in command.split():
            if token.startswith("/") or token.startswith("~"):
                if _is_sensitive_path(token):
                    return f"BLOCKED: Command references sensitive path: {token}"

        # ── Layer 4: hard block on system-critical commands ──────────────
        _HARD_BLOCKED = {"shutdown", "reboot", "poweroff", "halt", "init"}
        _PROCESS_KILL = {"kill", "killall", "pkill", "xkill"}
        _cmd_words = command.strip().split()
        _cmd_base = _cmd_words[0].split("/")[-1] if _cmd_words else ""

        if _cmd_base in _HARD_BLOCKED or any(w in _HARD_BLOCKED for w in _cmd_words):
            return "BLOCKED: JARVIS cannot shutdown, reboot, or halt the system."

        if _cmd_base in _PROCESS_KILL:
            _PROTECTED_PROCESSES = {
                "code", "vscode", "code-oss",
                "Xorg", "Xwayland", "gnome-shell", "plasmashell", "kwin",
                "systemd", "init", "dbus", "pulseaudio", "pipewire",
                "sshd", "NetworkManager", "nm-applet",
                "gdm", "sddm", "lightdm",
            }
            target = " ".join(_cmd_words[1:]).lower()
            for proc in _PROTECTED_PROCESSES:
                if proc.lower() in target:
                    return f"BLOCKED: Cannot kill protected process '{proc}'."

    # ── Destructive warning (informational only, never blocks) ───────
    destructive_warning = _get_destructive_warning(command) if not _full_access else ""

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

    # Commands that need an interactive terminal — only route to terminal when sandboxed.
    # With JARVIS_NO_SANDBOX=1 (owner mode), run directly so output is captured inline.
    interactive_cmds = ["sudo apt", "apt update", "apt upgrade", "apt install",
                        "apt remove", "dpkg", "systemctl"]
    if any(command.strip().startswith(ic) or command.strip().startswith(f"echo 'toor' | {ic}")
           for ic in interactive_cmds):
        if os.environ.get("JARVIS_NO_SANDBOX"):
            pass  # fall through to direct execution below
        else:
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

    # Check if sandbox requested — disabled when running in web server or explicitly bypassed
    use_sandbox = (
        not args.get("dangerouslyDisableSandbox", False)
        and not os.environ.get("JARVIS_NO_SANDBOX")
    )

    if use_sandbox:
        try:
            config = SandboxConfig(enabled=True, timeout=timeout)
            result = execute_sandboxed(command, config, cwd=os.getcwd(), timeout=timeout)
            output = ""
            if result["stdout"]:
                output += result["stdout"]
            if result["stderr"]:
                output += ("\n" if output else "") + result["stderr"]
            sandboxed = result.get("sandboxed", False)
            # If sandbox produced no output AND failed, fall through to unsandboxed.
            # This handles cases where unshare/namespace setup silently fails.
            if not output and result["returncode"] != 0 and sandboxed:
                pass  # fall through
            else:
                if not output:
                    output = "(no output)"
                prefix = f"exit_code={result['returncode']}"
                if sandboxed:
                    prefix += " [sandboxed]"
                # Cap output
                if len(output) > MAX_OUTPUT_SIZE:
                    half = MAX_OUTPUT_SIZE // 2
                    quarter = MAX_OUTPUT_SIZE // 4
                    output = output[:half] + "\n\n... (truncated) ...\n\n" + output[-quarter:]
                # Semantic exit code interpretation
                sem = _interpret_bash_result(command, result["returncode"], result.get("stdout", ""), result.get("stderr", ""))
                if sem.message:
                    prefix += f" ({sem.message})"
                if destructive_warning:
                    prefix += f"\n{destructive_warning}"
                return f"{prefix}\n{output}"
        except Exception:
            pass  # Fall through to unsandboxed execution

    # Original unsandboxed execution (fallback)
    # Full root access: wrap with sudo when NO_SANDBOX=1 and not already root/sudo
    _cmd_to_run = command
    _sudo_available = __import__('shutil').which("sudo") is not None
    if (os.environ.get("JARVIS_NO_SANDBOX")
            and _sudo_available
            and os.geteuid() != 0
            and not command.strip().startswith("sudo")):
        _cmd_to_run = f"sudo -E -n sh -c {__import__('shlex').quote(command)}"

    # Scrub secrets from subprocess environment
    _env = _get_display_env()
    _SECRET_KEYS = {
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY",
        "BRAVE_SEARCH_API_KEY", "TWILIO_AUTH_TOKEN", "TWILIO_ACCOUNT_SID",
        "JARVIS_VAULT_KEY", "GITHUB_TOKEN", "GITLAB_TOKEN",
    }
    for _k in _SECRET_KEYS:
        _env.pop(_k, None)

    try:
        proc = subprocess.Popen(
            _cmd_to_run, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=os.getcwd(), env=_env,
            start_new_session=True,
        )
        try:
            _stdout_b, _stderr_b = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # SIGTERM first, then SIGKILL after 5s grace period
            try:
                import signal as _signal
                os.killpg(os.getpgid(proc.pid), _signal.SIGTERM)
            except Exception:
                proc.terminate()
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), _signal.SIGKILL)
                except Exception:
                    proc.kill()
                proc.communicate()
            return f"exit_code=TIMEOUT\nCommand timed out after {timeout}s (killed): {command}"
        stdout = _stdout_b.decode("utf-8", errors="replace")
        stderr = _stderr_b.decode("utf-8", errors="replace")
        exit_code = proc.returncode
    except Exception as _e:
        return f"Error: {_e}"
    output = ""
    if stdout:
        output += stdout
    if stderr:
        output += ("\n" if output else "") + stderr
    if not output:
        output = "(no output)"
    if len(output) > MAX_OUTPUT_SIZE:
        half = MAX_OUTPUT_SIZE // 2
        output = output[:half] + "\n\n... (truncated) ...\n\n" + output[-(half // 2):]
    sem = _interpret_bash_result(command, exit_code, stdout, stderr)
    prefix = f"exit_code={exit_code}"
    if sem.message:
        prefix += f" ({sem.message})"
    if destructive_warning:
        prefix += f"\n{destructive_warning}"
    return f"{prefix}\n{output}"


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


def _exec_ssh(args: dict) -> str:
    """Execute a command on a remote host.

    If the named host has a manage_url + manage_token configured, uses the
    JARVIS management HTTP API (no SSH required). Otherwise falls back to
    paramiko SSH.
    """
    import json as _json

    host_ref = args.get("host", "").strip()
    command = args.get("command", "").strip()
    timeout = int(args.get("timeout", 30))

    if not host_ref or not command:
        return "Error: host and command are required"

    # Load named hosts config
    hosts_file = os.path.expanduser("~/.jarvis/ssh_hosts.json")
    hosts: dict = {}
    if os.path.exists(hosts_file):
        try:
            with open(hosts_file) as _f:
                hosts = _json.load(_f)
        except Exception as e:
            return f"Error reading ~/.jarvis/ssh_hosts.json: {e}"

    cfg = hosts.get(host_ref, {})

    # ── Management API path (preferred, no SSH needed) ──────────────
    manage_url = cfg.get("manage_url")
    manage_token = cfg.get("manage_token")
    if manage_url and manage_token:
        try:
            import urllib.request as _ur
            # Map common intent words to manage actions
            _lc = command.lower().strip()
            if _lc in ("restart", "reload"):
                payload = {"action": "restart"}
            elif _lc in ("status", "ping"):
                payload = {"action": "status"}
            elif _lc.startswith("pull"):
                payload = {"action": "pull_restart" if "restart" in _lc else "pull"}
            else:
                payload = {"action": "exec", "command": command, "timeout": timeout}

            data = _json.dumps(payload).encode()
            req = _ur.Request(
                manage_url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {manage_token}",
                },
                method="POST",
            )
            with _ur.urlopen(req, timeout=timeout) as resp:
                body = _json.loads(resp.read().decode())

            if payload["action"] == "status":
                return (
                    f"[{host_ref}] JARVIS is running\n"
                    f"  PID: {body.get('pid')}  host: {body.get('host')}\n"
                    f"  clients: {body.get('clients')}  brain: {body.get('brain')}"
                )
            if payload["action"] in ("restart", "pull_restart"):
                return f"[{host_ref}] {body.get('msg', body)}"
            if payload["action"] == "pull":
                out = body.get("stdout", "").strip()
                err = body.get("stderr", "").strip()
                return f"[{host_ref}] git pull\n{out}" + (f"\n{err}" if err else "")
            # exec
            out = body.get("stdout", "").strip()
            err = body.get("stderr", "").strip()
            rc = body.get("returncode", 0)
            result = f"[{host_ref}] $ {command}\n"
            if out:
                result += out
            if err:
                result += ("\n" if out else "") + f"STDERR:\n{err}"
            if rc != 0:
                result += f"\n[exit {rc}]"
            return result.strip()
        except Exception as e:
            return f"Manage API error ({host_ref}): {e}"

    # ── SSH fallback ─────────────────────────────────────────────────
    try:
        import paramiko
    except ImportError:
        return "Error: paramiko not installed. Run: pip install paramiko"

    if host_ref in hosts:
        hostname = cfg.get("host", host_ref)
        username = cfg.get("user", "root")
        port = int(cfg.get("port", 22))
        key_path = cfg.get("key")
        password = cfg.get("password")
    elif "@" in host_ref:
        username, hostname = host_ref.split("@", 1)
        port = 22
        key_path = None
        password = None
    else:
        hostname = host_ref
        username = "root"
        port = 22
        key_path = None
        password = None

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs: dict = {
        "hostname": hostname,
        "port": port,
        "username": username,
        "timeout": timeout,
    }
    if key_path:
        connect_kwargs["key_filename"] = os.path.expanduser(key_path)
    elif password:
        connect_kwargs["password"] = password

    try:
        client.connect(**connect_kwargs)

        import threading as _threading

        out_buf: list[str] = []
        err_buf: list[str] = []
        exit_buf: list[int] = [0]
        done_event = _threading.Event()

        def _run_cmd():
            try:
                _, stdout, stderr = client.exec_command(command)
                out_buf.append(stdout.read().decode("utf-8", errors="replace"))
                err_buf.append(stderr.read().decode("utf-8", errors="replace"))
                exit_buf[0] = stdout.channel.recv_exit_status()
            finally:
                done_event.set()

        t = _threading.Thread(target=_run_cmd, daemon=True)
        t.start()
        finished = done_event.wait(timeout=timeout)
        client.close()

        if not finished:
            return f"SSH command timed out after {timeout}s: {command}"

        out = out_buf[0] if out_buf else ""
        err = err_buf[0] if err_buf else ""
        exit_code = exit_buf[0]

        result = f"[{username}@{hostname}] $ {command}\n"
        if out:
            result += out
        if err:
            result += ("\n" if out else "") + f"STDERR:\n{err}"
        if exit_code != 0:
            result += f"\n[exit {exit_code}]"
        return result.strip()
    except Exception as e:
        try:
            client.close()
        except Exception:
            pass
        return f"SSH error ({username}@{hostname}): {e}"


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

    # Boundary/symlink guard: block symlinks whose *resolved* target escapes
    # ALLOWED_ROOTS.  Regular files outside ALLOWED_ROOTS are still readable
    # (user explicitly provided the path); only symlink indirection is blocked.
    _abs_path = os.path.abspath(path)  # normalises .. but does NOT follow symlinks
    if _abs_path != resolved:          # path is a symlink (target differs)
        _in_allowed = any(
            resolved == os.path.realpath(r)
            or resolved.startswith(os.path.realpath(r) + os.sep)
            for r in ALLOWED_ROOTS
        )
        if not _in_allowed:
            return (
                f"Access denied: symlink {path!r} resolves to {resolved!r} "
                "which is outside the allowed root paths."
            )

    ext = os.path.splitext(resolved)[1].lower()

    # Enforce per-category media size limits (images/audio/video/document)
    try:
        from src.media.mime import check_media_size, detect_mime
        _, _category = detect_mime(resolved)
        _size_err = check_media_size(resolved, _category)
        if _size_err and ext not in (".pdf",):
            return _size_err
    except Exception:
        pass  # media module not available — fall through to existing size check

    # Enforce file size limit from FileReadTool/limits.py
    try:
        file_size = os.path.getsize(resolved)
        if file_size > _FILE_READ_MAX_SIZE and ext not in (".pdf",) and ext not in IMAGE_EXTENSIONS:
            read_limits = _get_file_read_limits()
            return (
                f"File too large: {file_size:,} bytes exceeds limit of "
                f"{read_limits.max_size_bytes:,} bytes. Use offset/limit to read a portion, "
                f"or use bash with head/tail."
            )
    except OSError:
        pass  # stat failed, continue and let open() handle it

    # PDF support
    if ext == ".pdf":
        result = _read_pdf(resolved, offset, limit)
        _file_read_times[resolved] = os.path.getmtime(resolved)
        if len(result) > MAX_OUTPUT_SIZE:
            half = MAX_OUTPUT_SIZE // 2
            result = result[:half] + "\n\n... (truncated) ...\n\n" + result[-(half // 2):]
        return result

    # Image support (with optional resize via FileReadTool/imageProcessor)
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
        # 1. BOM detection (zero-cost, deterministic)
        encoding_used = "utf-8"
        with open(resolved, "rb") as _rb:
            _bom_bytes = _rb.read(4)
        if _bom_bytes[:3] == b"\xef\xbb\xbf":
            encoding_used = "utf-8-sig"
        elif _bom_bytes[:2] == b"\xff\xfe":
            encoding_used = "utf-16-le"
        elif _bom_bytes[:2] == b"\xfe\xff":
            encoding_used = "utf-16-be"
        else:
            # 2. Try strict UTF-8
            try:
                with open(resolved, "r", encoding="utf-8") as _tf:
                    _tf.read(8192)  # probe first 8KB
                encoding_used = "utf-8"
            except UnicodeDecodeError:
                # 3. charset-normalizer (transitive dep via requests)
                try:
                    from charset_normalizer import from_path as _cn_from_path
                    _cn_result = _cn_from_path(resolved, cp_isolation=["utf-8", "windows-1252", "latin-1", "shift-jis"])
                    _best = _cn_result.best()
                    encoding_used = str(_best.encoding) if _best else "latin-1"
                except Exception:
                    encoding_used = "latin-1"

        with open(resolved, "r", encoding=encoding_used, errors="replace") as f:
            lines = f.readlines()

        # Track read time for staleness detection
        _file_read_times[resolved] = os.path.getmtime(resolved)

        total = len(lines)
        start = max(0, ((offset or 1) - 1))
        end = min(total, start + (limit or 2000))
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
        # Resolve path first, then validate and create dirs using resolved path
        resolved = os.path.realpath(os.path.expanduser(path))
        valid, err = _validate_path(resolved, write=True)
        if not valid:
            return err
        os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
        extra_info = ""
        old_content: str | None = None

        # If file already exists, handle backup and line ending preservation
        if os.path.exists(resolved):
            # Read old content for diff generation
            try:
                with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                    old_content = f.read()
            except Exception:
                pass

            # Write backup to /tmp (not next to source — avoids polluting project)
            try:
                import tempfile as _tf2
                _bak_dir = os.path.join(_tf2.gettempdir(), "jarvis-backups")
                os.makedirs(_bak_dir, exist_ok=True)
                _bak_name = os.path.basename(resolved) + ".bak"
                _bak_path = os.path.join(_bak_dir, _bak_name)
                shutil.copy2(resolved, _bak_path)
                extra_info = f" (backup: {_bak_path})"
            except Exception:
                pass

            # Detect existing line endings and match them
            try:
                with open(resolved, "rb") as f:
                    raw = f.read(8192)
                if b"\r\n" in raw:
                    # File uses CRLF — convert content to match
                    content = content.replace("\r\n", "\n").replace("\n", "\r\n")
            except Exception:
                pass

        # Atomic write: temp file on same filesystem → fsync → os.replace
        import tempfile as _tf
        _dir = os.path.dirname(resolved) or "."
        _fd, _tmp = _tf.mkstemp(dir=_dir, prefix=".jarvis-write-")
        try:
            with os.fdopen(_fd, "w", encoding="utf-8") as _f:
                _f.write(content)
                _f.flush()
                os.fsync(_f.fileno())
            if os.path.exists(resolved):
                shutil.copystat(resolved, _tmp)  # preserve permissions + timestamps
            os.replace(_tmp, resolved)
        except Exception:
            try:
                os.unlink(_tmp)
            except Exception:
                pass
            raise

        # Track the write time for staleness detection
        _file_read_times[resolved] = os.path.getmtime(resolved)

        lines = content.count("\n") + 1
        result_msg = f"Wrote {lines} lines to {path}{extra_info}"

        # Append unified diff when overwriting an existing file
        if old_content is not None:
            old_lines = old_content.splitlines(keepends=True)
            new_lines = content.splitlines(keepends=True)
            diff = list(difflib.unified_diff(
                old_lines, new_lines,
                fromfile=f"a/{os.path.basename(path)}",
                tofile=f"b/{os.path.basename(path)}",
                n=3,
            ))
            if diff:
                diff_text = "".join(diff[:60])
                if len(diff) > 60:
                    diff_text += "\n... (diff truncated)"
                result_msg += f"\n\n{diff_text}"

        return result_msg
    except Exception as e:
        return f"Error writing {_sanitize_error_path(path)}: {e}"


def _exec_edit(args: dict) -> str:
    path = os.path.expanduser(args.get("path", ""))
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    replace_all = args.get("replace_all", False)

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

        # Use FileEditTool's find_actual_string for smart matching
        # (handles curly quote normalization transparently)
        actual_old = _find_actual_string(content, old_string)

        if actual_old is None:
            # Provide a helpful error: show close matches via difflib
            lines = content.splitlines()
            old_lines = old_string.splitlines()
            if old_lines:
                close = difflib.get_close_matches(old_lines[0], lines, n=3, cutoff=0.5)
                if close:
                    hint = "\n".join(f"  > {c}" for c in close)
                    return (
                        f"old_string not found in {_sanitize_error_path(path)}. "
                        f"Similar lines found:\n{hint}\n"
                        f"Read the file first to get the exact text."
                    )
            return f"old_string not found in {_sanitize_error_path(path)}. Read the file first to get the exact text."

        count = content.count(actual_old)
        if count > 1 and not replace_all:
            return f"old_string matches {count} locations. Provide more context to make it unique, or set replace_all=true."

        # Apply edit using FileEditTool's apply_edit_to_file (handles trailing newline stripping)
        new_content = _apply_edit_to_file(content, actual_old, new_string, replace_all)
        with open(path, "w") as f:
            f.write(new_content)

        # Update tracked mtime after successful edit
        _file_read_times[resolved] = os.path.getmtime(resolved)

        # Generate snippet showing context around the edit
        try:
            snippet_info = _get_edit_snippet(content, actual_old, new_string)
            snippet_text = f"  (around line {snippet_info['start_line']})\n{snippet_info['snippet']}"
        except Exception:
            snippet_text = ""

        # Generate unified diff for context
        old_lines = content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{os.path.basename(path)}",
            tofile=f"b/{os.path.basename(path)}",
            n=3,
        ))
        if diff:
            diff_text = "".join(diff[:50])
            if len(diff) > 50:
                diff_text += "\n... (diff truncated)"
            return f"Edited {path} successfully.\n\n{diff_text}"

        return f"Edited {path} successfully.{snippet_text}"
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
    confirm = bool(args.get("confirm", False))
    dry_run = bool(args.get("dry_run", False))

    if not query:
        return "No SQL query provided."
    if not database:
        return "No database path/connection string provided."

    # Block destructive operations on system databases
    db_lower = database.lower()
    if any(p in db_lower for p in ["/etc/", "/var/lib/", "/usr/", "system"]):
        return "BLOCKED: Cannot modify system databases."

    # Safety gate: require explicit confirmation for destructive operations
    _q_upper = query.strip().upper()
    _DESTRUCTIVE_PATTERNS = (
        "DROP TABLE", "DROP DATABASE", "DROP SCHEMA", "DROP INDEX", "DROP VIEW",
        "TRUNCATE", "DELETE FROM", "DELETE ",
    )
    _is_destructive = any(_q_upper.startswith(p) or f" {p}" in _q_upper for p in _DESTRUCTIVE_PATTERNS)
    # Allow safe DELETEs where it's in a subquery context (e.g. CREATE TABLE ... SELECT)
    if _is_destructive and not _q_upper.startswith("CREATE") and not _q_upper.startswith("INSERT"):
        if dry_run:
            return f"DRY RUN — would execute against {database}:\n{query}\n\n(No changes made — pass confirm=true to execute)"
        if not confirm:
            # Identify what would be affected
            _op = next((p for p in _DESTRUCTIVE_PATTERNS if _q_upper.startswith(p) or f" {p}" in _q_upper), "destructive operation")
            return (
                f"SAFETY BLOCK: '{_op}' is a destructive operation.\n"
                f"Query: {query[:120]}\n"
                f"Database: {database}\n\n"
                "Re-run with confirm=true to execute, or dry_run=true to preview without changes."
            )

    if dry_run:
        return f"DRY RUN — would execute against {database}:\n{query}\n\n(No changes made — pass confirm=true to execute)"

    try:
        if db_type == "sqlite":
            import sqlite3
            db_path = os.path.expanduser(database)
            conn = sqlite3.connect(db_path, timeout=10)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query)

                # SELECT queries return results
                if query.strip().upper().startswith("SELECT") or query.strip().upper().startswith("PRAGMA"):
                    rows = cursor.fetchmany(100)  # Cap at 100 rows
                    if not rows:
                        return "Query returned 0 rows."
                    # Format as table
                    columns = [d[0] for d in cursor.description]
                    lines = [" | ".join(columns)]
                    lines.append("-" * len(lines[0]))
                    for row in rows:
                        lines.append(" | ".join(str(v) for v in row))
                    if len(rows) >= 100:
                        try:
                            total = cursor.execute(f"SELECT COUNT(*) FROM ({query}) AS _c").fetchone()[0]
                        except Exception:
                            total = "100+"  # complex query (JOIN/CTE/GROUP BY) — approximate
                    else:
                        total = len(rows)
                    result = "\n".join(lines)
                    if len(rows) >= 100:
                        result += f"\n... showing 100 of {total} rows"
                    return result
                else:
                    # INSERT/UPDATE/DELETE/CREATE
                    conn.commit()
                    affected = cursor.rowcount
                    return f"OK. {affected} row(s) affected."
            finally:
                conn.close()

        elif db_type == "postgresql":
            try:
                import psycopg2
            except ImportError:
                return "PostgreSQL support requires: pip install psycopg2-binary"
            conn = psycopg2.connect(database)
            try:
                cursor = conn.cursor()
                cursor.execute(query)
                if cursor.description:
                    columns = [d[0] for d in cursor.description]
                    rows = cursor.fetchmany(100)
                    lines = [" | ".join(columns)]
                    lines.append("-" * len(lines[0]))
                    for row in rows:
                        lines.append(" | ".join(str(v) for v in row))
                    return "\n".join(lines)
                else:
                    conn.commit()
                    affected = cursor.rowcount
                    return f"OK. {affected} row(s) affected."
            finally:
                conn.close()

        elif db_type == "mysql":
            try:
                import mysql.connector
            except ImportError:
                return "MySQL support requires: pip install mysql-connector-python"
            import urllib.parse as _urlparse
            parsed = _urlparse.urlparse(database) if database.startswith("mysql://") else None
            if parsed and parsed.hostname:
                connect_kwargs: dict = {"host": parsed.hostname}
                if parsed.port:
                    connect_kwargs["port"] = parsed.port
                if parsed.username:
                    connect_kwargs["user"] = parsed.username
                if parsed.password:
                    connect_kwargs["password"] = parsed.password
                if parsed.path and parsed.path.lstrip("/"):
                    connect_kwargs["database"] = parsed.path.lstrip("/")
                conn = mysql.connector.connect(**connect_kwargs)
            else:
                conn = mysql.connector.connect(host=database)
            try:
                cursor = conn.cursor()
                cursor.execute(query)
                if cursor.description:
                    columns = [d[0] for d in cursor.description]
                    rows = cursor.fetchmany(100)
                    lines = [" | ".join(columns)]
                    lines.append("-" * len(lines[0]))
                    for row in rows:
                        lines.append(" | ".join(str(v) for v in row))
                    return "\n".join(lines)
                else:
                    conn.commit()
                    affected = cursor.rowcount
                    return f"OK. {affected} row(s) affected."
            finally:
                conn.close()
        else:
            return f"Unknown db_type: {db_type}. Use sqlite, postgresql, or mysql."

    except Exception as e:
        return f"Database error: {e}"


_PLATFORM_TOKEN_HELP = {
    "github":   "github.com → Settings → Developer settings → Personal access tokens → Fine-grained",
    "gitlab":   "gitlab.com → User Settings → Access Tokens",
    "slack":    "api.slack.com/apps → Your App → OAuth & Permissions → Bot Token",
    "discord":  "discord.com/developers/applications → Your App → Bot → Token",
    "jira":     "your-domain.atlassian.net → Account Settings → Security → API tokens",
    "notion":   "notion.so/my-integrations → New Integration → Secret",
    "openai":   "platform.openai.com/api-keys",
    "anthropic":"console.anthropic.com/keys",
    "groq":     "console.groq.com/keys",
    "linear":   "linear.app → Settings → API → Personal API keys",
    "vercel":   "vercel.com/account/tokens",
    "cloudflare": "dash.cloudflare.com/profile/api-tokens",
    "digitalocean": "cloud.digitalocean.com/account/api/tokens",
}


def _exec_web_api(args: dict) -> str:
    """Make authenticated HTTP API calls using stored tokens."""
    import urllib.request
    import urllib.error

    url = args.get("url", "")
    method = args.get("method", "GET").upper()
    platform = args.get("platform", "").lower()
    body = args.get("body", "")
    extra_headers = args.get("headers", "")

    # List configured platforms
    if platform == "list" or (not url and not platform):
        try:
            from src.vault.tokens import TokenVault
            vault = TokenVault()
            platforms = vault.list_platforms() if hasattr(vault, "list_platforms") else []
        except Exception:
            platforms = []
        if not platforms:
            return "No API tokens stored yet.\nAdd one with: /config vault store <platform> <token>"
        lines = ["Configured API platforms:"]
        for p in sorted(platforms):
            hint = _PLATFORM_TOKEN_HELP.get(p, "")
            lines.append(f"  {p}" + (f"  ({hint})" if hint else ""))
        return "\n".join(lines)

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
        hint = _PLATFORM_TOKEN_HELP.get(platform, "")
        token_url = f"\nGet your token at: {hint}" if hint else ""
        return (
            f"No token stored for '{platform}'.{token_url}\n\n"
            f"Store it with:\n"
            f"  /config vault store {platform} <token>\n"
            f"For Jira (needs email too):\n"
            f"  /config vault store jira <token> --email you@example.com"
        )

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
        err_body = ""
        try:
            err_body = e.read().decode()[:500]
        except Exception:
            pass
        _HTTP_HINTS = {
            401: "Token is invalid or expired — re-run /config vault store to update it.",
            403: "Token lacks required permissions — check scopes/roles in the platform settings.",
            404: "Endpoint not found — verify the URL path.",
            422: "Unprocessable request — check the request body format.",
            429: "Rate limited — wait a moment and retry.",
            500: "Server error on the platform side — try again shortly.",
        }
        hint = _HTTP_HINTS.get(e.code, "")
        return f"HTTP {e.code}: {e.reason}{f' — {hint}' if hint else ''}\n{err_body}"
    except Exception as e:
        return f"API error: {e}"


def _playwright_fetch(url: str) -> str:
    """Fetch a JavaScript-heavy page using Playwright headless Chromium.

    Used as fallback when requests+BeautifulSoup returns an empty/minimal shell
    (React/Vue/Angular SPAs that need JS to render their content).
    """
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
                page.goto(url, timeout=20000, wait_until="networkidle")
                html = page.content()
            finally:
                browser.close()
        # Parse rendered HTML with BeautifulSoup
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        lines = [l.strip() for l in soup.get_text(separator="\n", strip=True).splitlines() if l.strip()]
        return "\n".join(lines[:500])
    except ImportError:
        return ""  # Playwright not installed
    except Exception:
        return ""


def _exec_web_fetch(args: dict) -> str:
    url = args.get("url", "")
    if not url:
        return "No URL provided."

    # Basic SSRF guard — block loopback, link-local, and cloud metadata ranges
    import ipaddress, urllib.parse as _urlparse
    try:
        host = _urlparse.urlparse(url).hostname or ""
        try:
            addr = ipaddress.ip_address(host)
            if addr.is_loopback or addr.is_link_local or addr.is_private:
                return f"SSRF guard: {host} is a private/loopback address — fetch blocked."
        except ValueError:
            pass  # hostname, not a literal IP
        # Block common cloud metadata endpoints
        if host in ("169.254.169.254", "metadata.google.internal", "169.254.170.2"):
            return f"SSRF guard: cloud metadata endpoint {host} is blocked."
    except Exception:
        pass

    try:
        from src.internet.scraper import fetch_page
        content = fetch_page(url)
        # If content is sparse (<200 chars), this is likely a JS-rendered SPA shell.
        # Fall back to Playwright for full JS rendering.
        if not content or len(content.strip()) < 200:
            js_content = _playwright_fetch(url)
            if js_content and len(js_content) > len(content or ""):
                content = js_content
        if content:
            # Cap at 50 000 chars (matches OpenClaw's 50 KB limit)
            if len(content) > 50000:
                content = content[:50000] + "\n\n... (truncated)"
            return content
        return "No content extracted."
    except Exception as e:
        return f"Fetch error: {e}"


# ── Todo List state (session-scoped) ─────────────────────────────────

_todo_list: list[dict] = []


def _load_persisted_todos() -> list[dict]:
    """Load persisted todos from ~/.jarvis/todos.json."""
    try:
        from src.config import JARVIS_HOME
        todos_path = JARVIS_HOME / "todos.json"
        if todos_path.exists():
            import json as _j
            data = _j.loads(todos_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                # Filter out completed todos on load — only restore active work
                return [t for t in data if isinstance(t, dict) and t.get("status") != "completed"]
    except Exception:
        pass
    return []


def _persist_todos(todos: list[dict]) -> None:
    """Save todos to ~/.jarvis/todos.json (best-effort)."""
    try:
        from src.config import JARVIS_HOME
        import json as _j, tempfile as _tf2
        todos_path = JARVIS_HOME / "todos.json"
        JARVIS_HOME.mkdir(parents=True, exist_ok=True)
        _fd, _tmp = _tf2.mkstemp(dir=str(JARVIS_HOME), prefix=".todos-")
        try:
            with os.fdopen(_fd, "w", encoding="utf-8") as _f:
                _j.dump(todos, _f, indent=2, ensure_ascii=False)
        except Exception:
            try:
                os.unlink(_tmp)
            except Exception:
                pass
            raise
        os.replace(_tmp, str(todos_path))
    except Exception:
        pass  # Never let persistence break the tool


def _exec_todo_write(args: dict) -> str:
    """Update the session todo list.

    Enforces task-graph dependencies: if a task is moved to in_progress but
    one or more of its prerequisite tasks (tasks that list this task's ID in
    their `blocks` field) are not yet completed, the transition is blocked and
    the caller is told which tasks must complete first.
    """
    global _todo_list
    todos = args.get("todos", [])
    if not isinstance(todos, list):
        return "Invalid todos format. Expected a list of todo items."

    # Build a lookup of the *previous* state so we can detect status transitions
    prev_by_id: dict[str, dict] = {t["id"]: t for t in _todo_list if isinstance(t, dict) and "id" in t}

    # Build reverse dependency map: blocked_by[task_id] = [ids of tasks that must complete first]
    # A task X is blocked by task Y if Y.blocks contains X.
    blocked_by: dict[str, list[str]] = {}
    for t in todos:
        if not isinstance(t, dict):
            continue
        tid = t.get("id", "")
        for downstream_id in (t.get("blocks") or []):
            blocked_by.setdefault(downstream_id, []).append(tid)

    # Build content map for human-readable error messages
    content_by_id: dict[str, str] = {
        t.get("id", ""): t.get("content", t.get("id", "?"))
        for t in todos if isinstance(t, dict)
    }

    # Check for blocked transitions
    violations: list[str] = []
    for t in todos:
        if not isinstance(t, dict):
            continue
        tid = t.get("id", "")
        new_status = t.get("status", "")
        old_status = prev_by_id.get(tid, {}).get("status", "pending")

        # Only enforce on transitions into in_progress
        if new_status == "in_progress" and old_status != "in_progress":
            blockers = blocked_by.get(tid, [])
            if blockers:
                # Find any blocker that is not yet completed
                incomplete_blockers = [
                    b for b in blockers
                    if next((x.get("status") for x in todos if isinstance(x, dict) and x.get("id") == b), "pending")
                    != "completed"
                ]
                if incomplete_blockers:
                    blocker_labels = [f'"{content_by_id.get(b, b)}"' for b in incomplete_blockers]
                    violations.append(
                        f'Task "{content_by_id.get(tid, tid)}" is blocked — '
                        f'complete these first: {", ".join(blocker_labels)}'
                    )

    if violations:
        return (
            "Task graph violation — cannot start blocked tasks:\n"
            + "\n".join(f"  • {v}" for v in violations)
            + "\n\nComplete the prerequisite tasks before marking dependents as in_progress."
        )

    # Optional: strip completed tasks before storing
    if args.get("clear_completed", False):
        todos = [t for t in todos if isinstance(t, dict) and t.get("status") != "completed"]

    _todo_list = todos
    _persist_todos(todos)

    # Format summary
    pending = sum(1 for t in todos if t.get("status") == "pending")
    in_progress = sum(1 for t in todos if t.get("status") == "in_progress")
    completed = sum(1 for t in todos if t.get("status") == "completed")

    lines = [f"Todo list updated: {pending} pending, {in_progress} in progress, {completed} completed\n"]
    for t in todos:
        if not isinstance(t, dict):
            continue
        status_icon = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(t.get("status", ""), "[?]")
        content = t.get("content", "(no description)")
        dep_indicator = ""
        tid = t.get("id", "")
        if tid in blocked_by:
            blocker_ids = blocked_by[tid]
            incomplete = [b for b in blocker_ids
                          if next((x.get("status") for x in todos if isinstance(x, dict) and x.get("id") == b), "pending")
                          != "completed"]
            if incomplete:
                dep_indicator = " [blocked]"
        lines.append(f"  {status_icon} {content}{dep_indicator}")

    return "\n".join(lines)


def get_todo_list() -> list[dict]:
    """Return the current session todo list (for use by agent loop / UI)."""
    return _todo_list


def _exec_notebook_edit(args: dict) -> str:
    """Edit a Jupyter notebook cell."""
    notebook_path = os.path.expanduser(args.get("notebook_path", ""))
    action = args.get("action", "")
    cell_index = args.get("cell_index", None)
    new_source = args.get("new_source", "")
    cell_type = args.get("cell_type", "code")

    if not notebook_path:
        return "No notebook_path provided."
    if not action:
        return "No action provided."

    valid, err = _validate_path(notebook_path, write=True)
    if not valid:
        return err

    if not os.path.exists(notebook_path):
        return f"File not found: {notebook_path}"

    try:
        with open(notebook_path, "r", encoding="utf-8") as f:
            nb = json.load(f)

        cells = nb.get("cells", [])

        if action == "edit_cell":
            if cell_index is None:
                return "cell_index is required for edit_cell."
            if cell_index < 0 or cell_index >= len(cells):
                return f"cell_index {cell_index} out of range (notebook has {len(cells)} cells)."
            lines = new_source.split("\n") if new_source else []
            cells[cell_index]["source"] = [l + "\n" for l in lines[:-1]] + [lines[-1]] if lines else []

        elif action == "add_cell":
            insert_at = cell_index if cell_index is not None else len(cells)
            insert_at = max(0, min(insert_at, len(cells)))
            lines = new_source.split("\n") if new_source else []
            source = [l + "\n" for l in lines[:-1]] + [lines[-1]] if lines else []
            new_cell = {
                "cell_type": cell_type,
                "metadata": {},
                "source": source,
            }
            if cell_type == "code":
                new_cell["execution_count"] = None
                new_cell["outputs"] = []
            cells.insert(insert_at, new_cell)

        elif action == "delete_cell":
            if cell_index is None:
                return "cell_index is required for delete_cell."
            if cell_index < 0 or cell_index >= len(cells):
                return f"cell_index {cell_index} out of range (notebook has {len(cells)} cells)."
            deleted = cells.pop(cell_index)
            deleted_type = deleted.get("cell_type", "unknown")

        elif action == "execute":
            # Execute the notebook in-place via jupyter nbconvert
            import shutil as _shutil
            if not _shutil.which("jupyter"):
                return "jupyter not found. Install: pip install jupyter"
            import subprocess as _sp
            timeout_s = int(args.get("timeout", 120))
            r = _sp.run(
                ["jupyter", "nbconvert", "--to", "notebook", "--execute",
                 f"--ExecutePreprocessor.timeout={timeout_s}",
                 "--inplace", notebook_path],
                capture_output=True, text=True, timeout=timeout_s + 30,
            )
            if r.returncode != 0:
                err = (r.stderr or r.stdout or "unknown error")[:1000]
                return f"Notebook execution failed:\n{err}"
            return f"Executed notebook: {notebook_path}"

        else:
            return f"Unknown action: {action}. Use edit_cell, add_cell, delete_cell, or execute."

        nb["cells"] = cells
        with open(notebook_path, "w", encoding="utf-8") as f:
            json.dump(nb, f, indent=1, ensure_ascii=False)
            f.write("\n")

        if action == "edit_cell":
            return f"Edited cell {cell_index} in {notebook_path}."
        elif action == "add_cell":
            return f"Added {cell_type} cell at index {insert_at} in {notebook_path}."
        elif action == "delete_cell":
            return f"Deleted {deleted_type} cell at index {cell_index} from {notebook_path}."

    except json.JSONDecodeError as e:
        return f"Invalid notebook JSON: {e}"
    except Exception as e:
        return f"Error editing notebook: {e}"


# ── New Tool Implementations ────────────────────────────────────────────


def _exec_glob(args: dict) -> str:
    """Fast file pattern matching using Python glob."""
    pattern = args.get("pattern", "")
    path = os.path.expanduser(args.get("path", "."))

    if not pattern:
        return "No pattern provided."

    # Validate search path
    valid, err = _validate_path(path, write=False)
    if not valid:
        return err

    try:
        full_pattern = os.path.join(path, pattern)
        resolved_base = os.path.realpath(path)
        _base_prefix = resolved_base.rstrip(os.sep) + os.sep

        # Prefer ripgrep --files (gitignore-aware, fast, excludes .git/node_modules/etc)
        if shutil.which("rg"):
            try:
                # Use gitignore for cleaner results
                cmd = [
                    "rg", "--files",
                    "--glob", pattern,
                    path,
                ]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                matches = [m.strip() for m in r.stdout.splitlines() if m.strip()]
                # Also filter path escapes
                matches = [m for m in matches
                           if os.path.realpath(m) == resolved_base
                           or os.path.realpath(m).startswith(_base_prefix)]
                matches.sort(key=lambda f: os.path.getmtime(f) if os.path.exists(f) else 0, reverse=True)
                matches = matches[:250]
                if not matches:
                    return f"No files matching '{pattern}' in {path}"
                return f"Found {len(matches)} files:\n" + "\n".join(matches)
            except Exception:
                pass  # fall through to Python glob

        # Fallback: Python glob with manual .git/ exclusion
        matches = _glob.glob(full_pattern, recursive=True)
        matches = [m for m in matches
                   if os.path.realpath(m) == resolved_base
                   or os.path.realpath(m).startswith(_base_prefix)]
        # Exclude .git internals and common noise dirs
        _NOISE = {".git", "node_modules", "__pycache__", ".venv", "venv",
                  "dist", "build", "target", ".eggs"}
        matches = [m for m in matches
                   if not any(part in _NOISE for part in m.replace("\\", "/").split("/"))]
        matches.sort(key=lambda f: os.path.getmtime(f) if os.path.exists(f) else 0, reverse=True)
        matches = matches[:250]
        if not matches:
            return f"No files matching '{pattern}' in {path}"
        return f"Found {len(matches)} files:\n" + "\n".join(matches)
    except Exception as e:
        return f"Glob error: {e}"


def _exec_grep(args: dict) -> str:
    """Content search using ripgrep."""
    pattern = args.get("pattern", "")
    path = os.path.expanduser(args.get("path", "."))

    if not pattern:
        return "No pattern provided."

    # Validate search path
    valid, err = _validate_path(path, write=False)
    if not valid:
        return err

    try:
        from src.agent.ripgrep import RipgrepConfig, search as rg_search

        config = RipgrepConfig(
            pattern=pattern,
            path=path,
            glob=args.get("glob", ""),
            file_type=args.get("type", ""),
            output_mode=args.get("output_mode", "files_with_matches"),
            context=min(args.get("context", 0), 100),
            case_insensitive=args.get("-i", False),
            multiline=args.get("multiline", False),
            head_limit=min(args.get("head_limit", 250), 500),
        )
        result = rg_search(config)
        return result.output
    except Exception as e:
        return f"Grep error: {e}"


# ── Task Management ────────────────────────────────────────────────────

_task_list: list[dict] = []
_task_counter: int = 0


def _exec_task_create(args: dict) -> str:
    """Create a new task."""
    global _task_counter
    subject = args.get("subject", "")
    description = args.get("description", "")
    if not subject:
        return "No subject provided."

    _task_counter += 1
    task_id = f"task-{_task_counter}"
    task = {
        "id": task_id,
        "subject": subject,
        "description": description,
        "status": "pending",
        "owner": "",
        "activeForm": args.get("activeForm", ""),
        "blocks": [],
        "blockedBy": [],
        "output": "",
    }
    _task_list.append(task)
    return f"Created task {task_id}: {subject}"


def _exec_task_get(args: dict) -> str:
    """Get task by ID."""
    task_id = args.get("task_id", "")
    if not task_id:
        return "No task_id provided."
    for task in _task_list:
        if task["id"] == task_id:
            return json.dumps(task, indent=2)
    return f"Task not found: {task_id}"


def _exec_task_list(args: dict) -> str:
    """List all tasks."""
    if not _task_list:
        return "No tasks."
    lines = []
    for t in _task_list:
        status_icon = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]", "deleted": "[-]"}.get(t.get("status", ""), "[?]")
        owner = f" ({t['owner']})" if t.get("owner") else ""
        blocked = f" blocked by: {', '.join(t['blockedBy'])}" if t.get("blockedBy") else ""
        lines.append(f"{status_icon} {t['id']}: {t['subject']}{owner}{blocked}")
    return "\n".join(lines)


def _exec_task_stop(args: dict) -> str:
    """Stop a running task."""
    task_id = args.get("task_id", "")
    if not task_id:
        return "No task_id provided."
    for task in _task_list:
        if task["id"] == task_id:
            if task["status"] == "in_progress":
                task["status"] = "pending"
                return f"Stopped task {task_id}"
            return f"Task {task_id} is not in_progress (status: {task['status']})"
    return f"Task not found: {task_id}"


def _exec_task_update(args: dict) -> str:
    """Update a task."""
    task_id = args.get("task_id", "")
    if not task_id:
        return "No task_id provided."
    for task in _task_list:
        if task.get("id") == task_id:
            updated = []
            if "status" in args:
                new_status = args["status"]
                if new_status == "deleted":
                    _task_list.remove(task)
                    return f"Deleted task {task_id}"
                task["status"] = new_status
                updated.append(f"status={new_status}")
            if "subject" in args:
                task["subject"] = args["subject"]
                updated.append("subject")
            if "description" in args:
                task["description"] = args["description"]
                updated.append("description")
            if "activeForm" in args:
                task["activeForm"] = args["activeForm"]
                updated.append("activeForm")
            if "owner" in args:
                task["owner"] = args["owner"]
                updated.append(f"owner={args['owner']}")
            if "addBlocks" in args:
                task.setdefault("blocks", []).extend(args["addBlocks"])
                updated.append("blocks")
            if "addBlockedBy" in args:
                task.setdefault("blockedBy", []).extend(args["addBlockedBy"])
                updated.append("blockedBy")
            return f"Updated task {task_id}: {', '.join(updated)}" if updated else f"No changes to task {task_id}"
    return f"Task not found: {task_id}"


def _exec_task_output(args: dict) -> str:
    """Get task output."""
    task_id = args.get("task_id", "")
    if not task_id:
        return "No task_id provided."
    for task in _task_list:
        if task["id"] == task_id:
            output = task.get("output", "")
            return output if output else f"No output for task {task_id}"
    return f"Task not found: {task_id}"


# ── Config Tool ────────────────────────────────────────────────────────


def _exec_config(args: dict) -> str:
    """Get or set JARVIS configuration settings."""
    setting = args.get("setting", "")
    value = args.get("value", None)

    if not setting:
        return "No setting name provided."

    jarvis_home = os.path.expanduser(os.environ.get("JARVIS_HOME", "~/.jarvis"))
    settings_path = os.path.join(jarvis_home, "settings.json")

    # Load existing settings
    settings = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except (OSError, json.JSONDecodeError):
            settings = {}

    if value is None:
        # GET mode
        current = settings.get(setting, "(not set)")
        return f"{setting} = {current}"
    else:
        # SET mode
        settings[setting] = value
        try:
            os.makedirs(jarvis_home, exist_ok=True)
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2)
            return f"Set {setting} = {value}"
        except Exception as e:
            return f"Error saving setting: {e}"


# ── Sleep Tool ─────────────────────────────────────────────────────────


def _exec_sleep(args: dict) -> str:
    """Sleep for a specified duration, broadcasting status to UI if connected."""
    import time
    duration_ms = args.get("duration_ms", 1000)
    reason = args.get("reason", "")
    duration_s = min(duration_ms / 1000.0, 300)  # Cap at 5 minutes

    # Broadcast sleep status to connected UI clients
    status_msg = f"Sleeping for {duration_s:.1f}s"
    if reason:
        status_msg += f" — {reason}"
    try:
        if _open_url_hook is not None:
            # Re-use the broadcast path: send a __STATUS__ marker that
            # web_server can intercept and display in the UI
            pass  # open_url isn't right for this; use event hook below
        # Broadcast via a registered status hook (set by web_server)
        if _sleep_status_hook is not None:
            _sleep_status_hook({"type": "sleep_start", "duration_s": duration_s, "reason": reason})
    except Exception:
        pass

    time.sleep(duration_s)

    try:
        if _sleep_status_hook is not None:
            _sleep_status_hook({"type": "sleep_end", "duration_s": duration_s})
    except Exception:
        pass

    return f"Slept for {duration_s:.1f}s" + (f" ({reason})" if reason else "")


# ── MCP Resources ─────────────────────────────────────────────────────


def _exec_list_mcp_resources(args: dict) -> str:
    """List available MCP server resources."""
    try:
        manager = _mcp_manager
        if manager is None:
            return "MCP not initialized."
        server = args.get("server", None)
        if hasattr(manager, "list_resources"):
            resources = manager.list_resources(server=server)
            if not resources:
                return "No MCP resources available."
            lines = []
            for r in resources:
                name = r.get("name", r.get("uri", "unknown"))
                srv = r.get("server", "")
                desc = r.get("description", "")
                lines.append(f"- {name} [{srv}] {desc}")
            return "\n".join(lines)
        return "MCP manager does not support resource listing."
    except Exception as e:
        return f"MCP resource listing error: {e}"


# ── SMS via KDE Connect ───────────────────────────────────────────────

def _get_twilio_config() -> dict:
    """Read Twilio credentials from providers.json."""
    try:
        import json
        import pathlib
        data = json.loads((pathlib.Path.home() / ".jarvis" / "providers.json").read_text())
        twilio = data.get("twilio", {})
        return {
            "account_sid": twilio.get("account_sid", os.environ.get("TWILIO_ACCOUNT_SID", "")),
            "auth_token": twilio.get("auth_token", os.environ.get("TWILIO_AUTH_TOKEN", "")),
            "from_number": twilio.get("from_number", os.environ.get("TWILIO_FROM_NUMBER", "")),
        }
    except Exception:
        return {
            "account_sid": os.environ.get("TWILIO_ACCOUNT_SID", ""),
            "auth_token": os.environ.get("TWILIO_AUTH_TOKEN", ""),
            "from_number": os.environ.get("TWILIO_FROM_NUMBER", ""),
        }


def _exec_send_sms(args: dict) -> str:
    """Send SMS or WhatsApp message via Twilio (primary) or KDE Connect (local fallback)."""
    phone = args.get("phone_number", "").strip()
    message = args.get("message", "").strip()
    channel = args.get("channel", "sms").lower()  # "sms", "whatsapp", or "kde"

    if not phone:
        return "No phone number provided."
    if not message:
        return "No message provided."

    # ── Primary: Twilio ────────────────────────────────────────────────
    if channel != "kde":
        cfg = _get_twilio_config()
        if cfg["account_sid"] and cfg["auth_token"] and cfg["from_number"]:
            try:
                import requests as _req
                from_num = cfg["from_number"]
                to_num = phone
                # WhatsApp channel prefix
                if channel == "whatsapp":
                    if not from_num.startswith("whatsapp:"):
                        from_num = f"whatsapp:{from_num}"
                    if not to_num.startswith("whatsapp:"):
                        to_num = f"whatsapp:{to_num}"

                resp = _req.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{cfg['account_sid']}/Messages.json",
                    auth=(cfg["account_sid"], cfg["auth_token"]),
                    data={"From": from_num, "To": to_num, "Body": message},
                    timeout=15,
                )
                if resp.status_code in (200, 201):
                    sid = resp.json().get("sid", "")
                    return f"Message sent via Twilio ({channel}). SID: {sid}"
                else:
                    err = resp.json().get("message", resp.text[:200])
                    return f"Twilio error {resp.status_code}: {err}"
            except ImportError:
                return "Twilio requires: pip install requests (already installed) — check providers.json for credentials"
            except Exception as e:
                return f"Twilio error: {e}"
        else:
            if channel == "whatsapp":
                return (
                    "WhatsApp sending requires Twilio credentials.\n"
                    "Add to ~/.jarvis/providers.json:\n"
                    '  "twilio": {"account_sid": "...", "auth_token": "...", "from_number": "whatsapp:+1..."}'
                )

    # ── Fallback: KDE Connect (local, same WiFi only) ──────────────────
    try:
        result = subprocess.run(
            ["kdeconnect-cli", "-a", "--id-only"],
            capture_output=True, text=True, timeout=5,
        )
        devices = [d.strip() for d in result.stdout.strip().split("\n") if d.strip()]
    except FileNotFoundError:
        return (
            "No message provider configured.\n"
            "Options:\n"
            "1. Twilio (SMS/WhatsApp): add credentials to ~/.jarvis/providers.json\n"
            "2. KDE Connect: sudo apt install kdeconnect"
        )
    except Exception as e:
        return f"Error listing KDE Connect devices: {e}"

    if not devices:
        # Try listing all paired (even offline)
        try:
            result2 = subprocess.run(
                ["kdeconnect-cli", "-l", "--id-only"],
                capture_output=True, text=True, timeout=5,
            )
            paired = [d.strip() for d in result2.stdout.strip().split("\n") if d.strip()]
            if paired:
                return (
                    "Phone is paired but not reachable. Make sure:\n"
                    "- Phone and PC are on the same WiFi\n"
                    "- KDE Connect app is running on the phone\n"
                    f"Paired devices: {', '.join(paired)}"
                )
        except Exception:
            pass
        return (
            "No paired phone found and no Twilio credentials configured.\n"
            "To set up Twilio (SMS/WhatsApp):\n"
            "  Add to ~/.jarvis/providers.json:\n"
            '  "twilio": {"account_sid": "ACxxx", "auth_token": "xxx", "from_number": "+1xxx"}\n'
            "To set up KDE Connect:\n"
            "  1. Install 'KDE Connect' app on your Android phone\n"
            "  2. Make sure phone and PC are on the same WiFi\n"
            "  3. Run: kdeconnect-cli -l"
        )

    # Send via first available KDE Connect device
    device_id = devices[0]
    try:
        result = subprocess.run(
            ["kdeconnect-cli", "--send-sms", message, "--destination", phone, "-d", device_id],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return f"SMS sent to {phone} via KDE Connect."
        return f"KDE Connect error: {result.stderr.strip() or result.stdout.strip() or 'Unknown error'}"
    except Exception as e:
        return f"KDE Connect send error: {e}"


# ── Domain Tools ──────────────────────────────────────────────────────

def _exec_sysinfo(args: dict) -> str:
    """System health diagnostics — services, processes, logs, disk, memory, network."""
    query = args.get("query", "all")
    filt = args.get("filter", "")
    parts = []

    def _run(cmd: str, label: str) -> str:
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
            out = r.stdout.strip() or r.stderr.strip()
            if filt and filt.lower() not in out.lower():
                out = "\n".join(l for l in out.splitlines() if filt.lower() in l.lower())
            return f"── {label} ──\n{out}" if out else f"── {label} ── (no output)"
        except subprocess.TimeoutExpired:
            return f"── {label} ── (timeout)"
        except Exception as e:
            return f"── {label} ── error: {e}"

    if query in ("services", "all"):
        cmd = "systemctl list-units --type=service --state=running --no-pager --plain 2>/dev/null | head -40"
        if filt:
            cmd = f"systemctl status {filt} --no-pager 2>/dev/null || systemctl list-units --type=service --no-pager --plain 2>/dev/null | grep -i '{filt}'"
        parts.append(_run(cmd, "Services"))

    if query in ("processes", "all"):
        parts.append(_run(
            "ps aux --sort=-%cpu | head -20",
            "Top Processes (CPU)"
        ))

    if query in ("logs", "all"):
        cmd = f"journalctl -n 50 --no-pager 2>/dev/null"
        if filt:
            cmd = f"journalctl -n 100 --no-pager -u '{filt}' 2>/dev/null || journalctl -n 100 --no-pager --grep='{filt}' 2>/dev/null"
        parts.append(_run(cmd, "Recent Logs"))

    if query in ("disk", "all"):
        parts.append(_run("df -h --output=target,size,used,avail,pcent 2>/dev/null", "Disk Usage"))

    if query in ("memory", "all"):
        parts.append(_run("free -h 2>/dev/null && echo && cat /proc/meminfo 2>/dev/null | grep -E 'MemTotal|MemFree|MemAvailable|SwapTotal|SwapFree'", "Memory"))

    if query in ("network", "all"):
        parts.append(_run("ss -tuln 2>/dev/null | head -30", "Listening Ports"))
        parts.append(_run("ip -brief addr 2>/dev/null", "Interfaces"))

    if query == "restart":
        if not filt:
            return "Specify the service name in the 'filter' parameter."
        r = subprocess.run(
            f"systemctl restart {filt}",
            shell=True, capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            return f"Service '{filt}' restarted."
        return f"Failed to restart '{filt}': {r.stderr.strip() or r.stdout.strip()}"

    return "\n\n".join(parts) if parts else "No data collected."


def _exec_container(args: dict) -> str:
    """Docker/Kubernetes operations with smart defaults."""
    action = args.get("action", "list")
    target = args.get("target", "")
    namespace = args.get("namespace", "default")
    command = args.get("command", "sh")
    tail = args.get("tail", 50)

    # Detect what's available
    has_kubectl = shutil.which("kubectl") is not None
    has_docker = shutil.which("docker") is not None

    def _run(cmd: str) -> str:
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            return (r.stdout.strip() or r.stderr.strip() or "(no output)")
        except subprocess.TimeoutExpired:
            return "Command timed out."
        except Exception as e:
            return f"Error: {e}"

    if action == "list":
        results = []
        if has_kubectl:
            results.append("── Kubernetes pods ──\n" + _run(f"kubectl get pods -n {namespace} 2>/dev/null"))
        if has_docker:
            results.append("── Docker containers ──\n" + _run("docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' 2>/dev/null"))
        return "\n\n".join(results) if results else "Neither kubectl nor docker found."

    if action == "status":
        if not target:
            return "Target required for status check."
        results = []
        if has_kubectl:
            results.append(_run(f"kubectl get pod {target} -n {namespace} -o wide 2>/dev/null || kubectl get deployment {target} -n {namespace} 2>/dev/null"))
        if has_docker:
            results.append(_run(f"docker inspect {target} --format '{{{{.Name}}}} {{{{.State.Status}}}} ({{{{.State.StartedAt}}}})' 2>/dev/null"))
        return "\n".join(results) if results else f"Target '{target}' not found."

    if action == "logs":
        if not target:
            return "Target required for logs."
        if has_kubectl:
            return _run(f"kubectl logs {target} -n {namespace} --tail={tail} 2>/dev/null")
        if has_docker:
            return _run(f"docker logs {target} --tail {tail} 2>/dev/null")
        return "Neither kubectl nor docker found."

    if action == "restart":
        if not target:
            return "Target required for restart."
        if has_kubectl:
            return _run(f"kubectl rollout restart deployment/{target} -n {namespace} 2>/dev/null")
        if has_docker:
            return _run(f"docker restart {target} 2>/dev/null")
        return "Neither kubectl nor docker found."

    if action == "exec":
        if not target:
            return "Target required for exec."
        if has_kubectl:
            return _run(f"kubectl exec {target} -n {namespace} -- {command} 2>/dev/null")
        if has_docker:
            return _run(f"docker exec {target} {command} 2>/dev/null")
        return "Neither kubectl nor docker found."

    if action == "rollback":
        if not target:
            return "Target required for rollback."
        if has_kubectl:
            return _run(f"kubectl rollout undo deployment/{target} -n {namespace} 2>/dev/null")
        return "kubectl not found — rollback only available for Kubernetes."

    if action == "deploy":
        if not target:
            return "Target (manifest path or compose file) required for deploy."
        if target.endswith(".yaml") or target.endswith(".yml"):
            if has_kubectl and "compose" not in target:
                return _run(f"kubectl apply -f {target} 2>/dev/null")
            if has_docker:
                return _run(f"docker compose -f {target} up -d 2>/dev/null")
        return f"Unknown deploy target: {target}"

    has_compose = shutil.which("docker") is not None

    if action in ("up", "compose_up"):
        if not target:
            return "Target (compose file path or directory) required."
        compose_file = target if target.endswith((".yml", ".yaml")) else os.path.join(target, "docker-compose.yml")
        if not os.path.exists(os.path.expanduser(compose_file)):
            compose_file = os.path.join(target, "docker-compose.yaml")
        if not has_compose:
            return "docker not found."
        return _run(f"docker compose -f {compose_file} up -d 2>&1")

    if action in ("down", "compose_down"):
        if not target:
            return "Target (compose file path or directory) required."
        compose_file = target if target.endswith((".yml", ".yaml")) else os.path.join(target, "docker-compose.yml")
        if not has_compose:
            return "docker not found."
        return _run(f"docker compose -f {compose_file} down 2>&1")

    if action in ("pull", "compose_pull"):
        if not target:
            return "Target (compose file path or directory) required."
        compose_file = target if target.endswith((".yml", ".yaml")) else os.path.join(target, "docker-compose.yml")
        if not has_compose:
            return "docker not found."
        return _run(f"docker compose -f {compose_file} pull 2>&1")

    return f"Unknown action: {action}"


def _exec_security_scan(args: dict) -> str:
    """Security scanning — ports, web headers, SSL, vulns."""
    target = args.get("target", "")
    scan_type = args.get("scan_type", "ports")
    ports = args.get("ports", "")
    all_ports = args.get("all_ports", False)

    if not target:
        return "Target required."

    has_nmap = shutil.which("nmap") is not None
    has_curl = shutil.which("curl") is not None

    def _run(cmd: str, timeout: int = 60) -> str:
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            return (r.stdout.strip() or r.stderr.strip() or "(no output)")
        except subprocess.TimeoutExpired:
            return "Scan timed out — try a narrower port range."
        except Exception as e:
            return f"Error: {e}"

    # Strip protocol for nmap targets
    nmap_target = target.replace("https://", "").replace("http://", "").split("/")[0]

    if scan_type == "ports":
        if not has_nmap:
            return "nmap not found. Install with: sudo apt install nmap"
        port_spec = f"-p {ports}" if ports else ("-p-" if all_ports else "--top-ports 1000")
        cmd = f"nmap -sV --open -T4 {port_spec} {nmap_target}"
        return f"$ {cmd}\n\n" + _run(cmd, timeout=120)

    if scan_type == "vulns":
        if not has_nmap:
            return "nmap not found. Install with: sudo apt install nmap"
        port_spec = f"-p {ports}" if ports else "--top-ports 100"
        cmd = f"nmap -sV --script vuln {port_spec} {nmap_target}"
        return f"$ {cmd}\n\n" + _run(cmd, timeout=180)

    if scan_type == "headers":
        if not has_curl:
            return "curl not found."
        url = target if target.startswith("http") else f"https://{target}"
        cmd = f"curl -sI --max-time 10 '{url}'"
        raw = _run(cmd)
        # Highlight missing security headers
        security_headers = [
            "Strict-Transport-Security", "Content-Security-Policy",
            "X-Frame-Options", "X-Content-Type-Options",
            "Referrer-Policy", "Permissions-Policy",
        ]
        missing = [h for h in security_headers if h.lower() not in raw.lower()]
        result = f"HTTP Headers for {url}:\n{raw}"
        if missing:
            result += f"\n\nMissing security headers: {', '.join(missing)}"
        return result

    if scan_type == "ssl":
        if not has_nmap:
            return "nmap not found. Install with: sudo apt install nmap"
        cmd = f"nmap -sV --script ssl-enum-ciphers,ssl-cert -p 443,8443 {nmap_target}"
        return f"$ {cmd}\n\n" + _run(cmd, timeout=60)

    if scan_type == "web":
        results = []
        url = target if target.startswith("http") else f"https://{target}"
        if has_curl:
            results.append("── HTTP Headers ──\n" + _run(f"curl -sI --max-time 10 '{url}'"))
        if has_nmap:
            results.append("── Open Ports ──\n" + _run(f"nmap -sV --open --top-ports 20 -T4 {nmap_target}", timeout=30))
        if shutil.which("whatweb"):
            results.append("── Technologies ──\n" + _run(f"whatweb -a 1 '{url}' 2>/dev/null", timeout=20))
        return "\n\n".join(results) if results else "No scanning tools found."

    if scan_type in ("nikto", "web_vuln"):
        has_nikto = shutil.which("nikto") is not None
        if not has_nikto:
            return "nikto not found. Install with: sudo apt install nikto"
        url = target if target.startswith("http") else f"https://{target}"
        port_flag = f" -p {ports}" if ports else ""
        cmd = f"nikto -h '{url}'{port_flag} -nointeractive 2>/dev/null"
        return f"$ {cmd}\n\n" + _run(cmd, timeout=180)

    if scan_type in ("gobuster", "dirbust", "dirs"):
        has_gobuster = shutil.which("gobuster") is not None
        has_dirb = shutil.which("dirb") is not None
        url = target if target.startswith("http") else f"https://{target}"
        wordlist = args.get("wordlist", "")
        if not wordlist:
            # Try common Kali wordlist paths
            for candidate in (
                "/usr/share/wordlists/dirb/common.txt",
                "/usr/share/dirb/wordlists/common.txt",
                "/usr/share/seclists/Discovery/Web-Content/common.txt",
            ):
                if os.path.exists(candidate):
                    wordlist = candidate
                    break
        if has_gobuster:
            wl_flag = f"-w '{wordlist}'" if wordlist else "-w /usr/share/wordlists/dirb/common.txt"
            cmd = f"gobuster dir -u '{url}' {wl_flag} -q --no-error -t 20 2>/dev/null"
            return f"$ {cmd}\n\n" + _run(cmd, timeout=120)
        if has_dirb:
            wl_arg = f" '{wordlist}'" if wordlist else ""
            cmd = f"dirb '{url}'{wl_arg} -S -r 2>/dev/null"
            return f"$ {cmd}\n\n" + _run(cmd, timeout=120)
        return "Neither gobuster nor dirb found. Install: sudo apt install gobuster dirb"

    if scan_type in ("whatweb", "tech", "fingerprint"):
        has_whatweb = shutil.which("whatweb") is not None
        if not has_whatweb:
            return "whatweb not found. Install with: sudo apt install whatweb"
        url = target if target.startswith("http") else f"https://{target}"
        cmd = f"whatweb -a 3 '{url}' 2>/dev/null"
        return f"$ {cmd}\n\n" + _run(cmd, timeout=30)

    if scan_type in ("dns", "subdomain"):
        results = []
        if shutil.which("dig"):
            results.append("── DNS Records ──\n" + _run(f"dig +short ANY {nmap_target} 2>/dev/null || dig {nmap_target} 2>/dev/null"))
        if shutil.which("nslookup"):
            results.append("── NSLookup ──\n" + _run(f"nslookup {nmap_target} 2>/dev/null"))
        if shutil.which("subfinder"):
            results.append("── Subdomains ──\n" + _run(f"subfinder -d {nmap_target} -silent 2>/dev/null", timeout=30))
        return "\n\n".join(results) if results else _run(f"host {nmap_target} 2>/dev/null")

    return f"Unknown scan type: {scan_type}"


# ── Network scan / device discovery ───────────────────────────────────

def _exec_network_scan(args: dict) -> str:
    """JARVIS surveys his own network: known devices, LAN topology, public IP."""
    import asyncio
    import json as _json

    action = args.get("action", "status")

    try:
        from src.server.device_registry import get_registry
        registry = get_registry()
    except Exception as e:
        return f"Device registry unavailable: {e}"

    # Run async discovery inside the executor context (may be called from sync loop)
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    def _run(coro):
        if loop.is_running():
            # We are inside an async context — use run_coroutine_threadsafe
            import concurrent.futures
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
            return fut.result(timeout=35)
        return loop.run_until_complete(coro)

    if action == "public_ip":
        ip = _run(registry.get_public_ip())
        return f"JARVIS public IP: {ip or 'unavailable (no internet or blocked)'}"

    if action == "interfaces":
        ifaces = registry.get_local_interfaces()
        if not ifaces:
            return "No network interfaces detected."
        lines = ["JARVIS network interfaces:"]
        for iface in ifaces:
            name = iface.get("interface", "")
            ip   = iface.get("ip", iface.get("ip", ""))
            subnet = iface.get("subnet", "")
            line = f"  {name}  {ip}"
            if subnet and subnet != ip:
                line += f"  ({subnet})"
            lines.append(line)
        return "\n".join(lines)

    if action == "discover":
        discovered = _run(registry.discover_network(force=True))
        public_ip  = _run(registry.get_public_ip())
        ifaces     = registry.get_local_interfaces()

        lines = [
            f"Network discovery complete.  {len(discovered)} device(s) on LAN.",
            f"JARVIS public IP: {public_ip or 'unknown'}",
            "",
        ]
        if ifaces:
            local_ips = [i.get("ip", "") for i in ifaces]
            lines.append(f"Local interfaces: {', '.join(ip for ip in local_ips if ip)}")
            lines.append("")

        lines.append(f"{'IP':<18} {'HOSTNAME':<28} {'MAC':<18} {'TRUST':<12} SOURCE")
        lines.append("─" * 84)
        for dev in discovered:
            ip   = dev.get("ip", "")
            host = dev.get("hostname", "")[:26]
            mac  = dev.get("mac", "—")[:17]
            trust= dev.get("trust", "SANDBOXED")
            src  = dev.get("source", "")
            lines.append(f"{ip:<18} {host:<28} {mac:<18} {trust:<12} {src}")

        # Also show registered (connected) devices
        known = registry.get_all()
        if known:
            lines.append("")
            lines.append("Connected / previously seen devices:")
            for d in known:
                ts = int(d.last_seen)
                lines.append(
                    f"  {d.ip:<18} [{d.label}]  trust={d.trust.name}"
                    f"  seen {d.total_connections}x  hostname={d.hostname or '—'}"
                )
        return "\n".join(lines)

    # Default: status (fast, no scan)
    known = registry.get_all()
    summary = registry.summary()
    ifaces  = registry.get_local_interfaces()

    lines = ["JARVIS device awareness:"]
    lines.append(f"  Public IP       : {summary.get('public_ip', 'unknown (run discover)')}")
    local_ips = [i.get("ip", "") for i in ifaces]
    lines.append(f"  Local IPs       : {', '.join(ip for ip in local_ips if ip) or 'none detected'}")
    lines.append(f"  Known devices   : {summary['total']}")
    lines.append("")

    by_trust = summary.get("by_trust", {})
    for trust_name in ("OWNER", "ELEVATED", "STANDARD", "SANDBOXED"):
        devs = by_trust.get(trust_name, [])
        if devs:
            lines.append(f"  [{trust_name}]")
            for d in devs:
                lines.append(
                    f"    {d['ip']:<18} {d.get('label',''):<20} "
                    f"hostname={d.get('hostname','—')}  "
                    f"seen={d.get('total_connections',1)}x"
                )

    if not known:
        lines.append("  No devices registered yet. Devices appear on first WS connect.")
        lines.append("  Run action='discover' for a live LAN scan.")

    return "\n".join(lines)


# ── Browser tool (Playwright) ─────────────────────────────────────────────────

# Persistent browser/page within a process (one session per JARVIS instance)
_pw_instance = None   # playwright sync instance
_pw_browser  = None   # browser
_pw_page     = None   # current page


def _exec_rag_search(args: dict) -> str:
    """Search/manage the local RAG knowledge base."""
    action = args.get("action", "search")
    query = args.get("query", "").strip()
    k = int(args.get("k", 5))
    source_filter = args.get("source_filter", "")
    ingest_path = args.get("path", "").strip()

    try:
        from src.rag import get_pipeline
        pipeline = get_pipeline()
    except RuntimeError as e:
        return f"RAG unavailable: {e}"
    except Exception as e:
        return f"RAG pipeline error: {e}"

    if action == "stats":
        try:
            stats = pipeline.stats()
            chunks = stats.get("chunks", 0)
            sources = stats.get("sources", [])
            size_mb = stats.get("size_mb", 0)
            lines = [
                f"RAG knowledge base:",
                f"  Chunks  : {chunks}",
                f"  Sources : {len(sources)}",
            ]
            if size_mb:
                lines.append(f"  Size    : {size_mb:.1f} MB")
            if sources:
                lines.append("")
                lines.append("Indexed sources:")
                for s in sources[:30]:
                    lines.append(f"  {s}")
                if len(sources) > 30:
                    lines.append(f"  ... and {len(sources) - 30} more")
            return "\n".join(lines)
        except Exception as e:
            return f"RAG stats error: {e}"

    if action == "reindex":
        try:
            result = pipeline.reindex() if hasattr(pipeline, "reindex") else pipeline.rebuild()
            chunks = result.get("chunks", "?") if isinstance(result, dict) else "?"
            return f"Re-index complete. {chunks} chunks indexed."
        except AttributeError:
            return "This RAG pipeline does not support reindex — use /ingest <path> to add documents."
        except Exception as e:
            return f"Reindex error: {e}"

    if action == "ingest":
        if not ingest_path:
            return "path is required for ingest action."
        try:
            result = pipeline.ingest(ingest_path)
            chunks = result.get("chunks", "?") if isinstance(result, dict) else "?"
            return f"Ingested '{ingest_path}'. {chunks} new chunks added."
        except Exception as e:
            return f"Ingest error: {e}"

    # Default: search
    if not query:
        return "query is required for search. Use action='stats' to inspect the knowledge base."

    try:
        stats = pipeline.stats()
        if stats.get("chunks", 0) == 0:
            return (
                "Knowledge base is empty. Use /ingest <path|url> to add documents first.\n"
                "Example: /ingest ~/Documents/notes.pdf"
            )

        where = {"source": source_filter} if source_filter else None
        results = pipeline.query(query, k=k, where=where)

        if not results:
            return f"No relevant results found for: {query}"

        lines = [f"RAG search: '{query}' — {len(results)} result(s)\n"]
        for i, (text, meta, dist) in enumerate(results, 1):
            source = meta.get("source", "unknown")
            score = 1.0 - dist
            snippet = text.strip()[:400]
            lines.append(f"[{i}] Score: {score:.2f} | Source: {source}")
            lines.append(snippet)
            lines.append("")

        return "\n".join(lines).strip()
    except Exception as e:
        return f"RAG search error: {e}"


def _exec_browser(args: dict) -> str:
    """Playwright browser tool — navigate, click, type, extract, screenshot, eval JS."""
    global _pw_instance, _pw_browser, _pw_page
    import tempfile, base64

    action   = args.get("action", "navigate")
    headless = args.get("headless", True)

    def _ensure_browser():
        global _pw_instance, _pw_browser, _pw_page
        try:
            # Prefer pipx-installed playwright (system apt version has broken Node.js driver)
            import sys as _sys, glob as _g
            _pw_venv_lib = os.path.expanduser("~/.local/share/pipx/venvs/playwright/lib")
            # Find site-packages under any python3.x directory (version-independent)
            _candidates = _g.glob(os.path.join(_pw_venv_lib, "python3.*", "site-packages"))
            for _pw_site in _candidates:
                if os.path.isdir(_pw_site) and _pw_site not in _sys.path:
                    _sys.path.insert(0, _pw_site)
                    break
            from playwright.sync_api import sync_playwright
        except ImportError:
            return "Playwright not installed. Run: pipx install playwright && playwright install chromium"
        if _pw_browser is None or not _pw_browser.is_connected():
            _pw_instance = sync_playwright().start()
            _pw_browser  = _pw_instance.chromium.launch(headless=headless)
        if _pw_page is None or _pw_page.is_closed():
            _pw_page = _pw_browser.new_page()
        return None

    if action == "close":
        try:
            if _pw_browser:
                _pw_browser.close()
            if _pw_instance:
                _pw_instance.stop()
        except Exception:
            pass
        _pw_instance = _pw_browser = _pw_page = None
        return "Browser session closed."

    err = _ensure_browser()
    if err:
        return err

    try:
        if action == "navigate":
            url = args.get("url", "")
            if not url:
                return "url required for navigate"
            _pw_page.goto(url, wait_until="domcontentloaded", timeout=30000)
            _pw_page.wait_for_load_state("networkidle", timeout=10000)
            title = _pw_page.title()
            # Grab first ~1500 chars of visible text
            text = _pw_page.evaluate(
                "() => document.body?.innerText?.replace(/\\s+/g,' ').substring(0,1500) || ''"
            )
            return f"Navigated to: {_pw_page.url}\nTitle: {title}\n\nContent preview:\n{text}"

        elif action == "click":
            selector = args.get("selector")
            text_val  = args.get("text")
            if text_val:
                _pw_page.get_by_text(text_val, exact=False).first.click(timeout=8000)
            elif selector:
                _pw_page.locator(selector).first.click(timeout=8000)
            else:
                return "selector or text required for click"
            _pw_page.wait_for_load_state("networkidle", timeout=8000)
            return f"Clicked. Current URL: {_pw_page.url}"

        elif action == "type":
            selector = args.get("selector", "")
            text_val  = args.get("text", "")
            if not selector:
                return "selector required for type"
            _pw_page.locator(selector).first.fill(text_val, timeout=8000)
            return f"Typed into {selector}"

        elif action == "screenshot":
            import base64
            fd, path = tempfile.mkstemp(prefix="jarvis-browser-", suffix=".png")
            os.close(fd)
            _pw_page.screenshot(path=path, full_page=False)
            try:
                with open(path, "rb") as _f:
                    b64 = base64.b64encode(_f.read()).decode()
                return f"Screenshot saved: {path}\ndata:image/png;base64,{b64[:2000]}... (truncated for context)"
            except Exception:
                return f"Screenshot saved: {path}"

        elif action == "extract":
            selector = args.get("selector")
            if selector:
                els = _pw_page.locator(selector).all()
                texts = [e.inner_text() for e in els[:20]]
                return "\n".join(texts)
            else:
                return _pw_page.evaluate(
                    "() => document.body?.innerText?.replace(/\\s+/g,' ').substring(0,5000) || ''"
                )

        elif action == "evaluate":
            script = args.get("script", "")
            if not script:
                return "script required for evaluate"
            result = _pw_page.evaluate(script)
            return str(result)

        elif action == "scroll":
            direction = args.get("direction", "down")
            amount    = args.get("amount", 500)
            dy = amount if direction == "down" else -amount
            _pw_page.evaluate(f"window.scrollBy(0, {dy})")
            return f"Scrolled {direction} {amount}px"

        elif action == "back":
            _pw_page.go_back(wait_until="domcontentloaded", timeout=10000)
            return f"Went back. Current URL: {_pw_page.url}"

        elif action == "save_cookies":
            profile = args.get("profile", "default")
            cookie_dir = os.path.expanduser("~/.jarvis/browser_cookies")
            os.makedirs(cookie_dir, exist_ok=True)
            cookie_path = os.path.join(cookie_dir, f"{profile}.json")
            cookies = _pw_page.context.cookies()
            import json as _json
            with open(cookie_path, "w") as _f:
                _json.dump(cookies, _f)
            return f"Saved {len(cookies)} cookies to profile '{profile}' ({cookie_path})"

        elif action == "load_cookies":
            profile = args.get("profile", "default")
            cookie_path = os.path.expanduser(f"~/.jarvis/browser_cookies/{profile}.json")
            if not os.path.exists(cookie_path):
                return f"No saved cookies for profile '{profile}'. Use save_cookies first."
            import json as _json
            with open(cookie_path) as _f:
                cookies = _json.load(_f)
            _pw_page.context.add_cookies(cookies)
            return f"Loaded {len(cookies)} cookies from profile '{profile}'. Navigate to the site to use them."

        elif action == "list_profiles":
            cookie_dir = os.path.expanduser("~/.jarvis/browser_cookies")
            if not os.path.isdir(cookie_dir):
                return "No cookie profiles saved yet."
            import glob as _g, json as _json
            profiles = []
            for f in sorted(_g.glob(os.path.join(cookie_dir, "*.json"))):
                name = os.path.basename(f).replace(".json", "")
                try:
                    cookies = _json.load(open(f))
                    profiles.append(f"  {name}: {len(cookies)} cookies")
                except Exception:
                    profiles.append(f"  {name}: (unreadable)")
            return "Saved browser profiles:\n" + "\n".join(profiles) if profiles else "No profiles found."

        else:
            return f"Unknown action: {action}"

    except Exception as e:
        return f"Browser error: {e}"
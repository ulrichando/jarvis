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
                "IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, `head`, `tail`, `sed`, `awk`, or `echo` commands, "
                "unless explicitly instructed or after you have verified that a dedicated tool cannot accomplish your task. "
                "Instead, use the appropriate dedicated tool as this will provide a much better experience for the user:\n"
                "\n"
                "- File search: Use Glob (NOT find or ls)\n"
                "- Content search: Use Grep (NOT grep or rg)\n"
                "- Read files: Use read_file (NOT cat/head/tail)\n"
                "- Edit files: Use edit_file (NOT sed/awk)\n"
                "- Write files: Use write_file (NOT echo >/cat <<EOF)\n"
                "- Communication: Output text directly (NOT echo/printf)\n"
                "While the bash tool can do similar things, it's better to use the built-in tools as they provide "
                "a better user experience and make it easier to review tool calls and give permission.\n"
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
                "- You can use the `run_in_background` parameter to run the command in the background. Only use "
                "this if you don't need the result immediately and are OK being notified when the command completes later.\n"
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
                        "description": "Absolute or relative file path to read",
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
            "name": "Glob",
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
            "name": "Grep",
            "description": (
                "A powerful content search tool built on ripgrep.\n"
                "\n"
                "ALWAYS use Grep for content search tasks. NEVER invoke `grep` or `rg` as a bash command. "
                "This tool has been optimized for correct permissions and access.\n"
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
    # computer_use removed from default tools — LLM should use bash/xdotool instead.
    # The execute_tool handler still supports it if explicitly needed.
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
            "name": "see",
            "description": "Look through the user's webcam. Returns a detailed description of what you see. Use when the user asks 'what do you see', 'look at me', 'what am I holding', 'describe what's in front of you', or any visual question about the physical world. Costs an API call — only use when the user asks.",
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
                "- scout: Read-only exploration -- searches, reads files, analyzes code (Tools: read_file, search_files, web_search, web_fetch, think)\n"
                "- worker: Full access execution -- can read, write, edit, run commands (Tools: All tools)\n"
                "- planner: Analysis and planning only -- produces plans without executing (Tools: read_file, search_files, think)\n"
                "- verifier: Post-work reviewer -- verifies correctness, runs tests, returns PASS/FAIL\n"
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
                            },
                            "required": ["id", "content", "status"],
                        },
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
                "- cell_type: 'code' or 'markdown' (for add_cell, defaults to 'code')"
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
            "name": "EnterPlanMode",
            "description": (
                "Switch to plan mode for non-trivial implementation tasks. "
                "In plan mode you can explore the codebase and design an approach for user approval "
                "before writing code. Use when the task involves new features, multiple approaches, "
                "architectural decisions, or multi-file changes. Requires user approval to enter."
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
            "name": "ExitPlanMode",
            "description": (
                "Exit plan mode after writing your plan. Signals that you are done planning "
                "and ready for the user to review and approve your implementation plan. "
                "Only use when you have finished writing your plan and are ready for approval."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    # ── Worktree Tools ─────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "EnterWorktree",
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
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ExitWorktree",
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
            "name": "SendMessage",
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
            "name": "TaskCreate",
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
            "name": "TaskGet",
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
            "name": "TaskList",
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
            "name": "TaskStop",
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
            "name": "TaskUpdate",
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
            "name": "TaskOutput",
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
            "name": "TeamCreate",
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
            "name": "TeamDelete",
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
            "name": "Skill",
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
            "name": "ConfigTool",
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
            "name": "LSP",
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
            "name": "Sleep",
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
                },
                "required": ["duration_ms"],
            },
        },
    },
    # ── Schedule/Cron Tool ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "ScheduleCron",
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
            "name": "BriefTool",
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
            "name": "ListMcpResources",
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
            "name": "RemoteTrigger",
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
    # ── SMS / Phone messaging via KDE Connect ──────────────────────────
    {
        "type": "function",
        "function": {
            "name": "send_sms",
            "description": (
                "Send a text message (SMS) to a phone number via KDE Connect.\n"
                "Requires a paired Android phone running KDE Connect.\n"
                "Use this when Ulrich asks you to text someone, send a message, or SMS.\n"
                "If no device is paired, tell the user to pair their phone first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {
                        "type": "string",
                        "description": "Phone number to send to (e.g. '+1234567890')",
                    },
                    "message": {
                        "type": "string",
                        "description": "The text message to send",
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
                "- navigate: Go to a URL. Returns page title and brief content summary.\n"
                "- click: Click an element by CSS selector or text content.\n"
                "- type: Type text into an input field (selector required).\n"
                "- screenshot: Take a screenshot. Returns file path.\n"
                "- extract: Extract text from the page or a specific selector.\n"
                "- evaluate: Run JavaScript in the page context. Returns the result.\n"
                "- scroll: Scroll the page (direction: up/down, amount in pixels).\n"
                "- back: Go back in history.\n"
                "- close: Close the browser session.\n"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["navigate", "click", "type", "screenshot", "extract",
                                 "evaluate", "scroll", "back", "close"],
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
]


# ── open_url / switch_channel broadcast hooks — set by web_server ────
_open_url_hook = None       # callable(url: str) → None
_switch_channel_hook = None  # callable(target: str) → None
_channel_state_hook = None   # callable() → dict  (returns current channel state)


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


# ── Tool Execution ──────────────────────────────────────────────────

# Tools allowed in plan/read-only mode
READONLY_TOOLS = {
    "read_file", "Glob", "Grep", "web_search", "web_fetch", "think", "dispatch",
    "view_screen", "see", "tool_search", "ask_user", "todo_write",
    "TaskList", "TaskGet", "TaskOutput", "ListMcpResources", "LSP",
    "ConfigTool", "BriefTool", "EnterPlanMode", "ExitPlanMode",
    "network_scan",
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
        elif name == "Glob":
            return _exec_glob(args)
        elif name == "Grep":
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
        elif name == "EnterPlanMode":
            return "__PLAN_MODE_ENTER__"  # Handled by agent loop
        elif name == "ExitPlanMode":
            return "__PLAN_MODE_EXIT__"  # Handled by agent loop
        # ── Worktree ───────────────────────────────────────────────────
        elif name == "EnterWorktree":
            return "__WORKTREE_ENTER__"  # Handled by agent loop
        elif name == "ExitWorktree":
            return "__WORKTREE_EXIT__"  # Handled by agent loop
        # ── Multi-Agent ────────────────────────────────────────────────
        elif name == "SendMessage":
            return "__SEND_MESSAGE__"  # Handled by agent loop
        # ── Task Management ────────────────────────────────────────────
        elif name == "TaskCreate":
            return _exec_task_create(args)
        elif name == "TaskGet":
            return _exec_task_get(args)
        elif name == "TaskList":
            return _exec_task_list(args)
        elif name == "TaskStop":
            return _exec_task_stop(args)
        elif name == "TaskUpdate":
            return _exec_task_update(args)
        elif name == "TaskOutput":
            return _exec_task_output(args)
        # ── Team Tools ─────────────────────────────────────────────────
        elif name == "TeamCreate":
            return "__TEAM_CREATE__"  # Handled by agent loop
        elif name == "TeamDelete":
            return "__TEAM_DELETE__"  # Handled by agent loop
        # ── Skill ──────────────────────────────────────────────────────
        elif name == "Skill":
            return "__SKILL__"  # Handled by agent loop
        # ── Config ─────────────────────────────────────────────────────
        elif name == "ConfigTool":
            return _exec_config(args)
        # ── LSP ────────────────────────────────────────────────────────
        elif name == "LSP":
            return "__LSP__"  # Handled by agent loop (requires LSP server)
        # ── Sleep ──────────────────────────────────────────────────────
        elif name == "Sleep":
            return _exec_sleep(args)
        # ── Cron/Schedule ──────────────────────────────────────────────
        elif name == "ScheduleCron":
            return "__CRON__"  # Handled by agent loop
        # ── BriefTool (SendUserMessage) ────────────────────────────────
        elif name == "BriefTool":
            return args.get("message", "")
        # ── MCP Resources ─────────────────────────────────────────────
        elif name == "ListMcpResources":
            return _exec_list_mcp_resources(args)
        # ── Remote Trigger ─────────────────────────────────────────────
        elif name == "RemoteTrigger":
            return "__REMOTE_TRIGGER__"  # Handled by agent loop
        elif name == "send_sms":
            return _exec_send_sms(args)
        elif name == "network_scan":
            return _exec_network_scan(args)
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
            # Semantic exit code interpretation (grep 1 = no matches, not error, etc.)
            sem = _interpret_bash_result(command, result["returncode"], result.get("stdout", ""), result.get("stderr", ""))
            if sem.message:
                prefix += f" ({sem.message})"
            # Destructive warning (informational)
            if destructive_warning:
                prefix += f"\n{destructive_warning}"
            return f"{prefix}\n{output}"
        except Exception:
            pass  # Fall through to unsandboxed execution

    # Original unsandboxed execution (fallback)
    try:
        # Full root access: wrap with sudo when NO_SANDBOX=1 and not already root/sudo
        _cmd_to_run = command
        _sudo_available = __import__('shutil').which("sudo") is not None
        if (os.environ.get("JARVIS_NO_SANDBOX")
                and _sudo_available
                and os.geteuid() != 0
                and not command.strip().startswith("sudo")):
            _cmd_to_run = f"sudo -E -n sh -c {__import__('shlex').quote(command)}"

        result = subprocess.run(
            _cmd_to_run, shell=True, capture_output=True, text=True,
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
        # Semantic exit code interpretation
        sem = _interpret_bash_result(command, result.returncode, result.stdout or "", result.stderr or "")
        prefix = f"exit_code={result.returncode}"
        if sem.message:
            prefix += f" ({sem.message})"
        if destructive_warning:
            prefix += f"\n{destructive_warning}"
        return f"{prefix}\n{output}"
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
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        client.close()

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

        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)

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
                if len(rows) >= 100:
                    try:
                        total = cursor.execute(f"SELECT COUNT(*) FROM ({query}) AS _c").fetchone()[0]
                    except Exception:
                        total = "100+"  # complex query (JOIN/CTE/GROUP BY) — approximate
                else:
                    total = len(rows)
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
                import urllib.parse as _urlparse
                # Parse connection string: mysql://user:pass@host:port/dbname
                # or fall back to treating it as a host name
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


def _exec_todo_write(args: dict) -> str:
    """Update the session todo list."""
    global _todo_list
    todos = args.get("todos", [])
    if not isinstance(todos, list):
        return "Invalid todos format. Expected a list of todo items."

    _todo_list = todos

    # Format summary
    pending = sum(1 for t in todos if t.get("status") == "pending")
    in_progress = sum(1 for t in todos if t.get("status") == "in_progress")
    completed = sum(1 for t in todos if t.get("status") == "completed")

    lines = [f"Todo list updated: {pending} pending, {in_progress} in progress, {completed} completed\n"]
    for t in todos:
        status_icon = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(t.get("status", ""), "[?]")
        content = t.get("content", "(no description)")
        lines.append(f"  {status_icon} {content}")

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
            cells[cell_index]["source"] = new_source.split("\n") if new_source else []
            # Normalize source to list of lines with newlines
            lines = new_source.split("\n")
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

        else:
            return f"Unknown action: {action}. Use edit_cell, add_cell, or delete_cell."

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
        # Verify pattern doesn't escape the search path via ../
        resolved_base = os.path.realpath(path)
        matches = _glob.glob(full_pattern, recursive=True)
        # Filter out matches that escape the base path (add sep so /tmp/foo doesn't match /tmp/foobar)
        _base_prefix = resolved_base.rstrip(os.sep) + os.sep
        matches = [m for m in matches
                   if os.path.realpath(m) == resolved_base
                   or os.path.realpath(m).startswith(_base_prefix)]
        # Sort by modification time (newest first)
        matches.sort(key=lambda f: os.path.getmtime(f) if os.path.exists(f) else 0, reverse=True)
        matches = matches[:250]  # Cap results
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
            head_limit=min(args.get("head_limit", 250), 10000),
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
    """Sleep for a specified duration."""
    import time
    duration_ms = args.get("duration_ms", 1000)
    duration_s = min(duration_ms / 1000.0, 300)  # Cap at 5 minutes
    time.sleep(duration_s)
    return f"Slept for {duration_s:.1f}s"


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

def _exec_send_sms(args: dict) -> str:
    """Send SMS via KDE Connect to a paired Android phone."""
    phone = args.get("phone_number", "").strip()
    message = args.get("message", "").strip()

    if not phone:
        return "No phone number provided."
    if not message:
        return "No message provided."

    # Find paired + reachable device
    try:
        result = subprocess.run(
            ["kdeconnect-cli", "-a", "--id-only"],
            capture_output=True, text=True, timeout=5,
        )
        devices = [d.strip() for d in result.stdout.strip().split("\n") if d.strip()]
    except FileNotFoundError:
        return "kdeconnect-cli not installed. Install KDE Connect: sudo apt install kdeconnect"
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
            "No paired phone found. To set up:\n"
            "1. Install 'KDE Connect' app on your Android phone\n"
            "2. Make sure phone and PC are on the same WiFi\n"
            "3. Run: kdeconnect-cli -l   (should show your phone)\n"
            "4. Pair from the phone app or: kdeconnect-cli --pair -d <device_id>"
        )

    device_id = devices[0]

    # Send the SMS
    try:
        result = subprocess.run(
            [
                "kdeconnect-cli",
                "-d", device_id,
                "--send-sms", message,
                "--destination", phone,
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return f"SMS sent to {phone}: \"{message}\""
        else:
            err = result.stderr.strip() or result.stdout.strip()
            return f"Failed to send SMS: {err}"
    except Exception as e:
        return f"Error sending SMS: {e}"


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
            fd, path = tempfile.mkstemp(prefix="jarvis-browser-", suffix=".png")
            os.close(fd)
            _pw_page.screenshot(path=path, full_page=False)
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

        else:
            return f"Unknown action: {action}"

    except Exception as e:
        return f"Browser error: {e}"
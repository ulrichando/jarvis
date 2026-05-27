"""Terminal Tool — execute shell commands in the local environment.

JARVIS voice-agent variant: local execution only. No container backends
(Docker / Modal / SSH / Singularity / Daytona / Vercel) — the voice agent
runs on the local machine alongside the user. Multi-backend execution is
not ported here; if remote execution is ever needed, add it with
JARVIS-native env-var names.

Registered tool name: ``terminal``

Faithful behavioral port of the upstream terminal tool (local path only).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional

from .ansi_strip import strip_ansi
from .command_safety import scan_command
from .registry import registry, tool_error

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Hard cap on foreground timeout; override via TERMINAL_MAX_FOREGROUND_TIMEOUT.
def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid value for %s: %r — using default %d", name, raw, default)
        return default


FOREGROUND_MAX_TIMEOUT = _parse_int_env("TERMINAL_MAX_FOREGROUND_TIMEOUT", 600)
_DEFAULT_TIMEOUT = _parse_int_env("TERMINAL_TIMEOUT", 180)

# Output truncation limit (chars). Keeps context window safe.
_MAX_OUTPUT_CHARS = _parse_int_env("TERMINAL_MAX_OUTPUT_CHARS", 100_000)


# ---------------------------------------------------------------------------
# Workdir validation — allowlist of safe characters only
# ---------------------------------------------------------------------------

_WORKDIR_SAFE_RE = re.compile(r'^[A-Za-z0-9/\\:_\-.~ +@=,]+$')


def _validate_workdir(workdir: str) -> Optional[str]:
    """Return None if safe, or an error message if dangerous."""
    if not workdir:
        return None
    if not _WORKDIR_SAFE_RE.match(workdir):
        for ch in workdir:
            if not _WORKDIR_SAFE_RE.match(ch):
                return (
                    f"Blocked: workdir contains disallowed character {repr(ch)}. "
                    "Use a simple filesystem path without shell metacharacters."
                )
        return "Blocked: workdir contains disallowed characters."
    return None


# ---------------------------------------------------------------------------
# Exit code interpretation
# ---------------------------------------------------------------------------

def _interpret_exit_code(command: str, exit_code: int) -> Optional[str]:
    """Return a human-readable note when a non-zero exit code is non-erroneous."""
    if exit_code == 0:
        return None
    segments = re.split(r'\s*(?:\|\||&&|[|;])\s*', command)
    last_segment = (segments[-1] if segments else command).strip()
    words = last_segment.split()
    base_cmd = ""
    for w in words:
        if "=" in w and not w.startswith("-"):
            continue
        base_cmd = w.split("/")[-1]
        break
    if not base_cmd:
        return None
    semantics: dict[str, dict[int, str]] = {
        "grep":  {1: "No matches found (not an error)"},
        "egrep": {1: "No matches found (not an error)"},
        "fgrep": {1: "No matches found (not an error)"},
        "rg":    {1: "No matches found (not an error)"},
        "ag":    {1: "No matches found (not an error)"},
        "ack":   {1: "No matches found (not an error)"},
        "diff":  {1: "Files differ (expected, not an error)"},
        "colordiff": {1: "Files differ (expected, not an error)"},
        "find":  {1: "Some directories were inaccessible (partial results may still be valid)"},
        "test":  {1: "Condition evaluated to false (expected, not an error)"},
        "[":     {1: "Condition evaluated to false (expected, not an error)"},
        "curl":  {
            6: "Could not resolve host",
            7: "Failed to connect to host",
            22: "HTTP response code indicated error (e.g. 404, 500)",
            28: "Operation timed out",
        },
        "git":   {1: "Non-zero exit (often normal — e.g. 'git diff' returns 1 when files differ)"},
    }
    cmd_semantics = semantics.get(base_cmd)
    if cmd_semantics and exit_code in cmd_semantics:
        return cmd_semantics[exit_code]
    return None


# ---------------------------------------------------------------------------
# Long-lived / shell-background-wrapper guards
# ---------------------------------------------------------------------------

_SHELL_LEVEL_BACKGROUND_RE = re.compile(
    r"(?:^|[;&|]\s*|&&\s*|\|\|\s*|\$\(\s*)(?:nohup|disown|setsid)\b",
    re.IGNORECASE | re.MULTILINE,
)
_INLINE_BACKGROUND_AMP_RE = re.compile(r"\s&\s")
_TRAILING_BACKGROUND_AMP_RE = re.compile(r"\s&\s*(?:#.*)?$")

_LONG_LIVED_FOREGROUND_PATTERNS = (
    re.compile(r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start|serve|watch)\b", re.IGNORECASE),
    re.compile(r"\bdocker\s+compose\s+up\b", re.IGNORECASE),
    re.compile(r"\bnext\s+dev\b", re.IGNORECASE),
    re.compile(r"\bvite(?:\s|$)", re.IGNORECASE),
    re.compile(r"\bnodemon\b", re.IGNORECASE),
    re.compile(r"\buvicorn\b", re.IGNORECASE),
    re.compile(r"\bgunicorn\b", re.IGNORECASE),
    re.compile(r"\bpython(?:3)?\s+-m\s+http\.server\b", re.IGNORECASE),
)


def _strip_quotes(command: str) -> str:
    result = re.sub(r"'[^']*'", "''", command)
    result = re.sub(r'"(?:[^"\\]|\\.)*"', '""', result)
    result = re.sub(r"`[^`]*`", "``", result)
    return result


def _looks_like_help_or_version(command: str) -> bool:
    normalized = " ".join(command.lower().split())
    return (
        " --help" in normalized
        or normalized.endswith(" -h")
        or " --version" in normalized
        or normalized.endswith(" -v")
    )


def _foreground_background_guidance(command: str) -> Optional[str]:
    """Suggest background mode when a foreground command looks long-lived."""
    if _looks_like_help_or_version(command):
        return None
    unquoted = _strip_quotes(command)
    if _SHELL_LEVEL_BACKGROUND_RE.search(unquoted):
        return (
            "Foreground command uses shell-level background wrappers (nohup/disown/setsid). "
            "Use terminal(background=true) to run long-lived processes."
        )
    if _INLINE_BACKGROUND_AMP_RE.search(unquoted) or _TRAILING_BACKGROUND_AMP_RE.search(unquoted):
        return (
            "Foreground command uses '&' backgrounding. Use terminal(background=true) for "
            "long-lived processes, then run health checks in follow-up commands."
        )
    for pattern in _LONG_LIVED_FOREGROUND_PATTERNS:
        if pattern.search(unquoted):
            return (
                "This foreground command appears to start a long-lived server/watch process. "
                "Run it with background=true, verify readiness (health endpoint/log signal), "
                "then execute tests in a separate command."
            )
    return None


# ---------------------------------------------------------------------------
# Active background-process registry (minimal — tracks pid + session_id)
# ---------------------------------------------------------------------------

_bg_lock = threading.Lock()
_bg_processes: dict[str, dict] = {}  # session_id → {pid, proc, command, started}
_bg_counter = 0


def _next_session_id() -> str:
    global _bg_counter
    with _bg_lock:
        _bg_counter += 1
        return f"jarvis-bg-{_bg_counter}"


def _poll_bg_process(session_id: str) -> Optional[dict]:
    """Return status dict for a background process, or None if unknown."""
    with _bg_lock:
        entry = _bg_processes.get(session_id)
    if entry is None:
        return None
    proc: subprocess.Popen = entry["proc"]
    rc = proc.poll()
    return {
        "session_id": session_id,
        "pid": entry["pid"],
        "command": entry["command"],
        "running": rc is None,
        "exit_code": rc,
        "started": entry["started"],
    }


# ---------------------------------------------------------------------------
# Core execution
# ---------------------------------------------------------------------------

def _run_local(
    command: str,
    timeout: int,
    cwd: str,
) -> dict:
    """Execute *command* locally, return {output, returncode}."""
    env = os.environ.copy()
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            executable="/bin/bash",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=env,
            text=True,
            errors="replace",
        )
        try:
            output, _ = proc.communicate(timeout=timeout)
            return {"output": output or "", "returncode": proc.returncode}
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                out, _ = proc.communicate(timeout=5)
            except Exception:
                out = ""
            return {
                "output": out or "",
                "returncode": 124,
                "_timeout": True,
            }
    except Exception as exc:
        return {
            "output": "",
            "returncode": -1,
            "_error": str(exc),
        }


def terminal_tool(
    command: str,
    background: bool = False,
    timeout: Optional[int] = None,
    workdir: Optional[str] = None,
) -> str:
    """Execute a shell command in the local environment.

    Args:
        command: Shell command to run.
        background: Run in background (returns session_id immediately).
        timeout: Max seconds to wait for foreground commands.
        workdir: Working directory (absolute path preferred).

    Returns:
        JSON string with ``output``, ``exit_code``, ``error`` fields.
    """
    try:
        if not isinstance(command, str):
            return json.dumps({
                "output": "",
                "exit_code": -1,
                "error": f"Invalid command: expected string, got {type(command).__name__}",
                "status": "error",
            }, ensure_ascii=False)

        effective_timeout = timeout or _DEFAULT_TIMEOUT

        # Reject foreground timeout above hard cap.
        if not background and timeout and timeout > FOREGROUND_MAX_TIMEOUT:
            return json.dumps({
                "error": (
                    f"Foreground timeout {timeout}s exceeds the maximum of "
                    f"{FOREGROUND_MAX_TIMEOUT}s. Use background=true for long-running commands."
                ),
            }, ensure_ascii=False)

        # Guardrail: long-lived server/watch commands should run as background.
        if not background:
            guidance = _foreground_background_guidance(command)
            if guidance:
                return json.dumps({
                    "output": "",
                    "exit_code": -1,
                    "error": guidance,
                    "status": "error",
                }, ensure_ascii=False)

        # Validate workdir.
        cwd = workdir or os.getcwd()
        if workdir:
            err = _validate_workdir(workdir)
            if err:
                return json.dumps({
                    "output": "",
                    "exit_code": -1,
                    "error": err,
                    "status": "blocked",
                }, ensure_ascii=False)
            # Resolve ~ and relative paths.
            cwd = str(Path(workdir).expanduser().resolve())

        # Safety scan — block catastrophic commands before any execution.
        denial = scan_command(command)
        if denial:
            return json.dumps({
                "output": "",
                "exit_code": -1,
                "error": denial,
                "status": "blocked",
            }, ensure_ascii=False)

        if background:
            session_id = _next_session_id()
            try:
                env = os.environ.copy()
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    executable="/bin/bash",
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=cwd,
                    env=env,
                )
                with _bg_lock:
                    _bg_processes[session_id] = {
                        "pid": proc.pid,
                        "proc": proc,
                        "command": command,
                        "started": time.time(),
                    }
                return json.dumps({
                    "output": "Background process started",
                    "session_id": session_id,
                    "pid": proc.pid,
                    "exit_code": 0,
                    "error": None,
                }, ensure_ascii=False)
            except Exception as exc:
                return json.dumps({
                    "output": "",
                    "exit_code": -1,
                    "error": f"Failed to start background process: {exc}",
                }, ensure_ascii=False)

        # Foreground execution.
        result = _run_local(command, effective_timeout, cwd)

        if result.get("_timeout"):
            return json.dumps({
                "output": strip_ansi(result.get("output", "")),
                "exit_code": 124,
                "error": f"Command timed out after {effective_timeout} seconds",
            }, ensure_ascii=False)

        if result.get("_error"):
            return json.dumps({
                "output": "",
                "exit_code": result.get("returncode", -1),
                "error": f"Command execution failed: {result['_error']}",
            }, ensure_ascii=False)

        output = result.get("output", "")
        returncode = result.get("returncode", 0)

        # Truncate oversized output.
        if len(output) > _MAX_OUTPUT_CHARS:
            head = int(_MAX_OUTPUT_CHARS * 0.4)
            tail = _MAX_OUTPUT_CHARS - head
            omitted = len(output) - head - tail
            output = (
                output[:head]
                + f"\n\n... [OUTPUT TRUNCATED — {omitted} chars omitted out of {len(output)} total] ...\n\n"
                + output[-tail:]
            )

        output = strip_ansi(output)

        exit_note = _interpret_exit_code(command, returncode)
        result_dict: dict = {
            "output": output.strip() if output else "",
            "exit_code": returncode,
            "error": None,
        }
        if exit_note:
            result_dict["exit_code_meaning"] = exit_note

        return json.dumps(result_dict, ensure_ascii=False)

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.error("terminal_tool exception:\n%s", tb)
        return json.dumps({
            "output": "",
            "exit_code": -1,
            "error": f"Failed to execute command: {exc}",
            "status": "error",
        }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Schema + registration
# ---------------------------------------------------------------------------

_TOOL_DESCRIPTION = """\
Execute shell commands on the local Linux environment. Filesystem persists between calls.

WHEN TO USE: builds, installs, git, processes, scripts, network checks, package managers,
launching apps (`setsid <app> &`), and any one-shot command the user asks for by name
("run pytest", "git status", "kill chrome", "open Discord").

DO NOT reply with phrases like "Done", "Running it now", "I've executed that" UNLESS you
have already issued the corresponding terminal call this turn AND seen the result.
Tool first, words after — narrating success without a tool call is confab.

Do NOT use cat/head/tail to read files — use read_file instead.
Do NOT use grep/rg/find to search — use search_files instead.
Do NOT use sed/awk to edit files — use patch instead.
Do NOT use echo/cat heredoc to create files — use write_file instead.

Foreground (default): commands return when done. Set timeout=300 for long builds.
Background: set background=true to return immediately with a session_id.
Working directory: use workdir= for per-command cwd.
"""

TERMINAL_SCHEMA = {
    "name": "terminal",
    "description": _TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute",
            },
            "background": {
                "type": "boolean",
                "description": (
                    "Run the command in the background and return a session_id immediately. "
                    "Use for long-lived servers or tasks that take more than a minute."
                ),
                "default": False,
            },
            "timeout": {
                "type": "integer",
                "description": (
                    f"Max seconds to wait for foreground commands (default: {_DEFAULT_TIMEOUT}, "
                    f"max: {FOREGROUND_MAX_TIMEOUT}). Returns instantly when done."
                ),
                "minimum": 1,
            },
            "workdir": {
                "type": "string",
                "description": "Working directory for this command (absolute path).",
            },
        },
        "required": ["command"],
    },
}


def _handle_terminal(args: dict, **kw) -> str:
    return terminal_tool(
        command=args.get("command", ""),
        background=args.get("background", False),
        timeout=args.get("timeout"),
        workdir=args.get("workdir"),
    )


registry.register(
    name="terminal",
    schema=TERMINAL_SCHEMA,
    handler=_handle_terminal,
    toolset="terminal",
    is_async=False,
    emoji="💻",
    max_result_size_chars=100_000,
)

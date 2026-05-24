"""Execute Code Tool — run a Python script in a sandboxed subprocess.

Registered tool name: ``execute_code``

Lets the supervisor write a Python script that collapses multi-step tool
chains into a single inference turn. The script runs in a child process;
tool calls travel back to the parent over a Unix domain socket (UDS) RPC.

Architecture:
1. Parent writes a ``jarvis_tools.py`` stub module to a temp dir.
2. Parent opens a UDS and starts an RPC listener thread.
3. Parent spawns a child process running the LLM's script.
4. Tool calls from the script travel over the UDS back to the parent
   for dispatch; only stdout is returned to the LLM.

JARVIS-only adaptation notes:
- Local execution only (no container / SSH / Docker backends).
- Sandbox env vars use JARVIS_* names; home dir via ``tools.runtime``.
- Allowed sandbox tools: terminal, read_file, write_file, patch, search_files.
- Timeout: 300 s. Max tool calls per run: 50. Stdout cap: 50 KB.
- Windows not supported (UDS requires POSIX).

Ported from upstream code_execution_tool.py; all upstream platform tokens scrubbed.
"""
from __future__ import annotations

import base64
import functools
import json
import logging
import os
import platform
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

import psutil

from .ansi_strip import strip_ansi
from .registry import registry, tool_error
from .runtime import get_jarvis_home, get_subprocess_home

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"

# On Windows UDS is unreliable; fall back to loopback TCP.
_USE_TCP_RPC = _IS_WINDOWS

# Resource limits (overridable via env vars)
DEFAULT_TIMEOUT = int(os.environ.get("JARVIS_CODE_EXEC_TIMEOUT", "300"))
DEFAULT_MAX_TOOL_CALLS = int(os.environ.get("JARVIS_CODE_EXEC_MAX_TOOL_CALLS", "50"))
MAX_STDOUT_BYTES = 50_000
MAX_STDERR_BYTES = 10_000

# Tools reachable from sandbox scripts
SANDBOX_ALLOWED_TOOLS = frozenset(
    ["terminal", "read_file", "write_file", "patch", "search_files"]
)

# Env var scrubbing for the child process
_SAFE_ENV_PREFIXES = (
    "PATH", "HOME", "USER", "LANG", "LC_", "TERM",
    "TMPDIR", "TMP", "TEMP", "SHELL", "LOGNAME",
    "XDG_", "PYTHONPATH", "VIRTUAL_ENV", "CONDA",
    "JARVIS_",
)
_SECRET_SUBSTRINGS = (
    "KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "PASSWD", "AUTH",
)


# ---------------------------------------------------------------------------
# Env scrubbing
# ---------------------------------------------------------------------------

def _scrub_child_env(source_env: dict) -> dict:
    """Return a sanitized environment for the sandbox subprocess.

    Secret-substring names are blocked. Safe-prefix names pass. Passthrough
    vars declared via JARVIS_CODE_EXEC_ENV_PASSTHROUGH (comma-separated) are
    always included.
    """
    passthrough = set(
        v.strip() for v in
        os.environ.get("JARVIS_CODE_EXEC_ENV_PASSTHROUGH", "").split(",")
        if v.strip()
    )
    scrubbed: dict = {}
    for k, v in source_env.items():
        if k in passthrough:
            scrubbed[k] = v
            continue
        if any(s in k.upper() for s in _SECRET_SUBSTRINGS):
            continue
        if any(k.startswith(p) for p in _SAFE_ENV_PREFIXES):
            scrubbed[k] = v
    return scrubbed


# ---------------------------------------------------------------------------
# jarvis_tools.py stub generator
# ---------------------------------------------------------------------------

_TOOL_STUBS = {
    "terminal": (
        "terminal",
        "command: str, timeout: int = None, workdir: str = None",
        '"""Run a shell command (foreground only). Returns dict with "output" and "exit_code"."""',
        '{"command": command, "timeout": timeout, "workdir": workdir}',
    ),
    "read_file": (
        "read_file",
        "path: str, offset: int = 1, limit: int = 500",
        '"""Read a file (1-indexed lines). Returns dict with "content" and "total_lines"."""',
        '{"path": path, "offset": offset, "limit": limit}',
    ),
    "write_file": (
        "write_file",
        "path: str, content: str",
        '"""Write content to a file (always overwrites). Returns dict with status."""',
        '{"path": path, "content": content}',
    ),
    "search_files": (
        "search_files",
        'pattern: str, target: str = "content", path: str = ".", file_glob: str = None, limit: int = 50, offset: int = 0',
        '"""Search file contents or find files by name. Returns dict with "matches"."""',
        '{"pattern": pattern, "target": target, "path": path, "file_glob": file_glob, "limit": limit, "offset": offset}',
    ),
    "patch": (
        "patch",
        'path: str = None, old_string: str = None, new_string: str = None, replace_all: bool = False',
        '"""Targeted find-and-replace in a file. Returns dict with status."""',
        '{"path": path, "old_string": old_string, "new_string": new_string, "replace_all": replace_all}',
    ),
}

_COMMON_HELPERS = '''\

# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def json_parse(text: str):
    """Parse JSON tolerant of control characters (strict=False).
    Use instead of json.loads() for terminal() output that may have
    raw tabs/newlines in strings."""
    return json.loads(text, strict=False)


def shell_quote(s: str) -> str:
    """Shell-escape a string for safe interpolation into terminal() commands."""
    return shlex.quote(s)


def retry(fn, max_attempts=3, delay=2):
    """Retry fn up to max_attempts times with exponential backoff."""
    last_err = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if attempt < max_attempts - 1:
                time.sleep(delay * (2 ** attempt))
    raise last_err

'''

_UDS_TRANSPORT_HEADER = '''\
"""Auto-generated JARVIS tools RPC stubs."""
import json, os, socket, shlex, threading, time

_sock = None
_call_lock = threading.Lock()
''' + _COMMON_HELPERS + '''\

def _connect():
    global _sock
    if _sock is None:
        endpoint = os.environ["JARVIS_RPC_SOCKET"]
        if endpoint.startswith("tcp://"):
            _host_port = endpoint[len("tcp://"):]
            _host, _, _port = _host_port.rpartition(":")
            _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _sock.connect((_host or "127.0.0.1", int(_port)))
        else:
            _sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            _sock.connect(endpoint)
        _sock.settimeout(300)
    return _sock

def _call(tool_name, args):
    request = json.dumps({"tool": tool_name, "args": args}) + "\\n"
    with _call_lock:
        conn = _connect()
        conn.sendall(request.encode())
        buf = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                raise RuntimeError("JARVIS agent process disconnected")
            buf += chunk
            if buf.endswith(b"\\n"):
                break
    raw = buf.decode().strip()
    result = json.loads(raw)
    if isinstance(result, str):
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return result
    return result

'''


def _generate_jarvis_tools_module(enabled_tools: List[str]) -> str:
    """Build the source for the jarvis_tools.py stub module."""
    to_generate = sorted(SANDBOX_ALLOWED_TOOLS & set(enabled_tools))
    stubs = []
    for tool_name in to_generate:
        if tool_name not in _TOOL_STUBS:
            continue
        func_name, sig, doc, args_expr = _TOOL_STUBS[tool_name]
        stubs.append(
            f"def {func_name}({sig}):\n"
            f"    {doc}\n"
            f"    return _call({func_name!r}, {args_expr})\n"
        )
    return _UDS_TRANSPORT_HEADER + "\n".join(stubs)


# ---------------------------------------------------------------------------
# RPC server (runs in a parent thread)
# ---------------------------------------------------------------------------

_TERMINAL_BLOCKED_PARAMS = {"background", "pty", "notify_on_complete"}


def _rpc_server_loop(
    server_sock: socket.socket,
    tool_call_log: list,
    tool_call_counter: list,
    max_tool_calls: int,
    allowed_tools: frozenset,
) -> None:
    """Accept one client connection and dispatch tool-call requests."""
    conn = None
    try:
        server_sock.settimeout(5)
        conn, _ = server_sock.accept()
        conn.settimeout(300)
        buf = b""
        while True:
            try:
                chunk = conn.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                call_start = time.monotonic()
                try:
                    request = json.loads(line.decode())
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    conn.sendall((tool_error(f"Invalid RPC request: {exc}") + "\n").encode())
                    continue

                tool_name = request.get("tool", "")
                tool_args = request.get("args", {})

                if tool_name not in allowed_tools:
                    available = ", ".join(sorted(allowed_tools))
                    resp = json.dumps({"error": f"Tool '{tool_name}' not available in execute_code. Available: {available}"})
                    conn.sendall((resp + "\n").encode())
                    continue

                if tool_call_counter[0] >= max_tool_calls:
                    resp = json.dumps({"error": f"Tool call limit reached ({max_tool_calls})."})
                    conn.sendall((resp + "\n").encode())
                    continue

                if tool_name == "terminal" and isinstance(tool_args, dict):
                    for param in _TERMINAL_BLOCKED_PARAMS:
                        tool_args.pop(param, None)

                # Dispatch through JARVIS registry
                try:
                    from tools.registry import registry as _registry
                    entry = _registry.get_entry(tool_name)
                    if entry is None:
                        result = json.dumps({"error": f"Tool '{tool_name}' not registered"})
                    else:
                        result = entry.handler(tool_args)
                        if not isinstance(result, str):
                            result = str(result) if result is not None else ""
                except Exception as exc:
                    logger.error("Sandbox tool call %s failed: %s", tool_name, exc, exc_info=True)
                    result = tool_error(str(exc))

                tool_call_counter[0] += 1
                duration = time.monotonic() - call_start
                tool_call_log.append({
                    "tool": tool_name,
                    "args_preview": str(tool_args)[:80],
                    "duration": round(duration, 2),
                })
                conn.sendall((result + "\n").encode())

    except socket.timeout:
        logger.debug("RPC listener socket timeout")
    except OSError as e:
        logger.debug("RPC listener socket error: %s", e)
    finally:
        if conn:
            try:
                conn.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Process group killer
# ---------------------------------------------------------------------------

def _kill_process_group(proc: subprocess.Popen, escalate: bool = False) -> None:
    """Kill the child and its process tree.

    Uses psutil so the implementation is the same on POSIX and Windows
    (psutil walks the parent/child relation via the OS APIs; no need for
    ``os.killpg`` which doesn't exist on Windows, or ``signal.SIGKILL``
    which also doesn't exist on Windows). Terminates → optionally
    escalates to kill() if the parent doesn't exit within 5 s.
    """
    try:
        try:
            parent = psutil.Process(proc.pid)
        except psutil.NoSuchProcess:
            return
        # Snapshot before we start terminating; children disappear as they exit.
        try:
            descendants = parent.children(recursive=True)
        except psutil.NoSuchProcess:
            descendants = []
        # Terminate descendants first (deepest-first), then the parent. On
        # POSIX terminate() = SIGTERM; on Windows = TerminateProcess.
        for child in reversed(descendants):
            try:
                child.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        try:
            parent.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        if escalate:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Anything still alive gets the hammer. On POSIX kill() =
                # SIGKILL; on Windows it's the same as terminate() (still
                # the hardest stop available).
                for child in reversed(descendants):
                    try:
                        if child.is_running():
                            child.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                try:
                    if parent.is_running():
                        parent.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
    except Exception as e:
        logger.debug("_kill_process_group failed: %s", e)
        try:
            proc.kill()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main execution path
# ---------------------------------------------------------------------------

def execute_code(code: str) -> str:
    """Run a Python script in a sandboxed child process.

    The child has access to a subset of JARVIS tools via RPC over a Unix
    domain socket. Only stdout is returned; intermediate tool results stay
    out of the LLM context window.
    """
    if _IS_WINDOWS:
        return json.dumps({
            "error": "execute_code uses Unix domain sockets and is not supported on Windows."
        })
    if not code or not code.strip():
        return tool_error("No code provided.")

    timeout = DEFAULT_TIMEOUT
    max_tool_calls = DEFAULT_MAX_TOOL_CALLS

    # All enabled tools become available as sandbox stubs (we allow all of
    # SANDBOX_ALLOWED_TOOLS since JARVIS always has terminal/file tools).
    sandbox_tools = SANDBOX_ALLOWED_TOOLS

    tmpdir = tempfile.mkdtemp(prefix="jarvis_sandbox_")
    _sock_tmpdir = "/tmp" if sys.platform == "darwin" else tempfile.gettempdir()

    if _USE_TCP_RPC:
        sock_path = None
        rpc_endpoint: Optional[str] = None
    else:
        sock_path = os.path.join(_sock_tmpdir, f"jarvis_rpc_{os.urandom(8).hex()}.sock")
        rpc_endpoint = sock_path

    tool_call_log: list = []
    tool_call_counter = [0]
    exec_start = time.monotonic()
    server_sock: Optional[socket.socket] = None
    rpc_thread: Optional[threading.Thread] = None

    try:
        # Write jarvis_tools.py stub and the user's script
        tools_src = _generate_jarvis_tools_module(list(sandbox_tools))
        with open(os.path.join(tmpdir, "jarvis_tools.py"), "w", encoding="utf-8") as f:
            f.write(tools_src)
        with open(os.path.join(tmpdir, "script.py"), "w", encoding="utf-8") as f:
            f.write(code)

        # Open RPC server socket
        if _USE_TCP_RPC:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.bind(("127.0.0.1", 0))
            _host, _port = server_sock.getsockname()[:2]
            rpc_endpoint = f"tcp://{_host}:{_port}"
        else:
            server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server_sock.bind(sock_path)
            os.chmod(sock_path, 0o600)
        server_sock.listen(1)

        rpc_thread = threading.Thread(
            target=_rpc_server_loop,
            args=(server_sock, tool_call_log, tool_call_counter, max_tool_calls, sandbox_tools),
            daemon=True,
        )
        rpc_thread.start()

        # Build child environment
        child_env = _scrub_child_env(os.environ)
        child_env["JARVIS_RPC_SOCKET"] = rpc_endpoint
        child_env["PYTHONDONTWRITEBYTECODE"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"

        # Ensure the voice-agent root is importable in the sandbox
        _va_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        existing_pp = child_env.get("PYTHONPATH", "")
        pp_parts = [tmpdir, _va_root]
        if existing_pp:
            pp_parts.append(existing_pp)
        child_env["PYTHONPATH"] = os.pathsep.join(pp_parts)

        # Per-profile HOME isolation when JARVIS_HOME is overridden
        _profile_home = get_subprocess_home()
        if _profile_home:
            child_env["HOME"] = _profile_home

        # Spawn the child
        script_path = os.path.join(tmpdir, "script.py")
        proc = subprocess.Popen(
            [sys.executable, script_path],
            cwd=tmpdir,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            preexec_fn=None if _IS_WINDOWS else os.setsid,
        )

        # Drain stdout (head+tail) and stderr (head-only) in background threads
        _STDOUT_HEAD = int(MAX_STDOUT_BYTES * 0.4)
        _STDOUT_TAIL = MAX_STDOUT_BYTES - _STDOUT_HEAD

        def _drain_simple(pipe, chunks, max_bytes):
            total = 0
            try:
                while True:
                    data = pipe.read(4096)
                    if not data:
                        break
                    if total < max_bytes:
                        keep = max_bytes - total
                        chunks.append(data[:keep])
                    total += len(data)
            except (ValueError, OSError):
                pass

        def _drain_head_tail(pipe, head_chunks, tail_chunks, head_bytes, tail_bytes, total_ref):
            from collections import deque
            head_collected = 0
            tail_buf: deque = deque()
            tail_collected = 0
            try:
                while True:
                    data = pipe.read(4096)
                    if not data:
                        break
                    total_ref[0] += len(data)
                    if head_collected < head_bytes:
                        keep = min(len(data), head_bytes - head_collected)
                        head_chunks.append(data[:keep])
                        head_collected += keep
                        data = data[keep:]
                        if not data:
                            continue
                    tail_buf.append(data)
                    tail_collected += len(data)
                    while tail_collected > tail_bytes and tail_buf:
                        oldest = tail_buf.popleft()
                        tail_collected -= len(oldest)
            except (ValueError, OSError):
                pass
            tail_chunks.extend(tail_buf)

        stdout_head_chunks: list = []
        stdout_tail_chunks: list = []
        stdout_total = [0]
        stderr_chunks: list = []

        stdout_reader = threading.Thread(
            target=_drain_head_tail,
            args=(proc.stdout, stdout_head_chunks, stdout_tail_chunks,
                  _STDOUT_HEAD, _STDOUT_TAIL, stdout_total),
            daemon=True,
        )
        stderr_reader = threading.Thread(
            target=_drain_simple,
            args=(proc.stderr, stderr_chunks, MAX_STDERR_BYTES),
            daemon=True,
        )
        stdout_reader.start()
        stderr_reader.start()

        # Poll: watch for exit, timeout
        status = "success"
        deadline = time.monotonic() + timeout
        while proc.poll() is None:
            if time.monotonic() > deadline:
                _kill_process_group(proc, escalate=True)
                status = "timeout"
                break
            time.sleep(0.2)

        stdout_reader.join(timeout=3)
        stderr_reader.join(timeout=3)

        stdout_head = b"".join(stdout_head_chunks).decode("utf-8", errors="replace")
        stdout_tail = b"".join(stdout_tail_chunks).decode("utf-8", errors="replace")
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")

        total = stdout_total[0]
        if total > MAX_STDOUT_BYTES and stdout_tail:
            omitted = total - len(stdout_head) - len(stdout_tail)
            stdout_text = (
                stdout_head
                + f"\n\n... [OUTPUT TRUNCATED - {omitted:,} chars omitted out of {total:,} total] ...\n\n"
                + stdout_tail
            )
        else:
            stdout_text = stdout_head + stdout_tail

        exit_code = proc.returncode if proc.returncode is not None else -1
        duration = round(time.monotonic() - exec_start, 2)

        # Stop RPC thread
        if server_sock is not None:
            try:
                server_sock.close()
                server_sock = None
            except OSError:
                pass
        if rpc_thread is not None:
            rpc_thread.join(timeout=3)

        # Strip ANSI
        stdout_text = strip_ansi(stdout_text)
        stderr_text = strip_ansi(stderr_text)

        result: Dict[str, Any] = {
            "status": status,
            "output": stdout_text,
            "tool_calls_made": tool_call_counter[0],
            "duration_seconds": duration,
        }

        if status == "timeout":
            msg = f"Script timed out after {timeout}s and was killed."
            result["error"] = msg
            result["output"] = (stdout_text + f"\n\n[Timeout] {msg}") if stdout_text else f"[Timeout] {msg}"
            logger.warning("execute_code timed out after %ss with %d tool calls", duration, tool_call_counter[0])
        elif exit_code != 0 and status != "timeout":
            result["status"] = "error"
            result["error"] = stderr_text or f"Script exited with code {exit_code}"
            if stderr_text:
                result["output"] = stdout_text + "\n--- stderr ---\n" + stderr_text

        return json.dumps(result, ensure_ascii=False)

    except Exception as exc:
        duration = round(time.monotonic() - exec_start, 2)
        logger.error(
            "execute_code failed after %ss with %d tool calls: %s",
            duration, tool_call_counter[0], exc, exc_info=True,
        )
        return json.dumps({
            "status": "error",
            "error": str(exc),
            "tool_calls_made": tool_call_counter[0],
            "duration_seconds": duration,
        }, ensure_ascii=False)

    finally:
        if server_sock is not None:
            try:
                server_sock.close()
            except OSError:
                pass
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
        if sock_path:
            try:
                os.unlink(sock_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def _check_execute_code_available() -> bool:
    """execute_code requires a POSIX OS for Unix domain socket RPC."""
    return not _IS_WINDOWS


# ---------------------------------------------------------------------------
# Schema + registration
# ---------------------------------------------------------------------------

_EXECUTE_CODE_SCHEMA = {
    "name": "execute_code",
    "description": (
        "Run a Python script that can call JARVIS tools programmatically. "
        "Use when you need 3 or more tool calls with processing logic between them, "
        "need to filter or reduce large tool outputs before they enter your context, "
        "need conditional branching (if X then Y else Z), or need to loop "
        "(fetch N pages, process N files, retry on failure).\n\n"
        "Use normal tool calls instead when: single tool call with no processing, "
        "you need the full result and complex reasoning, or the task requires "
        "interactive input.\n\n"
        "Available via `from jarvis_tools import ...`:\n"
        "  terminal(command, timeout=None, workdir=None) -> dict\n"
        "    Foreground only (no background/pty). Returns {output, exit_code}.\n"
        "  read_file(path, offset=1, limit=500) -> dict\n"
        "    1-indexed lines. Returns {content, total_lines}.\n"
        "  write_file(path, content) -> dict\n"
        "    Overwrites the file. Returns {status}.\n"
        "  search_files(pattern, target='content', path='.', file_glob=None, limit=50) -> dict\n"
        "    Returns {matches}.\n"
        "  patch(path, old_string, new_string, replace_all=False) -> dict\n"
        "    Find-and-replace in a file.\n\n"
        "Also available (no import needed — built into jarvis_tools):\n"
        "  json_parse(text) — json.loads with strict=False\n"
        "  shell_quote(s) — shlex.quote() for safe interpolation\n"
        "  retry(fn, max_attempts=3, delay=2) — exponential backoff\n\n"
        "Limits: 300-second timeout, 50 KB stdout cap, max 50 tool calls per script. "
        "Print your final result to stdout."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "Python code to execute. Import tools with "
                    "`from jarvis_tools import terminal, read_file, ...` "
                    "and print your final result to stdout."
                ),
            },
        },
        "required": ["code"],
    },
}

registry.register(
    name="execute_code",
    schema=_EXECUTE_CODE_SCHEMA,
    handler=lambda args, **_kw: execute_code(code=args.get("code", "")),
    toolset="builtin",
    check_fn=_check_execute_code_available,
    description=(
        "Run a Python script in a sandboxed subprocess. Use for multi-step "
        "automation that would otherwise require many sequential tool calls."
    ),
    emoji="🐍",
    max_result_size_chars=100_000,
)

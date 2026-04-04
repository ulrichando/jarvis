"""
TMUX SOCKET ISOLATION

Manages an isolated tmux socket for agent operations, preventing
interference with the user's tmux sessions.
"""

import asyncio
import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

TMUX_COMMAND = "tmux"
CLAUDE_SOCKET_PREFIX = "claude"

# Socket state
_socket_name: Optional[str] = None
_socket_path: Optional[str] = None
_server_pid: Optional[int] = None
_is_initializing = False
_tmux_availability_checked = False
_tmux_available = False
_tmux_tool_used = False


async def _exec_tmux(args: list, use_cwd: bool = False) -> dict:
    """Execute a tmux command."""
    try:
        proc = await asyncio.create_subprocess_exec(
            TMUX_COMMAND, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return {
            "stdout": stdout.decode("utf-8", errors="replace") if stdout else "",
            "stderr": stderr.decode("utf-8", errors="replace") if stderr else "",
            "code": proc.returncode or 0,
        }
    except FileNotFoundError:
        return {"stdout": "", "stderr": "tmux not found", "code": 127}


def get_claude_socket_name() -> str:
    """Gets the socket name for the isolated tmux session."""
    global _socket_name
    if not _socket_name:
        _socket_name = f"{CLAUDE_SOCKET_PREFIX}-{os.getpid()}"
    return _socket_name


def get_claude_socket_path() -> Optional[str]:
    """Gets the socket path if initialized."""
    return _socket_path


def set_claude_socket_info(path: str, pid: int) -> None:
    """Sets socket info after initialization."""
    global _socket_path, _server_pid
    _socket_path = path
    _server_pid = pid


def is_socket_initialized() -> bool:
    """Returns whether the socket has been initialized."""
    return _socket_path is not None and _server_pid is not None


def get_claude_tmux_env() -> Optional[str]:
    """
    Gets the TMUX environment variable value for the isolated socket.
    Format: "socket_path,server_pid,pane_index"
    """
    if not _socket_path or _server_pid is None:
        return None
    return f"{_socket_path},{_server_pid},0"


async def check_tmux_available() -> bool:
    """Checks if tmux is available on this system."""
    global _tmux_availability_checked, _tmux_available
    if not _tmux_availability_checked:
        try:
            result = await asyncio.create_subprocess_exec(
                "which", TMUX_COMMAND,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await result.communicate()
            _tmux_available = result.returncode == 0
        except FileNotFoundError:
            _tmux_available = False

        if not _tmux_available:
            logger.debug("[Socket] tmux is not installed.")
        _tmux_availability_checked = True
    return _tmux_available


def is_tmux_available() -> bool:
    """Returns the cached tmux availability status."""
    return _tmux_availability_checked and _tmux_available


def mark_tmux_tool_used() -> None:
    """Marks that the Tmux tool has been used at least once."""
    global _tmux_tool_used
    _tmux_tool_used = True


def has_tmux_tool_been_used() -> bool:
    """Returns whether the Tmux tool has been used."""
    return _tmux_tool_used


async def ensure_socket_initialized() -> None:
    """
    Ensures the socket is initialized with a tmux session.
    Safe to call multiple times; will only initialize once.
    """
    if is_socket_initialized():
        return

    available = await check_tmux_available()
    if not available:
        return

    global _is_initializing
    if _is_initializing:
        return

    _is_initializing = True
    try:
        await _do_initialize()
    except Exception as e:
        logger.error(f"[Socket] Failed to initialize tmux socket: {e}")
    finally:
        _is_initializing = False


async def _do_initialize() -> None:
    """Internal initialization of the tmux socket."""
    socket = get_claude_socket_name()

    result = await _exec_tmux([
        "-L", socket, "new-session", "-d", "-s", "base",
    ])

    if result["code"] != 0:
        check = await _exec_tmux(["-L", socket, "has-session", "-t", "base"])
        if check["code"] != 0:
            raise RuntimeError(
                f"Failed to create tmux session on socket {socket}: {result['stderr']}"
            )

    # Get socket path and server PID
    info = await _exec_tmux([
        "-L", socket, "display-message", "-p",
        "#{socket_path},#{pid}",
    ])

    if info["code"] == 0:
        parts = info["stdout"].strip().split(",")
        if len(parts) == 2:
            path, pid_str = parts
            try:
                pid = int(pid_str)
                set_claude_socket_info(path, pid)
                return
            except ValueError:
                pass

    # Fallback path
    uid = os.getuid() if hasattr(os, "getuid") else 0
    base_tmp = os.environ.get("TMPDIR", "/tmp")
    fallback_path = os.path.join(base_tmp, f"tmux-{uid}", socket)

    pid_result = await _exec_tmux(["-L", socket, "display-message", "-p", "#{pid}"])
    if pid_result["code"] == 0:
        try:
            pid = int(pid_result["stdout"].strip())
            set_claude_socket_info(fallback_path, pid)
            return
        except ValueError:
            pass

    raise RuntimeError(f"Failed to get socket info for {socket}")


def reset_socket_state() -> None:
    """Reset all socket state (for testing)."""
    global _socket_name, _socket_path, _server_pid
    global _is_initializing, _tmux_availability_checked, _tmux_available, _tmux_tool_used
    _socket_name = None
    _socket_path = None
    _server_pid = None
    _is_initializing = False
    _tmux_availability_checked = False
    _tmux_available = False
    _tmux_tool_used = False

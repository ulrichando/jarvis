"""
Error classes and utilities.
"""

from __future__ import annotations

import errno
from typing import Any, Optional


class JarvisError(Exception):
    """Base error class for JARVIS errors."""
    pass


class MalformedCommandError(Exception):
    """Error for malformed commands."""
    pass


class AbortError(Exception):
    """Error for aborted operations."""

    def __init__(self, message: str = "Operation aborted") -> None:
        super().__init__(message)
        self.name = "AbortError"


def is_abort_error(e: Any) -> bool:
    """Check if an error is an abort error."""
    if isinstance(e, AbortError):
        return True
    if isinstance(e, Exception) and getattr(e, "name", None) == "AbortError":
        return True
    return False


class ConfigParseError(Exception):
    """Custom error for configuration file parsing errors."""

    def __init__(
        self, message: str, file_path: str, default_config: Any
    ) -> None:
        super().__init__(message)
        self.file_path = file_path
        self.default_config = default_config


class ShellError(Exception):
    """Error from shell command execution."""

    def __init__(
        self, stdout: str, stderr: str, code: int, interrupted: bool
    ) -> None:
        super().__init__("Shell command failed")
        self.stdout = stdout
        self.stderr = stderr
        self.code = code
        self.interrupted = interrupted


class TeleportOperationError(Exception):
    """Error from teleport operations."""

    def __init__(self, message: str, formatted_message: str) -> None:
        super().__init__(message)
        self.formatted_message = formatted_message


class TelemetrySafeError(Exception):
    """Error with a message that is safe to log to telemetry."""

    def __init__(
        self, message: str, telemetry_message: Optional[str] = None
    ) -> None:
        super().__init__(message)
        self.telemetry_message = telemetry_message or message


def has_exact_error_message(error: Any, message: str) -> bool:
    """Check if an error has an exact message."""
    return isinstance(error, Exception) and str(error) == message


def to_error(e: Any) -> Exception:
    """Normalize an unknown value into an Exception."""
    return e if isinstance(e, Exception) else Exception(str(e))


def error_message(e: Any) -> str:
    """Extract a string message from an unknown error-like value."""
    return str(e) if isinstance(e, Exception) else str(e)


def get_errno_code(e: Any) -> Optional[str]:
    """Extract the errno code from a caught error."""
    if isinstance(e, OSError):
        return e.strerror
    code = getattr(e, "errno", None)
    if isinstance(code, int):
        return errno.errorcode.get(code)
    return None


def is_enoent(e: Any) -> bool:
    """True if the error is ENOENT (file or directory does not exist)."""
    if isinstance(e, FileNotFoundError):
        return True
    if isinstance(e, OSError) and e.errno == errno.ENOENT:
        return True
    return False


def is_fs_inaccessible(e: Any) -> bool:
    """
    True if the error means the path is missing, inaccessible, or
    structurally unreachable.
    """
    if isinstance(e, OSError):
        return e.errno in (errno.ENOENT, errno.EACCES, errno.EPERM, errno.ENOTDIR, errno.ELOOP)
    return False


def short_error_stack(e: Any, max_frames: int = 5) -> str:
    """Extract error message + top N stack frames from an unknown error."""
    if not isinstance(e, Exception):
        return str(e)
    import traceback

    tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
    if len(tb_lines) <= max_frames + 1:
        return "".join(tb_lines)
    return "".join(tb_lines[:1] + tb_lines[-(max_frames + 1) :])

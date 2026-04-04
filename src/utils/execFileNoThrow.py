"""
Subprocess execution wrappers that never raise exceptions.

Provides async wrappers over subprocess that always resolve (never throw),
easing error handling and cross-platform compatibility.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

MS_IN_SECOND = 1000
SECONDS_IN_MINUTE = 60
DEFAULT_TIMEOUT = 10 * SECONDS_IN_MINUTE  # 600 seconds


@dataclass
class ExecResult:
    """Result of a subprocess execution."""

    stdout: str = ""
    stderr: str = ""
    code: int = 0
    error: Optional[str] = None


async def exec_file_no_throw(
    file: str,
    args: list[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    preserve_output_on_error: bool = True,
    cwd: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
    stdin_mode: Optional[str] = None,
    input_data: Optional[str] = None,
) -> ExecResult:
    """
    Execute a file with arguments, never raising exceptions.

    Args:
        file: The executable to run.
        args: Command-line arguments.
        timeout: Timeout in seconds.
        preserve_output_on_error: Whether to include stdout/stderr on failure.
        cwd: Working directory.
        env: Environment variables (merged with current env).
        stdin_mode: How to handle stdin ('ignore', 'inherit', 'pipe').
        input_data: Data to send to stdin (requires stdin_mode='pipe').

    Returns:
        ExecResult with stdout, stderr, exit code, and optional error message.
    """
    try:
        process_env = dict(os.environ)
        if env:
            process_env.update(env)

        stdin = asyncio.subprocess.PIPE if input_data else asyncio.subprocess.DEVNULL

        proc = await asyncio.create_subprocess_exec(
            file,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=stdin,
            cwd=cwd,
            env=process_env if env else None,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=input_data.encode() if input_data else None),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecResult(
                stdout="",
                stderr="",
                code=1,
                error=f"Command timed out after {timeout}s",
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        code = proc.returncode or 0

        if code != 0:
            if preserve_output_on_error:
                return ExecResult(
                    stdout=stdout,
                    stderr=stderr,
                    code=code,
                    error=f"Command failed with exit code {code}",
                )
            else:
                return ExecResult(stdout="", stderr="", code=code)

        return ExecResult(stdout=stdout, stderr=stderr, code=0)

    except Exception as e:
        logger.error(f"exec_file_no_throw error: {e}")
        return ExecResult(stdout="", stderr="", code=1, error=str(e))


async def exec_file_no_throw_with_cwd(
    file: str,
    args: list[str],
    *,
    cwd: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    preserve_output_on_error: bool = True,
    env: Optional[dict[str, str]] = None,
    input_data: Optional[str] = None,
) -> ExecResult:
    """
    Execute a file with explicit cwd, never raising exceptions.
    Convenience wrapper around exec_file_no_throw.
    """
    return await exec_file_no_throw(
        file,
        args,
        timeout=timeout,
        preserve_output_on_error=preserve_output_on_error,
        cwd=cwd,
        env=env,
        input_data=input_data,
    )

"""
Wrapped subprocess.run with slow operation logging.

Use this instead of subprocess.run directly to detect performance issues.

Deprecated: Use async alternatives when possible. Sync exec calls block
the event loop.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)

SLOW_THRESHOLD_MS = 500


def exec_sync_deprecated(
    command: str,
    *,
    encoding: str = "utf-8",
    shell: bool = True,
    cwd: Optional[str] = None,
    timeout: Optional[float] = None,
) -> str:
    """
    Execute a command synchronously with slow operation logging.

    Deprecated: Use async alternatives when possible.

    Args:
        command: The shell command to execute.
        encoding: Output encoding.
        shell: Whether to run through a shell.
        cwd: Working directory.
        timeout: Timeout in seconds.

    Returns:
        The stdout output as a string.

    Raises:
        subprocess.CalledProcessError: If the command fails.
    """
    start = time.monotonic()
    truncated_cmd = command[:100]

    try:
        result = subprocess.run(
            command,
            shell=shell,
            capture_output=True,
            encoding=encoding,
            cwd=cwd,
            timeout=timeout,
            check=True,
        )
        return result.stdout
    finally:
        elapsed_ms = (time.monotonic() - start) * 1000
        if elapsed_ms > SLOW_THRESHOLD_MS:
            logger.warning(f"Slow execSync ({elapsed_ms:.0f}ms): {truncated_cmd}")

"""Process output utilities: stdout/stderr writing, error handling."""

from __future__ import annotations

import asyncio
import sys
from typing import Optional


def write_to_stdout(data: str) -> None:
    """Write data to stdout, ignoring BrokenPipeError."""
    try:
        sys.stdout.write(data)
        sys.stdout.flush()
    except BrokenPipeError:
        pass


def write_to_stderr(data: str) -> None:
    """Write data to stderr, ignoring BrokenPipeError."""
    try:
        sys.stderr.write(data)
        sys.stderr.flush()
    except BrokenPipeError:
        pass


def exit_with_error(message: str) -> None:
    """Write error to stderr and exit with code 1."""
    sys.stderr.write(message + "\n")
    sys.exit(1)


async def peek_for_stdin_data(timeout_ms: int) -> bool:
    """Wait for stdin data with a timeout.

    Returns True on timeout (no data arrived), False if data/EOF arrived.
    Used to distinguish a real pipe producer from an idle stdin.
    """
    loop = asyncio.get_event_loop()
    try:
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        try:
            await asyncio.wait_for(
                reader.readline(),
                timeout=timeout_ms / 1000.0,
            )
            return False  # Data arrived
        except asyncio.TimeoutError:
            return True  # Timed out
    except Exception:
        return True

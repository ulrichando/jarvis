"""CLI exit helpers for subcommand handlers."""

from __future__ import annotations

import sys


def cli_error(msg: str | None = None) -> None:
    """Write an error message to stderr and exit with code 1."""
    if msg:
        print(msg, file=sys.stderr)
    sys.exit(1)


def cli_ok(msg: str | None = None) -> None:
    """Write a message to stdout and exit with code 0."""
    if msg:
        print(msg)
    sys.exit(0)

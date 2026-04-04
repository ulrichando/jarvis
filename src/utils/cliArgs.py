"""CLI argument parsing utilities."""

from __future__ import annotations

import sys
from typing import Optional


def eager_parse_cli_flag(
    flag_name: str, argv: Optional[list[str]] = None
) -> Optional[str]:
    """Parse a CLI flag value early, before argparse processes arguments.

    Supports both space-separated (--flag value) and equals-separated (--flag=value).
    """
    if argv is None:
        argv = sys.argv

    for i, arg in enumerate(argv):
        if arg.startswith(f"{flag_name}="):
            return arg[len(flag_name) + 1 :]
        if arg == flag_name and i + 1 < len(argv):
            return argv[i + 1]
    return None


def extract_args_after_double_dash(
    command_or_value: str, args: Optional[list[str]] = None
) -> dict:
    """Handle the standard Unix -- separator convention in CLI arguments.

    Returns dict with 'command' and 'args' keys.
    """
    if args is None:
        args = []

    if command_or_value == "--" and len(args) > 0:
        return {"command": args[0], "args": args[1:]}
    return {"command": command_or_value, "args": args}

"""Shell prefix command formatting."""

from __future__ import annotations

import shlex


def format_shell_prefix_command(prefix: str, command: str) -> str:
    """Format a shell prefix with a command, properly quoting both.

    Examples:
        'bash' -> bash 'command'
        '/usr/bin/bash -c' -> /usr/bin/bash -c 'command'
    """
    space_before_dash = prefix.rfind(" -")
    if space_before_dash > 0:
        exec_path = prefix[:space_before_dash]
        args = prefix[space_before_dash + 1:]
        return f"{shlex.quote(exec_path)} {args} {shlex.quote(command)}"
    else:
        return f"{shlex.quote(prefix)} {shlex.quote(command)}"

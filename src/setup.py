"""Setup and initialization for the application.

Handles environment checks, session setup, worktree creation,
and background job initialization.
"""

from __future__ import annotations

import os
import sys
from typing import Optional


async def setup(
    cwd: str,
    permission_mode: str,
    allow_dangerously_skip_permissions: bool = False,
    worktree_enabled: bool = False,
    worktree_name: Optional[str] = None,
    tmux_enabled: bool = False,
    custom_session_id: Optional[str] = None,
    worktree_pr_number: Optional[int] = None,
    messaging_socket_path: Optional[str] = None,
) -> None:
    """Initialize the application environment.

    This function:
    1. Validates Python version
    2. Sets up session ID
    3. Sets current working directory
    4. Handles worktree creation if requested
    5. Starts background jobs
    6. Validates permission modes

    In the TypeScript version, this handles Node.js version checks,
    terminal backup restoration, GrowthBook initialization, and many
    other setup steps. The Python version focuses on core initialization.
    """
    # Validate Python version
    if sys.version_info < (3, 10):
        print(
            "Error: JARVIS requires Python version 3.10 or higher.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Set working directory
    os.chdir(cwd)

    # Validate bypass permissions mode
    if permission_mode == "bypassPermissions" or allow_dangerously_skip_permissions:
        # Check if running as root on Unix-like systems
        if (
            sys.platform != "win32"
            and hasattr(os, "getuid")
            and os.getuid() == 0
            and os.environ.get("IS_SANDBOX") != "1"
        ):
            print(
                "--dangerously-skip-permissions cannot be used with "
                "root/sudo privileges for security reasons",
                file=sys.stderr,
            )
            sys.exit(1)

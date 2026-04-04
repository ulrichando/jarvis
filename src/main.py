"""Main entry point for the application.

In the TypeScript version, this is main.tsx - it handles CLI argument
parsing, initialization, and launching the REPL or print mode.
The Python version provides the core logic without React/JSX.
"""

from __future__ import annotations

import os
import sys
from typing import Any, List, Optional


def start_deferred_prefetches() -> None:
    """Start background prefetch operations that can run after render."""
    # In full implementation: prefetch MCP resources, plugin hooks, etc.
    pass


async def main(
    args: Optional[List[str]] = None,
) -> None:
    """Main entry point.

    Handles:
    1. CLI argument parsing
    2. Environment initialization
    3. Setup (session, worktree, permissions)
    4. Launch REPL or print mode

    In the TypeScript version (main.tsx), this is a 2000+ line function
    that handles Commander.js argument parsing, React rendering, and
    extensive feature-gated logic. The Python version delegates to
    JARVIS's own CLI (shells/cli/jarvis_cli.py).
    """
    from .setup import setup

    cwd = os.getcwd()
    permission_mode = "default"

    await setup(
        cwd=cwd,
        permission_mode=permission_mode,
    )


def run() -> None:
    """Synchronous entry point."""
    import asyncio
    asyncio.run(main())


if __name__ == "__main__":
    run()

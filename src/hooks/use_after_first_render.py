"""Hook equivalent: run logic after first render/initialization."""

from __future__ import annotations

import os
import sys
import time


def after_first_render() -> None:
    """Execute post-initialization logic.

    If USER_TYPE is 'ant' and JARVIS_EXIT_AFTER_FIRST_RENDER is truthy,
    prints startup time to stderr and exits.
    """
    user_type = os.environ.get("USER_TYPE", "")
    exit_flag = os.environ.get("JARVIS_EXIT_AFTER_FIRST_RENDER", "")

    if user_type == "ant" and exit_flag.lower() in ("1", "true", "yes"):
        startup_ms = int(time.process_time() * 1000)
        sys.stderr.write(f"\nStartup time: {startup_ms}ms\n")
        sys.exit(0)

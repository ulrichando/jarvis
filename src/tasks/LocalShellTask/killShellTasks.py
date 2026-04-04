"""Kill shell tasks -- terminate running shell tasks."""

from __future__ import annotations

import os
import signal
import logging
from typing import Any

logger = logging.getLogger(__name__)


def kill_shell_task(pid: int, force: bool = False) -> bool:
    """Kill a shell task by PID. Returns True if successful."""
    try:
        sig = signal.SIGKILL if force else signal.SIGTERM
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except Exception as err:
        logger.warning("Failed to kill task %d: %s", pid, err)
        return False


def kill_all_shell_tasks(tasks: list[Any]) -> int:
    """Kill all running shell tasks. Returns count of killed tasks."""
    killed = 0
    for task in tasks:
        pid = getattr(task, "pid", None)
        if pid and kill_shell_task(pid):
            killed += 1
    return killed

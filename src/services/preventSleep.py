"""
Prevents the system from sleeping while working.

Uses `caffeinate` on macOS and `systemd-inhibit` on Linux to create
power assertions that prevent idle sleep. The subprocess is spawned
with a timeout and periodically restarted for self-healing behavior.

Only active on macOS and Linux - no-op on other platforms.
"""

from __future__ import annotations

import atexit
import logging
import platform
import shutil
import subprocess
import threading
from typing import Optional

logger = logging.getLogger(__name__)

CAFFEINATE_TIMEOUT_SECONDS = 300  # 5 minutes
RESTART_INTERVAL_S = 4 * 60  # 4 minutes

_process: Optional[subprocess.Popen] = None
_restart_timer: Optional[threading.Timer] = None
_ref_count = 0
_cleanup_registered = False
_lock = threading.Lock()


def start_prevent_sleep() -> None:
    """Increment the reference count and start preventing sleep if needed."""
    global _ref_count
    with _lock:
        _ref_count += 1
        if _ref_count == 1:
            _spawn_inhibitor()
            _start_restart_interval()


def stop_prevent_sleep() -> None:
    """Decrement the reference count and allow sleep if no more work pending."""
    global _ref_count
    with _lock:
        if _ref_count > 0:
            _ref_count -= 1
        if _ref_count == 0:
            _stop_restart_interval()
            _kill_inhibitor()


def force_stop_prevent_sleep() -> None:
    """Force stop preventing sleep, regardless of reference count."""
    global _ref_count
    with _lock:
        _ref_count = 0
        _stop_restart_interval()
        _kill_inhibitor()


def _start_restart_interval() -> None:
    """Start a periodic restart timer."""
    global _restart_timer
    system = platform.system()
    if system not in ("Darwin", "Linux"):
        return
    if _restart_timer is not None:
        return

    def _restart():
        global _restart_timer
        with _lock:
            if _ref_count > 0:
                logger.debug("Restarting sleep inhibitor to maintain prevention")
                _kill_inhibitor()
                _spawn_inhibitor()
            _restart_timer = threading.Timer(RESTART_INTERVAL_S, _restart)
            _restart_timer.daemon = True
            _restart_timer.start()

    _restart_timer = threading.Timer(RESTART_INTERVAL_S, _restart)
    _restart_timer.daemon = True
    _restart_timer.start()


def _stop_restart_interval() -> None:
    global _restart_timer
    if _restart_timer is not None:
        _restart_timer.cancel()
        _restart_timer = None


def _spawn_inhibitor() -> None:
    """Spawn a system-specific sleep inhibitor process."""
    global _process, _cleanup_registered
    system = platform.system()

    if system not in ("Darwin", "Linux"):
        return
    if _process is not None:
        return

    if not _cleanup_registered:
        _cleanup_registered = True
        atexit.register(force_stop_prevent_sleep)

    try:
        if system == "Darwin":
            # macOS: caffeinate -i prevents idle sleep
            _process = subprocess.Popen(
                ["caffeinate", "-i", "-t", str(CAFFEINATE_TIMEOUT_SECONDS)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif system == "Linux":
            if shutil.which("systemd-inhibit"):
                _process = subprocess.Popen(
                    [
                        "systemd-inhibit",
                        "--what=idle",
                        "--who=jarvis",
                        "--why=Working",
                        "sleep", str(CAFFEINATE_TIMEOUT_SECONDS),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        logger.debug("Started sleep inhibitor")
    except Exception:
        _process = None


def _kill_inhibitor() -> None:
    """Kill the sleep inhibitor process."""
    global _process
    if _process is not None:
        proc = _process
        _process = None
        try:
            proc.kill()
            logger.debug("Stopped sleep inhibitor, allowing sleep")
        except Exception:
            pass

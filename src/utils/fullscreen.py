"""
Fullscreen mode detection and control.

Manages fullscreen (alt-screen) rendering mode, including detection of
tmux control mode (iTerm2 integration) which is incompatible with
alt-screen rendering.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

_logged_tmux_cc_disable = False
_checked_tmux_mouse_hint = False
_tmux_control_mode_probed: Optional[bool] = None


def _is_env_truthy(value: Optional[str]) -> bool:
    """Check if an environment variable value is truthy."""
    if not value:
        return False
    return value.lower() in ("1", "true", "yes")


def _is_env_defined_falsy(value: Optional[str]) -> bool:
    """Check if an environment variable is explicitly set to a falsy value."""
    if value is None:
        return False
    return value.lower() in ("0", "false", "no")


def _is_tmux_control_mode_env_heuristic() -> bool:
    """
    Env-var heuristic for iTerm2's tmux integration mode (tmux -CC).

    In -CC mode, iTerm2 renders tmux panes as native splits. tmux runs
    as a server (TMUX is set) but iTerm2 is the actual terminal emulator,
    so TERM_PROGRAM stays 'iTerm.app'.
    """
    if not os.environ.get("TMUX"):
        return False
    if os.environ.get("TERM_PROGRAM") != "iTerm.app":
        return False
    term = os.environ.get("TERM", "")
    return not term.startswith("screen") and not term.startswith("tmux")


def _probe_tmux_control_mode_sync() -> None:
    """
    Sync one-shot probe: asks tmux directly whether this client is in
    control mode. Result is cached.
    """
    global _tmux_control_mode_probed

    _tmux_control_mode_probed = _is_tmux_control_mode_env_heuristic()
    if _tmux_control_mode_probed:
        return
    if not os.environ.get("TMUX"):
        return
    if os.environ.get("TERM_PROGRAM"):
        return

    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#{client_control_mode}"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            _tmux_control_mode_probed = result.stdout.strip() == "1"
    except Exception:
        pass


def is_tmux_control_mode() -> bool:
    """
    True when running under tmux -CC (iTerm2 integration mode).

    The alt-screen / mouse-tracking path is unrecoverable in -CC mode,
    so callers auto-disable fullscreen.
    """
    global _tmux_control_mode_probed
    if _tmux_control_mode_probed is None:
        _probe_tmux_control_mode_sync()
    return _tmux_control_mode_probed or False


def is_fullscreen_env_enabled() -> bool:
    """
    Check if fullscreen mode is enabled via environment variables.

    Explicit opt-out (CLAUDE_CODE_NO_FLICKER=0) always wins.
    Explicit opt-in (CLAUDE_CODE_NO_FLICKER=1) overrides auto-detection.
    Auto-disables under tmux -CC.
    """
    global _logged_tmux_cc_disable

    env_val = os.environ.get("CLAUDE_CODE_NO_FLICKER")

    if _is_env_defined_falsy(env_val):
        return False
    if _is_env_truthy(env_val):
        return True

    if is_tmux_control_mode():
        if not _logged_tmux_cc_disable:
            _logged_tmux_cc_disable = True
            logger.debug(
                "fullscreen disabled: tmux -CC (iTerm2 integration mode) detected"
            )
        return False

    return os.environ.get("USER_TYPE") == "ant"


def is_mouse_tracking_enabled() -> bool:
    """
    Whether fullscreen mode should enable mouse tracking.
    Set CLAUDE_CODE_DISABLE_MOUSE=1 to disable mouse capture while
    keeping alt-screen and virtualized scroll.
    """
    return not _is_env_truthy(os.environ.get("CLAUDE_CODE_DISABLE_MOUSE"))


def is_mouse_clicks_disabled() -> bool:
    """
    Whether mouse click handling is disabled (clicks/drags ignored,
    wheel still works).
    """
    return _is_env_truthy(os.environ.get("CLAUDE_CODE_DISABLE_MOUSE_CLICKS"))


def reset_for_testing() -> None:
    """Reset module-level state. Only for use in tests."""
    global _logged_tmux_cc_disable, _checked_tmux_mouse_hint, _tmux_control_mode_probed
    _logged_tmux_cc_disable = False
    _checked_tmux_mouse_hint = False
    _tmux_control_mode_probed = None

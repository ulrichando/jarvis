"""Idle auto-revert for the direct voice modes (gemini / openai).

When a direct-mode backend has had no activity for
JARVIS_DIRECT_IDLE_TIMEOUT_S seconds, revert to JARVIS-Claude (the free,
always-on base mode) so provider quota doesn't burn while the user is idle.
See docs/superpowers/specs/2026-05-30-direct-mode-idle-revert-design.md.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Callable


def idle_timeout_s() -> float:
    """Idle window before reverting. Env JARVIS_DIRECT_IDLE_TIMEOUT_S
    (default 300; 0 disables). Bad values fall back to the default."""
    raw = os.environ.get("JARVIS_DIRECT_IDLE_TIMEOUT_S", "300")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 300.0


def should_revert(idle_s: float, timeout_s: float, tool_running: bool) -> bool:
    """Pure decision: revert iff enabled, no tool in flight, and idle past
    the window. Boundary is strict (idle must EXCEED timeout)."""
    return timeout_s > 0 and not tool_running and idle_s > timeout_s


def revert_to_claude(jarvis_mode_path: str, log) -> None:
    """Switch back to JARVIS-Claude.

    MUST run `jarvis-mode jarvis` in a SEPARATE cgroup: the backend's unit is
    KillMode=control-group + Restart=always, so a plain child would be killed
    by the `systemctl stop` that jarvis-mode issues -- before it can unmute
    Claude. `systemd-run --user --scope` registers an independent scope that
    survives the stop. Falls back to a detached spawn if systemd-run is absent.
    """
    cmd = ["systemd-run", "--user", "--scope", "--", jarvis_mode_path, "jarvis"]
    try:
        subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return
    except Exception as e:
        log.warning(f"[idle-revert] systemd-run failed ({e!r}); trying direct spawn")
    try:
        subprocess.Popen(
            [jarvis_mode_path, "jarvis"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        log.warning(f"[idle-revert] fallback spawn failed: {e!r}")


async def idle_revert_watch(
    *,
    get_idle_s: Callable[[], float],
    is_tool_running: Callable[[], bool],
    jarvis_mode_path: str,
    stop: asyncio.Event,
    log,
    label: str,
) -> None:
    """Poll until idle exceeds the timeout (and no tool is running), then
    revert to Claude and set `stop` so the backend winds down."""
    timeout = idle_timeout_s()
    if timeout <= 0:
        log.info(f"[{label}] idle-revert disabled (JARVIS_DIRECT_IDLE_TIMEOUT_S=0)")
        return
    log.info(f"[{label}] idle-revert armed: -> Claude after {timeout:.0f}s idle")
    poll = min(20.0, max(5.0, timeout / 4.0))
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll)
            return  # stop set elsewhere (deliberate shutdown)
        except asyncio.TimeoutError:
            pass
        if should_revert(get_idle_s(), timeout, is_tool_running()):
            log.warning(
                f"[{label}] idle {get_idle_s():.0f}s > {timeout:.0f}s -- "
                f"reverting to JARVIS-Claude to stop token burn"
            )
            revert_to_claude(jarvis_mode_path, log)
            stop.set()
            return

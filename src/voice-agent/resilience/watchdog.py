"""sd_notify(WATCHDOG=1) emitter for the voice agent + voice client.

Critical detail: this MUST run in the same asyncio loop as the
listener task. If we used a separate thread, a stalled listener
wouldn't trigger the systemd-watchdog restart — the thread would
keep pinging happily while the actual work was wedged.

systemd's WatchdogSec=10s setting kills + restarts the process if we
miss two consecutive pings, so the default ping interval is half
that (5s).

Spec: docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md
"""
from __future__ import annotations

import asyncio
import logging

from pipeline import notify

logger = logging.getLogger("jarvis.watchdog")


async def watchdog_loop(
    stop: asyncio.Event,
    *,
    notifier=None,
    interval_s: float = 5.0,
) -> None:
    """Notify systemd while the listener loop is alive.

    Sends `READY=1` on entry, `WATCHDOG=1` every `interval_s` while
    `stop` is unset, and `STOPPING=1` on exit. If `stop` is already
    set on entry, no WATCHDOG ping is sent.

    Args:
        stop: asyncio.Event signalling shutdown.
        notifier: SystemdNotifier-like object (for test injection).
                  Defaults to pipeline.notify.get_notifier() (real sdnotify
                  on Linux, no-op on Windows/macOS).
        interval_s: how often to ping (half of WatchdogSec).
    """
    if notifier is None:
        notifier = notify.get_notifier()
    notifier.notify("READY=1")
    logger.info("[watchdog] READY=1; ping interval %.1fs", interval_s)
    try:
        while not stop.is_set():
            notifier.notify("WATCHDOG=1")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
            except asyncio.TimeoutError:
                pass
    finally:
        notifier.notify("STOPPING=1")
        logger.info("[watchdog] STOPPING=1")

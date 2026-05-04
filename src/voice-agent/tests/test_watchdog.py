"""watchdog_loop — sd_notify(WATCHDOG=1) emitted from inside the
asyncio loop. If the loop stalls (e.g. listener task crashed), the
notifications stop and systemd restarts us within WatchdogSec=10s.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


def _run(coro):
    """Run an async coroutine in a fresh event loop. Closes the loop
    afterwards to avoid ResourceWarning + selector fd leaks."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_watchdog_loop_emits_ready_pings_then_stopping():
    """While stop is unset the loop must emit at least one
    WATCHDOG=1; once stop is set it exits cleanly with STOPPING=1."""
    from watchdog import watchdog_loop

    notifier = MagicMock()
    stop = asyncio.Event()

    async def main():
        async def _stopper():
            await asyncio.sleep(0.1)
            stop.set()
        await asyncio.gather(
            watchdog_loop(stop, notifier=notifier, interval_s=0.02),
            _stopper(),
        )

    _run(main())

    calls = [c.args[0] for c in notifier.notify.call_args_list]
    assert calls[0] == "READY=1", f"first call should be READY=1; got {calls[0]!r}"
    assert "WATCHDOG=1" in calls, f"expected at least one WATCHDOG=1; got {calls!r}"
    assert calls[-1] == "STOPPING=1", f"last call should be STOPPING=1; got {calls[-1]!r}"


def test_watchdog_loop_skips_pings_when_stop_already_set():
    """If stop is set before entry the loop emits READY/STOPPING but
    no WATCHDOG (no point in pinging if we're shutting down)."""
    from watchdog import watchdog_loop

    notifier = MagicMock()
    stop = asyncio.Event()
    stop.set()

    async def main():
        await watchdog_loop(stop, notifier=notifier, interval_s=0.02)

    _run(main())

    calls = [c.args[0] for c in notifier.notify.call_args_list]
    assert calls == ["READY=1", "STOPPING=1"], (
        f"expected only READY+STOPPING when stop pre-set; got {calls!r}"
    )

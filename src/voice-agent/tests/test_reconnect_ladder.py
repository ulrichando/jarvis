"""ReconnectLadder — backoff schedule (0.5/1/2/4/10s + jitter) for
tier-1 resume; falls through to full teardown after attempts exhaust.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from reconnect_ladder import ReconnectLadder


def _run(coro):
    """Run an async coroutine in a fresh event loop. Closes the loop
    afterwards to avoid ResourceWarning + selector fd leaks."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_resume_succeeds_first_attempt_no_full_teardown():
    resume = AsyncMock(return_value=True)
    teardown = AsyncMock()
    ladder = ReconnectLadder(
        resume_fn=resume,
        full_teardown_fn=teardown,
        backoffs=[0.0, 0.0],
    )
    _run(ladder.recover())
    assert resume.await_count == 1
    assert teardown.await_count == 0


def test_falls_through_to_teardown_after_all_resumes_fail():
    resume = AsyncMock(return_value=False)
    teardown = AsyncMock()
    ladder = ReconnectLadder(
        resume_fn=resume,
        full_teardown_fn=teardown,
        backoffs=[0.0, 0.0, 0.0],
    )
    _run(ladder.recover())
    assert resume.await_count == 3
    assert teardown.await_count == 1


def test_resume_succeeds_on_third_attempt():
    resume = AsyncMock(side_effect=[False, False, True])
    teardown = AsyncMock()
    ladder = ReconnectLadder(
        resume_fn=resume,
        full_teardown_fn=teardown,
        backoffs=[0.0, 0.0, 0.0, 0.0],
    )
    _run(ladder.recover())
    assert resume.await_count == 3
    assert teardown.await_count == 0


async def _simulate_repeated(ladder, n):
    for _ in range(n):
        await ladder.recover()


def test_teardown_failure_after_max_in_a_row_bails():
    """After max_full_reconnects+1 consecutive full teardowns, raise
    SystemExit so systemd takes over."""
    resume = AsyncMock(return_value=False)
    teardown = AsyncMock()
    ladder = ReconnectLadder(
        resume_fn=resume,
        full_teardown_fn=teardown,
        backoffs=[0.0],
        max_full_reconnects=3,
    )
    with pytest.raises(SystemExit):
        _run(_simulate_repeated(ladder, 4))


def test_resume_exception_counts_as_failure():
    """A resume_fn that raises should be caught and counted as a
    failure, then move on to the next backoff slot."""
    boom = AsyncMock(side_effect=RuntimeError("oh no"))
    teardown = AsyncMock()
    ladder = ReconnectLadder(
        resume_fn=boom,
        full_teardown_fn=teardown,
        backoffs=[0.0, 0.0],
    )
    _run(ladder.recover())
    # All resumes failed → teardown fired once.
    assert boom.await_count == 2
    assert teardown.await_count == 1


def test_consecutive_full_counter_resets_on_success():
    """A successful resume resets the consecutive-full-reconnect
    counter so the next disconnect doesn't bail prematurely."""
    resume = AsyncMock()
    # First cycle: all fail → teardown fires.
    # Second cycle: succeed on first attempt.
    # Third cycle: fail again → teardown fires (count should be back to 1).
    resume.side_effect = [False, True, False]
    teardown = AsyncMock()
    ladder = ReconnectLadder(
        resume_fn=resume,
        full_teardown_fn=teardown,
        backoffs=[0.0],
        max_full_reconnects=3,
    )
    _run(_simulate_repeated(ladder, 3))
    # Two cycles ended in teardown; one ended in resume success.
    assert teardown.await_count == 2
    # Counter was reset after the success.
    assert ladder._consecutive_full == 1

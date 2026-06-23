"""Tests for the cognitive-loop experience signal (Phase 1, Task 1).

Async tests use asyncio.run() inside sync test fns — the repo convention
(see tests/test_automod_spawner.py); there is no asyncio_mode config.
"""
import asyncio

from pipeline.automod import experience_signal as signal


def test_bump_records_reasons_and_sets_event():
    signal.clear()
    signal.drain_reasons()  # reset the buffer
    signal.bump("error:tool_x")
    signal.bump("correction:stop saying sir")
    assert signal.is_set() is True
    assert signal.drain_reasons() == ["error:tool_x", "correction:stop saying sir"]
    # drain empties the buffer
    assert signal.drain_reasons() == []


def test_wait_returns_true_when_bumped():
    signal.clear()

    async def _run():
        loop = asyncio.get_running_loop()
        loop.call_later(0.05, signal.bump, "fact:user birthday")
        return await signal.wait(timeout=2.0)

    assert asyncio.run(_run()) is True


def test_wait_returns_false_on_timeout():
    signal.clear()
    signal.drain_reasons()
    assert asyncio.run(signal.wait(0.1)) is False

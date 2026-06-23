"""Tests for _task_utils.log_task_exception.

Uses a duck-typed fake task (real asyncio.Task duck-types to it) so the test
doesn't depend on pytest-asyncio mode.
"""
import logging

from _task_utils import log_task_exception


class _FakeTask:
    def __init__(self, exc=None, cancelled=False, name="t"):
        self._exc = exc
        self._cancelled = cancelled
        self._name = name

    def cancelled(self):
        return self._cancelled

    def exception(self):
        return self._exc

    def get_name(self):
        return self._name


def test_logs_on_failure(caplog):
    with caplog.at_level(logging.ERROR):
        log_task_exception(_FakeTask(exc=RuntimeError("nope"), name="t-fail"))
    assert any("failed" in r.message for r in caplog.records)
    assert any("t-fail" in r.getMessage() for r in caplog.records)


def test_silent_on_success(caplog):
    with caplog.at_level(logging.ERROR):
        log_task_exception(_FakeTask(exc=None, name="t-ok"))
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_silent_on_cancelled(caplog):
    with caplog.at_level(logging.ERROR):
        log_task_exception(_FakeTask(cancelled=True, name="t-cancel"))
    assert caplog.records == []

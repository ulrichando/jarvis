"""Tests for pipeline/memory_provider.py — Task 2 of the memory-provider plan.

Covers:
- no-op when JARVIS_MEMORY_PROVIDER is unset
- begin_session calls provider.initialize
- recall_for_query delegates to provider.recall
- sync_item_async is fire-and-forget (creates an asyncio task, never raises)
- provider errors during sync are swallowed
- maybe_recall_for_turn returns "" on timeout
- maybe_recall_for_turn returns the string on success (sync provider)
- maybe_recall_for_turn works with an async provider
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Fake provider helpers
# ---------------------------------------------------------------------------

class _Fake:
    name = "fake"

    def __init__(self):
        self.calls = []

    def is_available(self):
        return True

    def initialize(self, sid):
        self.calls.append(("init", sid))

    def recall_context(self, hint=""):
        self.calls.append(("ctx", hint))
        return "CTX"

    def recall(self, q):
        self.calls.append(("recall", q))
        return "DEEP"

    def sync_message(self, role, text):
        self.calls.append(("sync", role, text))

    def end_session(self):
        self.calls.append(("end",))


class _SlowFake(_Fake):
    """recall_context sleeps 0.2 s — used to test timeout path."""

    def recall_context(self, hint=""):
        import time
        time.sleep(0.2)
        return "SLOW"


class _AsyncFake(_Fake):
    """recall_context is a coroutine — used to test async-provider branch."""

    async def recall_context(self, hint=""):
        self.calls.append(("ctx", hint))
        return "ASYNC_CTX"


def _install(monkeypatch, prov):
    """Register *prov* in the "memory" kind and point the env flag at it."""
    from tools import _provider_registry as pr
    pr.reset_providers("memory")
    pr.register_provider("memory", prov.name, prov)
    monkeypatch.setenv("JARVIS_MEMORY_PROVIDER", prov.name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_runtime_noop_when_flag_unset(monkeypatch):
    monkeypatch.delenv("JARVIS_MEMORY_PROVIDER", raising=False)
    from pipeline import memory_provider as mp
    assert mp.active_provider() is None
    mp.begin_session("room1")          # no raise
    assert mp.recall_for_query("hi") == ""


def test_begin_session_and_recall(monkeypatch):
    from pipeline import memory_provider as mp
    f = _Fake()
    _install(monkeypatch, f)
    mp.begin_session("room1")
    assert ("init", "room1") in f.calls
    assert mp.recall_for_query("deep q") == "DEEP"


def test_recall_for_query_handles_async_provider(monkeypatch):
    """recall_for_query must await an ASYNC provider.recall (Honcho's peer.chat is
    a coroutine). Regression: the sync wrapper used to isinstance-check the
    coroutine object → always returned "" and left it un-awaited. recall_for_query
    runs in the recall() tool's to_thread worker (no running loop), so asyncio.run
    on the coroutine is valid."""
    from pipeline import memory_provider as mp

    class _AsyncRecall(_Fake):
        async def recall(self, q):
            self.calls.append(("recall", q))
            return "DEEP_ASYNC"

    a = _AsyncRecall()
    _install(monkeypatch, a)
    mp.begin_session("room1")
    assert mp.recall_for_query("deep q") == "DEEP_ASYNC"
    assert ("recall", "deep q") in a.calls


def test_sync_async_is_fire_and_forget(monkeypatch):
    from pipeline import memory_provider as mp
    f = _Fake()
    _install(monkeypatch, f)

    async def run():
        mp.begin_session("r")
        mp.sync_item_async("user", "hello")
        await asyncio.sleep(0.05)  # let the task run

    asyncio.run(run())
    assert ("sync", "user", "hello") in f.calls


def test_sync_swallows_provider_error(monkeypatch):
    from pipeline import memory_provider as mp

    class Boom(_Fake):
        def sync_message(self, role, text):
            raise RuntimeError("boom")

    b = Boom()
    _install(monkeypatch, b)

    async def run():
        mp.begin_session("r")
        mp.sync_item_async("user", "x")
        await asyncio.sleep(0.05)  # must NOT raise out

    asyncio.run(run())  # no exception escapes


def test_maybe_recall_returns_string_sync_provider(monkeypatch):
    """A normal sync provider whose recall_context returns a string."""
    from pipeline import memory_provider as mp
    f = _Fake()
    _install(monkeypatch, f)

    result = asyncio.run(mp.maybe_recall_for_turn("what did I say", timeout_s=1.5))
    assert result == "CTX"


def test_maybe_recall_timeout_returns_empty(monkeypatch):
    """A slow sync provider that sleeps > timeout_s → returns "" (never raises)."""
    from pipeline import memory_provider as mp
    s = _SlowFake()
    _install(monkeypatch, s)

    result = asyncio.run(mp.maybe_recall_for_turn("anything", timeout_s=0.05))
    assert result == ""


def test_maybe_recall_returns_empty_when_no_provider(monkeypatch):
    monkeypatch.delenv("JARVIS_MEMORY_PROVIDER", raising=False)
    from pipeline import memory_provider as mp

    result = asyncio.run(mp.maybe_recall_for_turn("hi"))
    assert result == ""


def test_maybe_recall_async_provider(monkeypatch):
    """An async recall_context coroutine is awaited directly (no to_thread)."""
    from pipeline import memory_provider as mp

    class AsyncFake(_AsyncFake):
        pass

    a = AsyncFake()
    _install(monkeypatch, a)

    result = asyncio.run(mp.maybe_recall_for_turn("tell me about my dog", timeout_s=1.5))
    assert result == "ASYNC_CTX"


def test_end_session_noop_when_no_provider(monkeypatch):
    monkeypatch.delenv("JARVIS_MEMORY_PROVIDER", raising=False)
    from pipeline import memory_provider as mp
    mp.end_session()  # no raise


def test_end_session_calls_provider(monkeypatch):
    from pipeline import memory_provider as mp
    f = _Fake()
    _install(monkeypatch, f)
    mp.begin_session("s1")
    mp.end_session()
    assert ("end",) in f.calls

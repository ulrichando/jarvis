"""Honcho memory provider — gating + structure tests (no network required).

The live recall/sync path (peer.chat, session.add_messages) is not exercised here
because it requires a HONCHO_API_KEY and a live Honcho service. What is verified:
  - name="honcho"; is_available() gates on the key
  - all methods return safe defaults when uninitialized / no key
  - async method signatures are correct (iscoroutinefunction)
  - no "hermes" token anywhere in the plugin module
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_PLUGIN_PATH = Path(__file__).parent.parent / "plugins" / "memory" / "honcho" / "__init__.py"


def _load():
    """Load the honcho plugin module directly (bypass plugin discovery)."""
    spec = importlib.util.spec_from_file_location("_t_honcho", _PLUGIN_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------

def test_honcho_name():
    p = _load().HonchoMemoryProvider()
    assert p.name == "honcho"


def test_honcho_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    p = _load().HonchoMemoryProvider()
    assert p.is_available() is False


def test_honcho_available_with_key_and_sdk(monkeypatch):
    """With a key set (and honcho installed), is_available() returns True."""
    monkeypatch.setenv("HONCHO_API_KEY", "test-key-123")
    p = _load().HonchoMemoryProvider()
    # honcho-ai IS installed in this venv
    assert p.is_available() is True


def test_honcho_unavailable_when_sdk_missing(monkeypatch):
    """Simulate honcho not installed: find_spec returns None → not available."""
    monkeypatch.setenv("HONCHO_API_KEY", "test-key-123")
    # We patch importlib.util.find_spec inside the module's scope
    m = _load()
    original_find_spec = importlib.util.find_spec

    def _fake_find_spec(name):
        if name == "honcho":
            return None
        return original_find_spec(name)

    import importlib.util as _iu
    monkeypatch.setattr(_iu, "find_spec", _fake_find_spec)
    # Reload to pick up the monkeypatch (find_spec is called at is_available time)
    p2 = _load().HonchoMemoryProvider()
    assert p2.is_available() is False


# ---------------------------------------------------------------------------
# Safe defaults when uninitialized / no key
# ---------------------------------------------------------------------------

def test_honcho_recall_safe_when_uninitialized(monkeypatch):
    """No session/key → returns "" rather than raising."""
    import asyncio
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    p = _load().HonchoMemoryProvider()
    # recall is async in the real impl — await it
    result = asyncio.run(p.recall("anything"))
    assert result == ""


def test_honcho_recall_context_safe_when_uninitialized(monkeypatch):
    import asyncio
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    p = _load().HonchoMemoryProvider()
    result = asyncio.run(p.recall_context("x"))
    assert result == ""


def test_honcho_sync_message_safe_when_uninitialized(monkeypatch):
    """sync_message with no initialized session must not raise."""
    import asyncio
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    p = _load().HonchoMemoryProvider()
    # async method — awaiting it with no handles must not raise
    asyncio.run(p.sync_message("user", "hello"))
    # No assertion needed — non-raise IS the contract


def test_honcho_end_session_safe_when_uninitialized(monkeypatch):
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    p = _load().HonchoMemoryProvider()
    p.end_session()  # must not raise


def test_honcho_initialize_safe_without_key(monkeypatch):
    """initialize() with a bad/missing key must not raise (guards + logs)."""
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    p = _load().HonchoMemoryProvider()
    p.initialize("session-xyz")  # must not raise


# ---------------------------------------------------------------------------
# Async method signatures (runtime relies on iscoroutinefunction checks)
# ---------------------------------------------------------------------------

def test_honcho_recall_is_async():
    """recall() must be a coroutine function (AsyncHoncho path)."""
    p = _load().HonchoMemoryProvider()
    assert inspect.iscoroutinefunction(p.recall), (
        "HonchoMemoryProvider.recall must be async (the runtime awaits it)"
    )


def test_honcho_recall_context_is_async():
    """recall_context() must be a coroutine function."""
    p = _load().HonchoMemoryProvider()
    assert inspect.iscoroutinefunction(p.recall_context), (
        "HonchoMemoryProvider.recall_context must be async"
    )


def test_honcho_sync_message_is_async():
    """sync_message() must be a coroutine function (AsyncHoncho.add_messages)."""
    p = _load().HonchoMemoryProvider()
    assert inspect.iscoroutinefunction(p.sync_message), (
        "HonchoMemoryProvider.sync_message must be async"
    )


# ---------------------------------------------------------------------------
# register() API
# ---------------------------------------------------------------------------

def test_honcho_register_fn_exists():
    m = _load()
    assert callable(m.register), "register(ctx) must exist in the module"


def test_honcho_register_calls_ctx(monkeypatch):
    """register(ctx) calls ctx.register_memory_provider with a HonchoMemoryProvider."""
    m = _load()
    received = []

    class _FakeCtx:
        def register_memory_provider(self, p):
            received.append(p)

    m.register(_FakeCtx())
    assert len(received) == 1
    assert received[0].name == "honcho"


# ---------------------------------------------------------------------------
# No "hermes" tokens anywhere in the plugin file
# ---------------------------------------------------------------------------

def test_no_hermes_token_in_plugin():
    source = _PLUGIN_PATH.read_text()
    assert "hermes" not in source.lower(), (
        "plugins/memory/honcho/__init__.py must not contain 'hermes' (JARVIS-native naming rule)"
    )


# ---------------------------------------------------------------------------
# Lazy init — must be safe from a running event loop (regression: the old
# initialize() used asyncio.run, which raises inside on_enter's loop and would
# silently leave the backend permanently inert).
# ---------------------------------------------------------------------------

def test_initialize_deferred_and_loop_safe(monkeypatch):
    """initialize() does NO network/asyncio.run — safe inside a running loop,
    leaves handles unbuilt (deferred), and the async methods no-op without a key."""
    import asyncio
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    p = _load().HonchoMemoryProvider()

    async def run():
        p.initialize("sess-x")          # called under a running loop — must not raise
        assert p._session_id == "sess-x"
        assert p._session is None        # deferred, not eagerly built
        assert p._init_attempted is False
        assert await p.recall("q") == ""
        assert await p.recall_context("h") == ""
        await p.sync_message("user", "x")

    asyncio.run(run())  # no RuntimeError escapes


def test_lazy_init_builds_handles_in_async_context(monkeypatch):
    """With a key + a (faked) SDK, the client/peer/session handles build lazily
    on the first async call FROM a running loop — the case the old asyncio.run
    code silently failed. Proves _ensure_init works under the event loop."""
    import asyncio
    import sys
    import types

    mod = _load()

    class _Peer:
        def __init__(self, pid): self.id = pid

    class _SessAio:
        async def add_messages(self, *a, **k): return None
        async def context(self, **k):
            return types.SimpleNamespace(summary=None, messages=[])

    class _Session:
        def __init__(self, sid): self.id = sid; self.aio = _SessAio()

    class _ClientAio:
        async def peer(self, pid): return _Peer(pid)
        async def session(self, sid): return _Session(sid)

    class _Client:
        def __init__(self, api_key=None): self.aio = _ClientAio()

    fake = types.ModuleType("honcho")
    fake.Honcho = _Client
    fake.MessageCreateParams = lambda **k: types.SimpleNamespace(**k)
    monkeypatch.setitem(sys.modules, "honcho", fake)
    monkeypatch.setenv("HONCHO_API_KEY", "k")
    monkeypatch.setattr(mod.HonchoMemoryProvider, "is_available", lambda self: True)

    p = mod.HonchoMemoryProvider()

    async def run():
        p.initialize("s1")
        assert p._session is None              # not yet built
        await p.sync_message("user", "hi")     # triggers lazy _ensure_init under a loop
        assert p._session is not None          # built — no asyncio.run failure
        assert p._peer_user.id == "ulrich"
        assert p._peer_agent.id == "jarvis"

    asyncio.run(run())

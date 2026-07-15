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


# ---------------------------------------------------------------------------
# _durable_representation — keep the deriver's reasoned sections, drop the
# noisy ``## Explicit Observations`` transcript-restatement layer.
# ---------------------------------------------------------------------------

def test_durable_representation_drops_explicit_only():
    m = _load()
    rep = "## Explicit Observations\n[t] ulrich said Wow!\n[t] ulrich has a drink"
    assert m._durable_representation(rep) == ""


def test_durable_representation_keeps_reasoned_sections():
    m = _load()
    rep = ("## Explicit Observations\n[t] ulrich said Wow!\n"
           "## Deductive Observations\n[t] ulrich values verification\n"
           "## Inductive Observations\n[t] ulrich tests end-to-end")
    out = m._durable_representation(rep)
    assert "Wow!" not in out                       # explicit dropped
    assert "## Deductive Observations" in out
    assert "ulrich values verification" in out
    assert "## Inductive Observations" in out


def test_durable_representation_keeps_contradictions():
    m = _load()
    rep = "## Contradictions\n[t] ulrich earlier said X but now Y"
    out = m._durable_representation(rep)
    assert "## Contradictions" in out and "X but now Y" in out


def test_durable_representation_empty_and_whitespace():
    m = _load()
    assert m._durable_representation("") == ""
    assert m._durable_representation("   \n  ") == ""


# ---------------------------------------------------------------------------
# recall_context — concurrent context() + working-representation, filtered.
# ---------------------------------------------------------------------------

def _fake_honcho_with_rep(monkeypatch, rep_text, *, summary="prior summary",
                          messages=None, rep_raises=False, ctx_raises=False):
    """Faked honcho SDK: peer.aio.representation returns rep_text (or raises);
    session.aio.context returns the given summary + messages (or raises)."""
    import sys
    import types

    mod = _load()

    class _PeerAio:
        async def representation(self, **k):
            if rep_raises:
                raise RuntimeError("representation endpoint 500")
            return rep_text
        async def chat(self, q):  # unused here
            return ""

    class _Peer:
        def __init__(self, pid): self.id = pid; self.aio = _PeerAio()

    class _Ctx:
        def __init__(self):
            self.summary = types.SimpleNamespace(content=summary) if summary else None
            self.messages = messages or []

    class _SessAio:
        async def add_messages(self, *a, **k): return None
        async def context(self, **k):
            if ctx_raises:
                raise RuntimeError("context endpoint 500")
            return _Ctx()

    class _Session:
        def __init__(self, sid): self.id = sid; self.aio = _SessAio()

    class _ClientAio:
        async def peer(self, pid): return _Peer(pid)
        async def session(self, sid): return _Session(sid)

    class _Client:
        def __init__(self, api_key=None, base_url=None): self.aio = _ClientAio()

    fake = types.ModuleType("honcho")
    fake.Honcho = _Client
    fake.MessageCreateParams = lambda **k: types.SimpleNamespace(**k)
    monkeypatch.setitem(sys.modules, "honcho", fake)
    monkeypatch.setenv("HONCHO_API_KEY", "k")
    monkeypatch.setattr(mod.HonchoMemoryProvider, "is_available", lambda self: True)
    return mod


def test_recall_context_omits_user_model_when_explicit_only(monkeypatch):
    """Explicit-only representation (today's live state) → no user-model block,
    but the summary + recent messages still come through."""
    import asyncio
    import types
    rep = "## Explicit Observations\n[t] ulrich said Wow!"
    msgs = [types.SimpleNamespace(peer_id="ulrich", content="hello there")]
    mod = _fake_honcho_with_rep(monkeypatch, rep, summary="rolling summary", messages=msgs)
    p = mod.HonchoMemoryProvider()

    async def run():
        p.initialize("s1")
        out = await p.recall_context("q")
        assert "[user model]" not in out           # explicit noise filtered
        assert "rolling summary" in out
        assert "ulrich: hello there" in out

    asyncio.run(run())


def test_recall_context_injects_user_model_when_durable(monkeypatch):
    """Once the deriver produces deductive/inductive conclusions, they inject
    under [user model] ahead of the summary; the explicit layer stays dropped."""
    import asyncio
    rep = ("## Explicit Observations\n[t] ulrich said Wow!\n"
           "## Deductive Observations\n[t] ulrich values verification")
    mod = _fake_honcho_with_rep(monkeypatch, rep, summary="sum", messages=[])
    p = mod.HonchoMemoryProvider()

    async def run():
        p.initialize("s1")
        out = await p.recall_context("q")
        assert out.startswith("[user model]")
        assert "## Deductive Observations" in out
        assert "ulrich values verification" in out
        assert "Wow!" not in out                    # explicit dropped
        assert "sum" in out                         # summary preserved

    asyncio.run(run())


def test_recall_context_degrades_when_representation_raises(monkeypatch):
    """A representation-endpoint failure must not empty the whole context —
    it degrades to summary + messages (gather return_exceptions)."""
    import asyncio
    import types
    msgs = [types.SimpleNamespace(peer_id="ulrich", content="hi")]
    mod = _fake_honcho_with_rep(monkeypatch, "", summary="distinct-summary-42",
                                messages=msgs, rep_raises=True)
    p = mod.HonchoMemoryProvider()

    async def run():
        p.initialize("s1")
        out = await p.recall_context("q")           # rep 500 must not kill context
        assert "[user model]" not in out
        assert "distinct-summary-42" in out and "ulrich: hi" in out

    asyncio.run(run())


def test_recall_context_survives_context_failure(monkeypatch):
    """The mirror case: context() fails while representation() succeeds with
    durable conclusions — the user model must still inject on its own."""
    import asyncio
    rep = "## Deductive Observations\n[t] ulrich values verification"
    mod = _fake_honcho_with_rep(monkeypatch, rep, ctx_raises=True)
    p = mod.HonchoMemoryProvider()

    async def run():
        p.initialize("s1")
        out = await p.recall_context("q")           # context 500 must not drop the user model
        assert out.startswith("[user model]")
        assert "ulrich values verification" in out

    asyncio.run(run())


def test_durable_representation_drops_unknown_section():
    """Allowlist, not blocklist: an unknown/renamed section (future honcho
    image) is dropped, never injected — fail-safe for a prompt."""
    m = _load()
    rep = "## Peer Card\n[t] some new honcho section we don't recognize"
    assert m._durable_representation(rep) == ""


def test_durable_representation_drops_bare_heading():
    """A durable heading with no bullets is no signal → ""."""
    m = _load()
    assert m._durable_representation("## Deductive Observations\n\n") == ""


# ---------------------------------------------------------------------------
# search() — honcho semantic message retrieval (recall mode='search').
# ---------------------------------------------------------------------------

def _fake_honcho_with_search(monkeypatch, messages):
    """Faked honcho SDK whose client.aio.search returns the given messages."""
    import sys
    import types
    mod = _load()

    class _ClientAio:
        async def peer(self, pid): return types.SimpleNamespace(id=pid)
        async def session(self, sid): return types.SimpleNamespace(id=sid)
        async def search(self, query, limit=8): return messages

    class _Client:
        def __init__(self, api_key=None, base_url=None): self.aio = _ClientAio()

    fake = types.ModuleType("honcho")
    fake.Honcho = _Client
    fake.MessageCreateParams = lambda **k: types.SimpleNamespace(**k)
    monkeypatch.setitem(sys.modules, "honcho", fake)
    monkeypatch.setenv("HONCHO_API_KEY", "k")
    monkeypatch.setattr(mod.HonchoMemoryProvider, "is_available", lambda self: True)
    return mod


def test_search_is_async():
    p = _load().HonchoMemoryProvider()
    assert inspect.iscoroutinefunction(p.search)


def test_search_safe_when_uninitialized(monkeypatch):
    import asyncio
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    p = _load().HonchoMemoryProvider()
    assert asyncio.run(p.search("x")) == ""


def test_search_formats_messages(monkeypatch):
    import asyncio
    import types
    msgs = [types.SimpleNamespace(peer_id="ulrich", content="I asked about car chargers"),
            types.SimpleNamespace(peer_id="jarvis", content="Belkin inverters are reliable")]
    mod = _fake_honcho_with_search(monkeypatch, msgs)
    p = mod.HonchoMemoryProvider()

    async def run():
        p.initialize("s1")
        out = await p.search("vehicle power", limit=6)
        assert out == "ulrich: I asked about car chargers\njarvis: Belkin inverters are reliable"

    asyncio.run(run())


def test_search_redacts_capability_denials(monkeypatch):
    """Self-poisoning gate: a persisted jarvis capability-denial in the search
    results must be dropped, not replayed into context."""
    import asyncio
    import types
    msgs = [types.SimpleNamespace(peer_id="jarvis",
                                  content="I don't have memory of past conversations"),
            types.SimpleNamespace(peer_id="ulrich",
                                  content="we discussed the brain project")]
    mod = _fake_honcho_with_search(monkeypatch, msgs)
    p = mod.HonchoMemoryProvider()

    async def run():
        p.initialize("s1")
        out = await p.search("brain project", limit=6)
        assert "don't have memory" not in out.lower()        # denial dropped
        assert "we discussed the brain project" in out        # real message kept

    asyncio.run(run())


def test_search_empty_results(monkeypatch):
    import asyncio
    mod = _fake_honcho_with_search(monkeypatch, [])
    p = mod.HonchoMemoryProvider()

    async def run():
        p.initialize("s1")
        assert await p.search("nothing") == ""

    asyncio.run(run())


def test_search_skips_blank_content(monkeypatch):
    import asyncio
    import types
    msgs = [types.SimpleNamespace(peer_id="ulrich", content="   "),
            types.SimpleNamespace(peer_id="jarvis", content="real answer")]
    mod = _fake_honcho_with_search(monkeypatch, msgs)
    p = mod.HonchoMemoryProvider()

    async def run():
        p.initialize("s1")
        assert await p.search("q") == "jarvis: real answer"

    asyncio.run(run())


def test_search_truncates_long_content(monkeypatch):
    """Each message is capped so a few long turns can't flood voice context."""
    import asyncio
    import types
    long_text = "x" * 5000
    mod = _fake_honcho_with_search(
        monkeypatch, [types.SimpleNamespace(peer_id="jarvis", content=long_text)])
    p = mod.HonchoMemoryProvider()

    async def run():
        p.initialize("s1")
        out = await p.search("q")
        assert out.endswith("…")
        assert len(out) < 600           # ~400 cap + "jarvis: " + ellipsis, not 5000

    asyncio.run(run())


def test_search_keeps_user_capability_phrase(monkeypatch):
    """The denial gate is jarvis-side only: a user message matching the pattern
    is a real statement and must NOT be dropped (over-redaction guard)."""
    import asyncio
    import types
    msgs = [types.SimpleNamespace(peer_id="ulrich",
                                  content="I don't have memory of where I left my keys")]
    mod = _fake_honcho_with_search(monkeypatch, msgs)
    p = mod.HonchoMemoryProvider()

    async def run():
        p.initialize("s1")
        out = await p.search("keys")
        assert "keys" in out             # user's own words kept

    asyncio.run(run())


# ---------------------------------------------------------------------------
# _ensure_init retry — a transient blip at session start must not latch the
# whole session into no-op memory (regression: the old _init_attempted guard).
# ---------------------------------------------------------------------------

def test_ensure_init_retries_after_transient_failure(monkeypatch):
    import asyncio
    import sys
    import types

    mod = _load()
    calls = {"n": 0}

    class _Peer:
        def __init__(self, pid): self.id = pid

    class _SessAio:
        async def add_messages(self, *a, **k): return None

    class _Session:
        def __init__(self, sid): self.id = sid; self.aio = _SessAio()

    class _ClientAio:
        async def peer(self, pid):
            calls["n"] += 1
            if calls["n"] == 1:      # first ever peer call fails (honcho briefly down)
                raise RuntimeError("Connection error: All connection attempts failed")
            return _Peer(pid)
        async def session(self, sid): return _Session(sid)

    class _Client:
        def __init__(self, **k): self.aio = _ClientAio()

    fake = types.ModuleType("honcho")
    fake.Honcho = _Client
    fake.MessageCreateParams = lambda **k: types.SimpleNamespace(**k)
    monkeypatch.setitem(sys.modules, "honcho", fake)
    monkeypatch.setenv("HONCHO_API_KEY", "k")
    monkeypatch.setattr(mod.HonchoMemoryProvider, "is_available", lambda self: True)

    clock = {"t": 1000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])

    p = mod.HonchoMemoryProvider()

    async def run():
        p.initialize("s1")
        await p._ensure_init()                       # attempt 1 → fails
        assert p._session is None
        await p._ensure_init()                       # within backoff → suppressed
        assert p._session is None and calls["n"] == 1
        clock["t"] += mod._INIT_RETRY_BACKOFF_S + 1  # past backoff → retry
        await p._ensure_init()
        assert p._session is not None                # recovered mid-session
        assert p._peer_user.id == "ulrich"

    asyncio.run(run())

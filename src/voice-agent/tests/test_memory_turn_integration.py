# tests/test_memory_turn_integration.py — verifies the JarvisAgent turn-loop
# hooks call the memory_provider runtime, gated by is_recall_query, without
# disturbing existing turn handling. All assertions hold with the layer OFF
# (default) and prove the wiring exists + is correctly gated when ON.
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Fake provider plumbed through the real provider registry ──────────────
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
        return "RECALLED-CONTEXT"

    def recall(self, q):
        self.calls.append(("recall", q))
        return "DEEP"

    def sync_message(self, role, text):
        self.calls.append(("sync", role, text))

    def end_session(self):
        self.calls.append(("end",))


def _install(monkeypatch, prov):
    from tools import _provider_registry as pr
    pr.register_provider("memory", prov.name, prov)
    monkeypatch.setenv("JARVIS_MEMORY_PROVIDER", prov.name)


# ── Lightweight stand-ins for the LiveKit objects the hooks touch ─────────
class _FakeChatCtx:
    """Captures add_message calls (the auto-recall injection target)."""

    def __init__(self):
        self.added = []

    def add_message(self, *, role, content, **kw):
        self.added.append((role, content))


class _FakeMsg:
    def __init__(self, text):
        self._text = text

    def text_content(self):
        return self._text


class _FakeItem:
    def __init__(self, role, text):
        self.role = role
        self._text = text
        self.content = text

    def text_content(self):
        return self._text


# ── is_recall_query gating (the seam the auto-recall hook keys off) ───────
def test_is_recall_query_gating():
    from pipeline import turn_router
    assert turn_router.is_recall_query("what did I tell you about my dog") is True
    assert turn_router.is_recall_query("set a timer for 5 minutes") is False


# ── jarvis_agent imports clean (the 4 monkeypatches install) ──────────────
def test_jarvis_agent_imports_clean():
    import jarvis_agent  # noqa: F401
    # JarvisAgent must OWN these overrides (not merely inherit the base
    # Agent no-ops) — that's the wiring this task adds.
    assert "on_enter" in jarvis_agent.JarvisAgent.__dict__
    assert "on_exit" in jarvis_agent.JarvisAgent.__dict__
    assert "on_user_turn_completed" in jarvis_agent.JarvisAgent.__dict__


# ── on_enter/on_exit drive the runtime session lifecycle ──────────────────
def test_on_enter_begins_and_on_exit_ends_session(monkeypatch):
    import jarvis_agent
    from pipeline import memory_provider as mp
    f = _Fake()
    _install(monkeypatch, f)

    # Build a JarvisAgent without running its heavy __init__ (no LLM/tools);
    # we only exercise the lifecycle hooks, which read self.session.room_io.
    agent = jarvis_agent.JarvisAgent.__new__(jarvis_agent.JarvisAgent)

    class _Room:
        name = "room-xyz"

    class _RoomIO:
        room = _Room()

    class _Sess:
        room_io = _RoomIO()

    object.__setattr__(agent, "_session_obj_for_test", _Sess())
    # JarvisAgent.session is a read-only property on the base; patch the
    # class-level accessor to our fake for the duration of this test.
    monkeypatch.setattr(type(agent), "session",
                        property(lambda self: self._session_obj_for_test),
                        raising=False)

    asyncio.run(agent.on_enter())
    assert ("init", "room-xyz") in f.calls

    asyncio.run(agent.on_exit())
    assert ("end",) in f.calls


# ── Runtime is inert by default (layer OFF) ───────────────────────────────
def test_runtime_inert_when_flag_unset(monkeypatch):
    monkeypatch.delenv("JARVIS_MEMORY_PROVIDER", raising=False)
    from pipeline import memory_provider as mp
    assert mp.active_provider() is None
    # auto-recall path returns "" → nothing injected
    assert asyncio.run(mp.maybe_recall_for_turn("what did I tell you about X")) == ""


# ── Gated auto-recall fires ONLY on recall queries (layer ON) ─────────────
def test_auto_recall_only_on_recall_queries(monkeypatch):
    from pipeline import memory_provider as mp, turn_router
    f = _Fake()
    _install(monkeypatch, f)
    mp.begin_session("room-test")
    assert ("init", "room-test") in f.calls

    async def _drive(text):
        # mirror the jarvis_agent.on_user_turn_completed auto-recall snippet
        ctxobj = _FakeChatCtx()
        if mp.active_provider() is not None and turn_router.is_recall_query(text):
            ctx = await mp.maybe_recall_for_turn(text)
            if ctx:
                ctxobj.add_message(role="assistant", content=f"[memory] {ctx}")
        return ctxobj

    # recall-ish query → recall_context invoked + injected
    recall_ctx = asyncio.run(_drive("what did I tell you about my dog"))
    assert any(c[0] == "ctx" for c in f.calls)
    assert recall_ctx.added == [("assistant", "[memory] RECALLED-CONTEXT")]

    # non-recall command → no recall, no injection
    f.calls.clear()
    cmd_ctx = asyncio.run(_drive("set a timer for 5 minutes"))
    assert not any(c[0] == "ctx" for c in f.calls)
    assert cmd_ctx.added == []


# ── Per-item sync is fire-and-forget for user + assistant items ───────────
def test_per_item_sync_user_and_assistant(monkeypatch):
    from pipeline import memory_provider as mp
    f = _Fake()
    _install(monkeypatch, f)

    async def run():
        mp.begin_session("r")
        for item in (_FakeItem("user", "hello there"),
                     _FakeItem("assistant", "hi back")):
            role = getattr(item, "role", "") or ""
            item_text = ""
            try:
                item_text = item.text_content() or ""
            except Exception:
                item_text = ""
            if role in ("user", "assistant") and item_text.strip():
                mp.sync_item_async(role, item_text)
        await asyncio.sleep(0.05)  # let the fire-and-forget tasks run

    asyncio.run(run())
    assert ("sync", "user", "hello there") in f.calls
    assert ("sync", "assistant", "hi back") in f.calls


# ── Sync skips non-user/assistant + empty items ───────────────────────────
def test_per_item_sync_skips_irrelevant(monkeypatch):
    from pipeline import memory_provider as mp
    f = _Fake()
    _install(monkeypatch, f)

    async def run():
        mp.begin_session("r")
        for item in (_FakeItem("system", "boot"),
                     _FakeItem("user", "   ")):
            role = getattr(item, "role", "") or ""
            item_text = ""
            try:
                item_text = item.text_content() or ""
            except Exception:
                item_text = ""
            if role in ("user", "assistant") and item_text.strip():
                mp.sync_item_async(role, item_text)
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert not any(c[0] == "sync" for c in f.calls)

"""ACP adapter — round-trip + permission + cancel tests.

Each test wires the ``JarvisACPAgent`` against a stub LLM and a stub
tool surface, then drives it through the same async paths an IDE would
exercise. The supervisor + tool registry are NOT loaded, so the suite
stays fast (~tens of ms per test) and isolated from the live LLM and
disk state.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import types
from pathlib import Path
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


class _ToolInfo:
    """Stand-in for LiveKit ``RawFunctionTool.info`` — just exposes ``.name``."""

    def __init__(self, name: str) -> None:
        self.name = name


class _StubTool:
    """Test tool: keeps the calls received + returns a fixed JSON result."""

    def __init__(self, name: str, result: str = '{"ok": true}', is_async: bool = False) -> None:
        self.info = _ToolInfo(name)
        self.calls: list[dict] = []
        self._result = result
        self._is_async = is_async
        if is_async:
            self._callable = self._async_handler
        else:
            self._callable = self._sync_handler

    def _sync_handler(self, raw_arguments: dict) -> str:
        self.calls.append(raw_arguments)
        return self._result

    async def _async_handler(self, raw_arguments: dict) -> str:
        self.calls.append(raw_arguments)
        return self._result


class _StubDelta:
    """Mirrors livekit-agents ``ChoiceDelta`` shape."""

    def __init__(self, content: str | None = None, tool_calls: list | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []
        self.role = "assistant"


class _StubChunk:
    def __init__(self, delta: _StubDelta) -> None:
        self.delta = delta
        self.id = "x"
        self.usage = None


class _StubToolCall:
    def __init__(self, call_id: str, name: str, arguments: dict) -> None:
        self.call_id = call_id
        self.id = call_id
        self.name = name
        self.arguments = json.dumps(arguments) if not isinstance(arguments, str) else arguments


class _StubStream:
    """Async-iterable + async-context-managed stub for LLM.chat() output."""

    def __init__(self, chunks: list[_StubChunk]) -> None:
        self._chunks = chunks
        self._iter = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _StubLLM:
    """Test LLM: returns scripted streams across consecutive ``chat()`` calls."""

    def __init__(self, scripts: list[list[_StubChunk]]) -> None:
        self._scripts = list(scripts)
        self.calls = 0

    def chat(self, *, chat_ctx=None, tools=None, **kw):
        self.calls += 1
        if not self._scripts:
            return _StubStream([_StubChunk(_StubDelta(content=""))])
        return _StubStream(self._scripts.pop(0))


def _make_agent(scripts, tools=None, *, persist=False, monkeypatch=None):
    """Construct a JarvisACPAgent wired to stubs (no LLM/tool side effects)."""
    from acp_adapter.server import JarvisACPAgent
    from acp_adapter.session import SessionManager

    sm = SessionManager(persist=persist)
    tools = list(tools or [])
    agent = JarvisACPAgent(
        session_manager=sm,
        llm_builder=lambda: _StubLLM(scripts),
        tools_builder=lambda: tools,
    )
    return agent


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if asyncio._get_running_loop() else asyncio.run(coro)


# ---------------------------------------------------------------------------
# Initialize / session lifecycle
# ---------------------------------------------------------------------------


def test_initialize_returns_protocol_version(monkeypatch):
    """``initialize`` returns the ACP protocol version + agent info + capabilities."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-only")

    import acp

    agent = _make_agent(scripts=[])
    resp = asyncio.run(agent.initialize(protocol_version=1))
    assert resp.protocol_version == acp.PROTOCOL_VERSION
    assert resp.agent_info.name == "jarvis-agent"
    assert resp.agent_capabilities.load_session is True
    # At least the ``none`` auth method must be advertised.
    method_ids = {m.id for m in resp.auth_methods}
    assert "none" in method_ids


def test_session_new_returns_session_id():
    agent = _make_agent(scripts=[])
    resp = asyncio.run(agent.new_session(cwd="/tmp"))
    assert isinstance(resp.session_id, str) and len(resp.session_id) >= 8
    # The session manager should have the session in memory.
    assert agent.session_manager.get_session(resp.session_id) is not None


# ---------------------------------------------------------------------------
# Prompt streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_prompt_streams_assistant_text():
    """A pure-text supervisor reply should reach the IDE as ``agent_message_chunk``."""
    from acp_adapter.server import JarvisACPAgent
    from acp.schema import AgentMessageChunk

    scripts = [[
        _StubChunk(_StubDelta(content="Hello ")),
        _StubChunk(_StubDelta(content="from JARVIS.")),
    ]]
    agent = _make_agent(scripts=scripts)
    conn = AsyncMock()
    agent._conn = conn

    new_resp = await agent.new_session(cwd="/tmp")
    from acp.schema import TextContentBlock

    resp = await agent.prompt(
        prompt=[TextContentBlock(type="text", text="hi")],
        session_id=new_resp.session_id,
    )
    assert resp.stop_reason == "end_turn"

    # Inspect the session_update payloads that reached the connection.
    update_payloads = [c.kwargs.get("update", c.args[1] if len(c.args) >= 2 else None)
                       for c in conn.session_update.await_args_list]
    chunk_texts = []
    for u in update_payloads:
        if isinstance(u, AgentMessageChunk):
            block = u.content
            chunk_texts.append(getattr(block, "text", ""))
    assert "Hello " in chunk_texts
    assert "from JARVIS." in chunk_texts


@pytest.mark.asyncio
async def test_tool_call_round_trip():
    """A LLM that emits one tool_call should produce start+complete events
    and then a second LLM round responding with plain text."""
    from acp.schema import TextContentBlock, ToolCallStart, ToolCallProgress

    tool = _StubTool("read_file", result=json.dumps({"content": "hello"}))
    scripts = [
        # Round 1 — supervisor asks for the tool.
        [_StubChunk(_StubDelta(tool_calls=[
            _StubToolCall("call-1", "read_file", {"path": "/tmp/x.txt"})
        ]))],
        # Round 2 — supervisor wraps up with text.
        [_StubChunk(_StubDelta(content="Done."))],
    ]
    agent = _make_agent(scripts=scripts, tools=[tool])
    conn = AsyncMock()
    agent._conn = conn

    new_resp = await agent.new_session(cwd="/tmp")
    resp = await agent.prompt(
        prompt=[TextContentBlock(type="text", text="please read")],
        session_id=new_resp.session_id,
    )
    assert resp.stop_reason == "end_turn"

    # The tool got invoked exactly once with the args the LLM emitted.
    assert tool.calls == [{"path": "/tmp/x.txt"}]

    update_payloads = [c.kwargs.get("update", c.args[1] if len(c.args) >= 2 else None)
                       for c in conn.session_update.await_args_list]
    starts = [u for u in update_payloads if isinstance(u, ToolCallStart)]
    completes = [u for u in update_payloads if isinstance(u, ToolCallProgress)]
    assert len(starts) == 1
    assert len(completes) == 1
    assert starts[0].tool_call_id == "call-1"
    assert completes[0].status == "completed"


# ---------------------------------------------------------------------------
# Edit approval gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_file_requests_permission(tmp_path):
    """``write_file`` should trigger an ACP request_permission round-trip."""
    from acp.schema import TextContentBlock, AllowedOutcome

    target = tmp_path / "newfile.txt"
    tool = _StubTool("write_file", result=json.dumps({"success": True}))
    scripts = [
        [_StubChunk(_StubDelta(tool_calls=[
            _StubToolCall("call-1", "write_file",
                          {"path": str(target), "content": "Hello"})
        ]))],
        [_StubChunk(_StubDelta(content="Wrote it."))],
    ]
    agent = _make_agent(scripts=scripts, tools=[tool])

    # Fake conn: request_permission returns "Allow once".
    response = MagicMock()
    response.outcome = AllowedOutcome(outcome="selected", option_id="allow_once")
    conn = AsyncMock()
    conn.request_permission = AsyncMock(return_value=response)
    agent._conn = conn

    new_resp = await agent.new_session(cwd=str(tmp_path))
    await agent.prompt(
        prompt=[TextContentBlock(type="text", text="write the file")],
        session_id=new_resp.session_id,
    )

    # The IDE saw a request_permission call.
    assert conn.request_permission.await_count == 1
    # And the underlying tool was still invoked (approval allowed).
    assert tool.calls == [{"path": str(target), "content": "Hello"}]


@pytest.mark.asyncio
async def test_permission_denied_skips_tool_call(tmp_path):
    """When the IDE denies permission the tool must not run; an error
    message is fed back to the supervisor."""
    from acp.schema import TextContentBlock, AllowedOutcome

    target = tmp_path / "secret.txt"
    target.write_text("existing", encoding="utf-8")
    tool = _StubTool("write_file", result=json.dumps({"success": True}))
    scripts = [
        [_StubChunk(_StubDelta(tool_calls=[
            _StubToolCall("call-1", "write_file",
                          {"path": str(target), "content": "should not land"})
        ]))],
        [_StubChunk(_StubDelta(content="OK — skipped."))],
    ]
    agent = _make_agent(scripts=scripts, tools=[tool])

    response = MagicMock()
    response.outcome = AllowedOutcome(outcome="selected", option_id="deny")
    conn = AsyncMock()
    conn.request_permission = AsyncMock(return_value=response)
    agent._conn = conn

    new_resp = await agent.new_session(cwd=str(tmp_path))
    await agent.prompt(
        prompt=[TextContentBlock(type="text", text="try to write")],
        session_id=new_resp.session_id,
    )

    # The actual tool handler was never invoked.
    assert tool.calls == []
    # The session history records a tool result that surfaces the denial.
    state = agent.session_manager.get_session(new_resp.session_id)
    tool_msgs = [m for m in state.history if m.get("role") == "tool"]
    assert tool_msgs, "expected a tool result row even when denied"
    assert "denied" in (tool_msgs[0].get("content") or "").lower()


# ---------------------------------------------------------------------------
# Cancel + permissive mode + no-hermes guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_aborts_in_flight_prompt(monkeypatch):
    """``cancel`` flips the cancel event so the next iteration aborts."""
    from acp.schema import TextContentBlock

    tool = _StubTool("read_file", result=json.dumps({"content": "hi"}))
    scripts = [
        # Round 1 — issues a tool call. The cancel event fires AFTER this
        # round, before round 2 runs.
        [_StubChunk(_StubDelta(tool_calls=[
            _StubToolCall("call-1", "read_file", {"path": "/tmp/x"})
        ]))],
        # Round 2 — would emit more text if reached. Cancellation
        # should short-circuit before this call is consumed.
        [_StubChunk(_StubDelta(content="should not stream"))],
    ]
    agent = _make_agent(scripts=scripts, tools=[tool])
    agent._conn = AsyncMock()

    new_resp = await agent.new_session(cwd="/tmp")

    # Patch the dispatch tool call to set the cancel event after it runs.
    original = agent._dispatch_tool_call

    async def cancelling_dispatch(state, name, args, tools, loop):
        result = await original(state, name, args, tools, loop)
        state.cancel_event.set()
        return result

    agent._dispatch_tool_call = cancelling_dispatch

    resp = await agent.prompt(
        prompt=[TextContentBlock(type="text", text="please read")],
        session_id=new_resp.session_id,
    )
    assert resp.stop_reason == "cancelled"


@pytest.mark.asyncio
async def test_permissive_mode_skips_approval(monkeypatch, tmp_path):
    """``JARVIS_ACP_PERMISSIONS=permissive`` bypasses request_permission."""
    monkeypatch.setenv("JARVIS_ACP_PERMISSIONS", "permissive")
    from acp.schema import TextContentBlock

    target = tmp_path / "x.txt"
    tool = _StubTool("write_file", result=json.dumps({"success": True}))
    scripts = [
        [_StubChunk(_StubDelta(tool_calls=[
            _StubToolCall("call-1", "write_file",
                          {"path": str(target), "content": "ok"})
        ]))],
        [_StubChunk(_StubDelta(content="done"))],
    ]
    agent = _make_agent(scripts=scripts, tools=[tool])
    conn = AsyncMock()
    conn.request_permission = AsyncMock()
    agent._conn = conn

    new_resp = await agent.new_session(cwd=str(tmp_path))
    await agent.prompt(
        prompt=[TextContentBlock(type="text", text="write")],
        session_id=new_resp.session_id,
    )

    # No permission request fired.
    conn.request_permission.assert_not_awaited()
    # Tool ran.
    assert tool.calls == [{"path": str(target), "content": "ok"}]


# ---------------------------------------------------------------------------
# Regression tests — 2026-06 acp_adapter review fixes
# ---------------------------------------------------------------------------


class _RecordingLLM(_StubLLM):
    """StubLLM that records every chat_ctx it receives."""

    def __init__(self, scripts) -> None:
        super().__init__(scripts)
        self.chat_ctxs: list = []

    def chat(self, *, chat_ctx=None, tools=None, **kw):
        self.chat_ctxs.append(chat_ctx)
        return super().chat(chat_ctx=chat_ctx, tools=tools, **kw)


@pytest.mark.asyncio
async def test_queued_prompt_notification_not_under_runtime_lock():
    """The 'queued' notification must fire AFTER runtime_lock is released.

    runtime_lock is a threading.Lock; awaiting send_update while holding
    it lets a concurrent prompt()/cancel() block the loop thread on
    acquire — and then the holder can never resume to release. Deadlock.
    """
    from acp.schema import TextContentBlock

    agent = _make_agent(scripts=[])
    new_resp = await agent.new_session(cwd="/tmp")
    state = agent.session_manager.get_session(new_resp.session_id)
    state.is_running = True  # simulate an in-flight prompt

    lock_free_during_update: list[bool] = []

    class _LockProbeConn:
        async def session_update(self, *, session_id=None, update=None):
            acquired = state.runtime_lock.acquire(blocking=False)
            if acquired:
                state.runtime_lock.release()
            lock_free_during_update.append(acquired)

    agent._conn = _LockProbeConn()
    resp = await agent.prompt(
        prompt=[TextContentBlock(type="text", text="queued one")],
        session_id=new_resp.session_id,
    )
    assert resp.stop_reason == "end_turn"
    assert state.queued_prompts == ["queued one"]
    assert lock_free_during_update == [True]


@pytest.mark.asyncio
async def test_tool_results_survive_across_prompts():
    """Tool calls persist as function_call history rows, so the NEXT
    prompt's rebuilt chat_ctx carries paired call/output items (the
    provider formatter drops orphan outputs silently)."""
    from acp.schema import TextContentBlock
    from acp_adapter.server import JarvisACPAgent
    from acp_adapter.session import SessionManager
    from livekit.agents.llm import FunctionCall, FunctionCallOutput

    tool = _StubTool("read_file", result=json.dumps({"content": "hello"}))
    llm = _RecordingLLM([
        # Prompt 1, round 1 — tool call; round 2 — text.
        [_StubChunk(_StubDelta(tool_calls=[
            _StubToolCall("call-1", "read_file", {"path": "/tmp/x.txt"})
        ]))],
        [_StubChunk(_StubDelta(content="Done."))],
        # Prompt 2 — plain text; we inspect the ctx it receives.
        [_StubChunk(_StubDelta(content="Second."))],
    ])
    agent = JarvisACPAgent(
        session_manager=SessionManager(persist=False),
        llm_builder=lambda: llm,
        tools_builder=lambda: [tool],
    )
    agent._conn = AsyncMock()

    new_resp = await agent.new_session(cwd="/tmp")
    await agent.prompt(prompt=[TextContentBlock(type="text", text="read it")],
                       session_id=new_resp.session_id)

    state = agent.session_manager.get_session(new_resp.session_id)
    fc_rows = [m for m in state.history if m.get("role") == "function_call"]
    assert fc_rows and fc_rows[0]["call_id"] == "call-1"

    await agent.prompt(prompt=[TextContentBlock(type="text", text="again")],
                       session_id=new_resp.session_id)

    # llm.chat() calls: prompt1-round1, prompt1-round2, prompt2-round1.
    ctx2 = llm.chat_ctxs[2]
    items = list(ctx2.items)
    calls = [it for it in items if isinstance(it, FunctionCall)]
    outputs = [it for it in items if isinstance(it, FunctionCallOutput)]
    assert [c.call_id for c in calls] == ["call-1"]
    assert [o.call_id for o in outputs] == ["call-1"]


@pytest.mark.asyncio
async def test_legacy_orphan_tool_rows_skipped_on_rebuild():
    """Tool-result rows persisted by older builds (no function_call rows)
    must not be rebuilt as orphan FunctionCallOutput items."""
    from acp.schema import TextContentBlock
    from acp_adapter.server import JarvisACPAgent
    from acp_adapter.session import SessionManager
    from livekit.agents.llm import FunctionCallOutput

    llm = _RecordingLLM([[_StubChunk(_StubDelta(content="ok"))]])
    agent = JarvisACPAgent(
        session_manager=SessionManager(persist=False),
        llm_builder=lambda: llm,
        tools_builder=lambda: [],
    )
    agent._conn = AsyncMock()

    new_resp = await agent.new_session(cwd="/tmp")
    state = agent.session_manager.get_session(new_resp.session_id)
    state.history.extend([
        {"role": "user", "content": "old prompt"},
        {"role": "tool", "tool_call_id": "legacy-1",
         "tool_name": "read_file", "content": "{}"},
    ])

    await agent.prompt(prompt=[TextContentBlock(type="text", text="hi")],
                       session_id=new_resp.session_id)

    items = list(llm.chat_ctxs[0].items)
    assert not [it for it in items if isinstance(it, FunctionCallOutput)]


@pytest.mark.asyncio
async def test_edit_approval_targets_owning_session(tmp_path):
    """Edit approval must use the session that owns the tool call — not
    whichever running session a manager scan happens to find first."""
    from acp.schema import TextContentBlock, AllowedOutcome

    target = tmp_path / "owned.txt"
    tool = _StubTool("write_file", result=json.dumps({"success": True}))
    scripts = [
        [_StubChunk(_StubDelta(tool_calls=[
            _StubToolCall("call-1", "write_file",
                          {"path": str(target), "content": "Hello"})
        ]))],
        [_StubChunk(_StubDelta(content="Wrote it."))],
    ]
    agent = _make_agent(scripts=scripts, tools=[tool])

    response = MagicMock()
    response.outcome = AllowedOutcome(outcome="selected", option_id="allow_once")
    conn = AsyncMock()
    conn.request_permission = AsyncMock(return_value=response)
    agent._conn = conn

    # Decoy created FIRST (earlier in dict order), running, with an
    # auto-approve-everything policy. A first-running-session scan would
    # consult the decoy and silently auto-approve under ITS policy.
    decoy = await agent.new_session(cwd=str(tmp_path))
    decoy_state = agent.session_manager.get_session(decoy.session_id)
    decoy_state.is_running = True
    decoy_state.mode = "dont_ask"

    owner = await agent.new_session(cwd=str(tmp_path))  # default: ask
    await agent.prompt(
        prompt=[TextContentBlock(type="text", text="write the file")],
        session_id=owner.session_id,
    )

    assert conn.request_permission.await_count == 1
    kwargs = conn.request_permission.await_args.kwargs
    assert kwargs.get("session_id") == owner.session_id


def test_provider_sanitizers_install_helper():
    """The ACP process must install the provider-shape sanitizers itself —
    jarvis_agent.py's import-time installs never run here, and without
    anthropic_strict_schema every Anthropic tool request 400s."""
    from acp_adapter.server import _install_provider_sanitizers
    import sanitizers.anthropic_strict_schema as strict_schema

    _install_provider_sanitizers()
    _install_provider_sanitizers()  # idempotent
    assert strict_schema._INSTALLED is True


def test_no_hermes_token():
    """Hard guard: the new ACP code must not carry any 'hermes' identifier."""
    root = Path(__file__).resolve().parent.parent
    targets = [
        root / "acp_adapter",
        root / "acp_registry",
        root.parent.parent / "bin" / "jarvis-acp",
    ]
    found: list[tuple[Path, int, str]] = []
    pattern = re.compile(r"hermes", re.IGNORECASE)
    for t in targets:
        files: list[Path] = []
        if t.is_dir():
            files = [p for p in t.rglob("*") if p.is_file()]
        elif t.is_file():
            files = [t]
        for f in files:
            # Skip __pycache__ and binary artifacts.
            if "__pycache__" in f.parts:
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    found.append((f, i, line))
    assert not found, (
        "Found 'hermes' tokens in ported code:\n"
        + "\n".join(f"  {p}:{i}: {ln}" for p, i, ln in found)
    )

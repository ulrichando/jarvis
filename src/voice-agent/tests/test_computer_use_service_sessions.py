"""Detached computer-use runs: sessions registry, reattach, live mode flip, stop.

Covers the 2026-07-06 fixes:
  1. Auto mode never prompts + a mid-run flip to Auto releases pending approvals
     (supervision is read live per action, not snapshotted at run start).
  2. GET /sessions lists runs; GET /events replays + follows.
  3. A client disconnect no longer kills the run (JARVIS_CU_DETACH default-on).
"""
import asyncio
import json
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

import computer_use_service as svc
from pipeline.cu_adapters.base import StepResult, ToolCall


class FakeAdapter:
    """Scripted adapter: yields the given StepResults, then a bare finish."""

    def __init__(self, steps, step_gate=None):
        self._steps = list(steps)
        self._gate = step_gate  # optional asyncio.Event to hold next_step open
        self.results = []

    def seed(self, task, image_b64):
        pass

    async def next_step(self):
        if self._gate is not None:
            await self._gate.wait()
        if self._steps:
            return self._steps.pop(0)
        return StepResult(text="all done", calls=[])

    def add_results(self, results):
        self.results.extend(results)

    def export_history(self):
        return None  # skip persistence — not under test here

    def import_history(self, history):
        pass


@pytest.fixture()
def cu_env(monkeypatch):
    """Stub the desktop/provider surface so run_loop runs hermetically."""
    monkeypatch.setattr(svc, "x11_backend_available", lambda: True)
    monkeypatch.setattr(svc, "available_providers", lambda: {"anthropic": True})
    monkeypatch.setattr(svc, "provider_for", lambda model: "anthropic")
    monkeypatch.setattr(svc, "native_anthropic_cu", lambda model: False)
    monkeypatch.setattr(svc, "_ensure_frame", lambda mode="som": None)
    monkeypatch.setattr(svc, "_current_frame_b64", lambda max_px=None: None)
    monkeypatch.setattr(svc, "handle_computer_use", lambda args: json.dumps({"ok": True}))
    svc._RUNS.clear()
    svc._SESSIONS.clear()
    svc._APPROVED_KINDS.clear()
    svc._PENDING_APPROVALS.clear()
    yield
    for sess in svc._RUNS.values():
        if sess.runner is not None and not sess.runner.done():
            sess.runner.cancel()
    svc._RUNS.clear()


def _adapter_factory(monkeypatch, *adapters):
    """Patch make_adapter to hand out the given adapters in order."""
    queue = list(adapters)
    monkeypatch.setattr(svc, "make_adapter", lambda model, system: queue.pop(0))


async def _client():
    client = TestClient(TestServer(svc.build_app()))
    await client.start_server()
    return client


async def _read_events(resp, until_types=("done", "error", "stopped"), timeout=10):
    """Collect data: events from an SSE response until a terminal type (or EOF)."""
    events = []

    async def _reader():
        while True:
            line = await resp.content.readline()
            if not line:
                return
            text = line.decode()
            if not text.startswith("data:"):
                continue
            ev = json.loads(text[5:].strip())
            events.append(ev)
            if ev.get("type") in until_types:
                return

    await asyncio.wait_for(_reader(), timeout)
    return events


async def _wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met within timeout")


CLICK = StepResult(text=None, calls=[ToolCall("c1", "click", {"element": 1})])
TYPE = StepResult(text=None, calls=[ToolCall("c2", "type", {"text": "hi"})])


def test_auto_mode_never_prompts(cu_env, monkeypatch):
    """Issue 1 regression: supervised=false must execute without any prompt."""
    _adapter_factory(monkeypatch, FakeAdapter([CLICK]))

    async def main():
        client = await _client()
        try:
            resp = await client.post("/run", json={
                "task": "click it", "session_id": "s-auto", "supervised": False,
            })
            events = await _read_events(resp)
        finally:
            await client.close()
        types = [e["type"] for e in events]
        assert "permission_request" not in types
        assert "action" in types and types[-1] == "done"

    asyncio.run(main())


def test_supervised_prompts_and_approve_resolves(cu_env, monkeypatch):
    _adapter_factory(monkeypatch, FakeAdapter([CLICK]))

    async def main():
        client = await _client()
        try:
            resp = await client.post("/run", json={
                "task": "click it", "session_id": "s-sup", "supervised": True,
            })
            head = await _read_events(resp, until_types=("permission_request",))
            rid = head[-1]["id"]
            ok = await client.post("/approve", json={"request_id": rid, "decision": "once"})
            assert (await ok.json())["ok"] is True
            tail = await _read_events(resp)
        finally:
            await client.close()
        types = [e["type"] for e in tail]
        assert "approval_resolved" in types  # buffered for reattaching clients
        assert "action" in types and types[-1] == "done"

    asyncio.run(main())


def test_mode_flip_mid_run_releases_pending_and_stops_prompting(cu_env, monkeypatch):
    """Issue 1, mid-run: clicking Auto while a prompt is pending releases it AND
    no later action kind prompts again."""
    _adapter_factory(monkeypatch, FakeAdapter([CLICK, TYPE]))

    async def main():
        client = await _client()
        try:
            resp = await client.post("/run", json={
                "task": "click then type", "session_id": "s-flip", "supervised": True,
            })
            head = await _read_events(resp, until_types=("permission_request",))
            assert head[-1]["type"] == "permission_request"
            flip = await client.post("/mode", json={"session_id": "s-flip", "supervised": False})
            body = await flip.json()
            assert body["ok"] is True and len(body["resolved"]) == 1
            tail = await _read_events(resp)
        finally:
            await client.close()
        types = [e["type"] for e in tail]
        assert "permission_request" not in types  # the type-text action ran unprompted
        assert types.count("action") == 2
        assert types[-1] == "done"

    asyncio.run(main())


def test_disconnect_does_not_kill_run(cu_env, monkeypatch):
    """Issue 3 regression: closing the browser mid-run leaves the run going;
    /sessions + /events expose it afterward. (detach:true = the web route.)"""
    gate = asyncio.Event()

    async def main():
        adapter = FakeAdapter([CLICK], step_gate=gate)
        _adapter_factory(monkeypatch, adapter)
        client = await _client()
        try:
            resp = await client.post("/run", json={
                "task": "long job", "session_id": "s-detach", "supervised": False,
                "detach": True,
            })
            head = await _read_events(resp, until_types=("start",))
            assert head[-1]["type"] == "start"
            resp.close()  # browser closed

            sess = svc._RUNS["s-detach"]
            assert sess.running(), "runner must survive the disconnect"
            gate.set()  # let the model step proceed AFTER the client is gone
            await _wait_for(lambda: not sess.running())

            listing = await (await client.get("/sessions")).json()
            row = next(s for s in listing["sessions"] if s["id"] == "s-detach")
            assert row["status"] == "idle" and row["task"] == "long job"

            replay = await client.get("/events", params={"session_id": "s-detach", "after": "-1"})
            events = await _read_events(replay)
            types = [e["type"] for e in events]
            assert types[0] == "start" and "action" in types and types[-1] == "done"
        finally:
            await client.close()

    asyncio.run(main())


def test_cli_shape_disconnect_cancels_run(cu_env, monkeypatch):
    """No detach flag (the CLI's /run shape) keeps the legacy tie: Ctrl-C /
    connection drop still stops the desktop run."""
    gate = asyncio.Event()

    async def main():
        _adapter_factory(monkeypatch, FakeAdapter([CLICK], step_gate=gate))
        client = await _client()
        try:
            resp = await client.post("/run", json={
                "task": "long job", "session_id": "s-cli", "supervised": False,
            })
            await _read_events(resp, until_types=("start",))
            resp.close()
            sess = svc._RUNS["s-cli"]
            await _wait_for(lambda: not sess.running())
            assert any(e["type"] == "stopped" for e in sess.events)
        finally:
            await client.close()

    asyncio.run(main())


def test_detach_killswitch_overrides_request(cu_env, monkeypatch):
    """JARVIS_CU_DETACH=0 forces the legacy tie even when detach:true is sent."""
    monkeypatch.setenv("JARVIS_CU_DETACH", "0")
    gate = asyncio.Event()

    async def main():
        _adapter_factory(monkeypatch, FakeAdapter([CLICK], step_gate=gate))
        client = await _client()
        try:
            resp = await client.post("/run", json={
                "task": "long job", "session_id": "s-legacy", "supervised": False,
                "detach": True,
            })
            await _read_events(resp, until_types=("start",))
            resp.close()
            sess = svc._RUNS["s-legacy"]
            await _wait_for(lambda: not sess.running())
            assert any(e["type"] == "stopped" for e in sess.events)
        finally:
            await client.close()

    asyncio.run(main())


def test_approval_timeout_buffers_resolution(cu_env, monkeypatch):
    """An unanswered prompt times out to deny AND buffers approval_resolved so
    a reattaching client doesn't render a stale, still-actionable card."""
    monkeypatch.setattr(svc, "_APPROVAL_TIMEOUT_S", 0.05)
    _adapter_factory(monkeypatch, FakeAdapter([CLICK]))

    async def main():
        client = await _client()
        try:
            resp = await client.post("/run", json={
                "task": "click it", "session_id": "s-timeout", "supervised": True,
            })
            events = await _read_events(resp)
        finally:
            await client.close()
        types = [e["type"] for e in events]
        i_req = types.index("permission_request")
        i_res = types.index("approval_resolved")
        assert i_res > i_req
        assert events[i_res]["decision"] == "deny"
        assert "denied" in types and types[-1] == "done"

    asyncio.run(main())


def test_stop_endpoint_cancels_run(cu_env, monkeypatch):
    gate = asyncio.Event()

    async def main():
        _adapter_factory(monkeypatch, FakeAdapter([CLICK], step_gate=gate))
        client = await _client()
        try:
            resp = await client.post("/run", json={
                "task": "long job", "session_id": "s-stop", "supervised": False,
            })
            await _read_events(resp, until_types=("start",))
            stop = await client.post("/stop", json={"session_id": "s-stop"})
            assert (await stop.json())["ok"] is True
            tail = await _read_events(resp, until_types=("stopped",))
            assert tail[-1]["type"] == "stopped"
            missing = await client.post("/stop", json={"session_id": "s-stop"})
            assert missing.status == 404
        finally:
            await client.close()

    asyncio.run(main())


def test_second_run_on_busy_session_rejected(cu_env, monkeypatch):
    gate = asyncio.Event()

    async def main():
        _adapter_factory(monkeypatch, FakeAdapter([CLICK], step_gate=gate))
        client = await _client()
        try:
            resp = await client.post("/run", json={
                "task": "first", "session_id": "s-busy", "supervised": False,
            })
            await _read_events(resp, until_types=("start",))
            dup = await client.post("/run", json={
                "task": "second", "session_id": "s-busy", "supervised": False,
            })
            events = await _read_events(dup, until_types=("error",))
            assert "already in progress" in events[-1]["error"]
            gate.set()
            await _read_events(resp)
        finally:
            await client.close()

    asyncio.run(main())


def test_events_replay_respects_after(cu_env, monkeypatch):
    _adapter_factory(monkeypatch, FakeAdapter([CLICK]))

    async def main():
        client = await _client()
        try:
            resp = await client.post("/run", json={
                "task": "click it", "session_id": "s-after", "supervised": False,
            })
            all_events = await _read_events(resp)
            cut = all_events[1]["seq"]
            replay = await client.get("/events", params={"session_id": "s-after", "after": str(cut)})
            later = await _read_events(replay)
            assert all(e["seq"] > cut for e in later)
            assert later[-1]["type"] == "done"
            missing = await client.get("/events", params={"session_id": "nope"})
            assert missing.status == 404
        finally:
            await client.close()

    asyncio.run(main())


def test_eviction_caps_registry_and_cleans_siblings(cu_env):
    for i in range(svc._MAX_RUNS + 5):
        sid = f"s-{i}"
        sess = svc._Sess(sid)
        sess.updated = i  # oldest first
        svc._RUNS[sid] = sess
        svc._SESSIONS[sid] = {"anthropic": []}
        svc._APPROVED_KINDS[sid] = {"mouse"}
    svc._evict_runs()
    assert len(svc._RUNS) == svc._MAX_RUNS
    assert "s-0" not in svc._RUNS and "s-0" not in svc._SESSIONS
    assert "s-0" not in svc._APPROVED_KINDS
    assert f"s-{svc._MAX_RUNS + 4}" in svc._RUNS

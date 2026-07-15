"""Tests for pipeline/cloud_memory.py — the JARVIS_CLOUD_MEMORY repoint.

Covers the hard constraints of the cloud-memory design:
  1. FLAG-GATED: with JARVIS_CLOUD_MEMORY unset, enabled() is False, the
     memory tool never touches the cloud client, and active_provider()
     resolution is unchanged.
  2. OFFLINE RESILIENCE: transport errors on the cloud path fall back to the
     local file store / local provider; the 60 s backoff short-circuits
     subsequent calls.
  3. FROZEN SNAPSHOT: the cloud snapshot is fetched once and stays constant
     for the session regardless of what the "server" later serves.
  4. LOCAL MIRROR: successful cloud writes replay into the local files;
     mirror_entries writes cloud-fetched entries verbatim.

All HTTP is mocked (httpx.Client / httpx.AsyncClient monkeypatched); each
test isolates the file store under a tmp JARVIS_HOME.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import cloud_memory, file_memory, memory_provider
from pipeline.file_memory import ENTRY_DELIMITER
from tools.memory import _handle_memory

SEP = "═" * 46

_HEADERS = {
    "user": "USER PROFILE (who the user is) [1% — 10/2,000 chars]",
    "memory": "MEMORY (your durable notes) [1% — 10/3,000 chars]",
    "procedure": "PROCEDURES (named multi-step processes) [0% — 10/8,000 chars]",
}


def _render_block(target: str, entries: list[str]) -> str:
    return f"{SEP}\n{_HEADERS[target]}\n{SEP}\n{ENTRY_DELIMITER.join(entries)}"


def _snapshot(user=(), memory=(), procedure=()) -> dict:
    ub = _render_block("user", list(user)) if user else ""
    mb = _render_block("memory", list(memory)) if memory else ""
    pb = _render_block("procedure", list(procedure)) if procedure else ""
    return {
        "user_block": ub,
        "memory_block": mb,
        "procedure_block": pb,
        "snapshot_text": "\n\n".join(b for b in (ub, mb, pb) if b),
    }


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def _install_http(monkeypatch, handler):
    """Replace httpx.Client/AsyncClient in cloud_memory with fakes that route
    every call through handler(method, url, kwargs) → _FakeResponse|raise."""

    class _C:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            return handler("GET", url, kw)

        def post(self, url, **kw):
            return handler("POST", url, kw)

    class _AC:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            return handler("GET", url, kw)

        async def post(self, url, **kw):
            return handler("POST", url, kw)

    monkeypatch.setattr(cloud_memory.httpx, "Client", _C)
    monkeypatch.setattr(cloud_memory.httpx, "AsyncClient", _AC)


class _FakeFallback:
    """Sync stand-in for the local (honcho) provider."""

    name = "fake-local"

    def __init__(self):
        self.calls = []

    def is_available(self):
        return True

    def initialize(self, sid):
        self.calls.append(("init", sid))

    def recall(self, query):
        self.calls.append(("recall", query))
        return "LOCAL-RECALL"

    def recall_context(self, hint=""):
        self.calls.append(("ctx", hint))
        return "LOCAL-CTX"

    def sync_message(self, role, text):
        self.calls.append(("sync", role, text))

    def end_session(self):
        self.calls.append(("end",))


@pytest.fixture
def cloud_env(tmp_path, monkeypatch):
    """Flag ON + full config + clean tmp file store + clean module state."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_CLOUD_MEMORY", "1")
    monkeypatch.setenv("JARVIS_CLOUD_USER_ID", "test-user-uuid")
    monkeypatch.setenv("JARVIS_PROXY_JWT_SECRET", "test-secret")
    monkeypatch.delenv("JARVIS_CLOUD_MEMORY_URL", raising=False)
    cloud_memory._reset_for_tests()
    file_memory.reload_store()
    yield tmp_path
    cloud_memory._reset_for_tests()


@pytest.fixture
def local_env(tmp_path, monkeypatch):
    """Flag OFF (default) + clean tmp file store."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("JARVIS_CLOUD_MEMORY", raising=False)
    monkeypatch.delenv("JARVIS_CLOUD_USER_ID", raising=False)
    cloud_memory._reset_for_tests()
    file_memory.reload_store()
    yield tmp_path
    cloud_memory._reset_for_tests()


# ── 1. Flag gate ──────────────────────────────────────────────────────


def test_enabled_false_by_default(local_env):
    assert cloud_memory.enabled() is False


def test_enabled_requires_user_id_and_secret(cloud_env, monkeypatch):
    assert cloud_memory.enabled() is True
    monkeypatch.delenv("JARVIS_CLOUD_USER_ID")
    assert cloud_memory.enabled() is False
    monkeypatch.setenv("JARVIS_CLOUD_USER_ID", "u")
    monkeypatch.delenv("JARVIS_PROXY_JWT_SECRET")
    assert cloud_memory.enabled() is False


def test_flag_off_memory_tool_never_touches_cloud(local_env, monkeypatch):
    def _boom(*a, **kw):  # pragma: no cover - called = regression
        raise AssertionError("cloud path executed with flag off")

    monkeypatch.setattr(cloud_memory, "post_memory_tool", _boom)
    out = json.loads(_handle_memory({"action": "add", "target": "user", "content": "Flag-off local fact."}))
    assert out["success"] is True
    assert "Flag-off local fact." in (local_env / "memories" / "USER.md").read_text(encoding="utf-8")


def test_flag_off_active_provider_unchanged(local_env, monkeypatch):
    fake = _FakeFallback()
    monkeypatch.setattr(memory_provider, "active_provider_name", lambda: "fake-local")
    monkeypatch.setattr(
        memory_provider.provider_registry, "get_provider", lambda kind, name: fake
    )
    assert memory_provider.active_provider() is fake
    monkeypatch.setattr(memory_provider, "active_provider_name", lambda: None)
    assert memory_provider.active_provider() is None


def test_flag_on_wraps_local_provider(cloud_env, monkeypatch):
    fake = _FakeFallback()
    monkeypatch.setattr(memory_provider, "active_provider_name", lambda: "fake-local")
    monkeypatch.setattr(
        memory_provider.provider_registry, "get_provider", lambda kind, name: fake
    )
    prov = memory_provider.active_provider()
    assert isinstance(prov, cloud_memory.CloudMemoryProvider)
    assert prov._fallback is fake
    # No local provider configured → cloud provider still returned (fallback None).
    monkeypatch.setattr(memory_provider, "active_provider_name", lambda: None)
    prov2 = memory_provider.active_provider()
    assert isinstance(prov2, cloud_memory.CloudMemoryProvider)
    assert prov2._fallback is None


# ── 2. Frozen snapshot ────────────────────────────────────────────────


def test_snapshot_frozen_across_calls(cloud_env, monkeypatch):
    served = {"snap": _snapshot(user=["Ulrich fact one."], memory=["Note A."])}
    calls = {"n": 0}

    def handler(method, url, kw):
        calls["n"] += 1
        return _FakeResponse(payload=served["snap"])

    _install_http(monkeypatch, handler)
    assert cloud_memory.load_frozen_snapshot() is True
    first = cloud_memory.frozen_snapshot_text()
    assert "Ulrich fact one." in first and "Note A." in first
    # The "server" changes; the frozen text must NOT (no re-fetch mid-session).
    served["snap"] = _snapshot(user=["CHANGED."])
    assert cloud_memory.frozen_snapshot_text() == first
    assert cloud_memory.frozen_snapshot_text() == first
    assert calls["n"] == 1


def test_frozen_block_entries_parse_and_empty_guard(cloud_env, monkeypatch):
    snap = _snapshot(
        user=["Fact A.", "Multi\nline fact B."],
        procedure=["## deploy-app\n1. step"],
    )
    _install_http(monkeypatch, lambda m, u, kw: _FakeResponse(payload=snap))
    assert cloud_memory.load_frozen_snapshot() is True
    assert cloud_memory.frozen_block_entries("user") == ["Fact A.", "Multi\nline fact B."]
    assert cloud_memory.frozen_block_entries("procedure") == ["## deploy-app\n1. step"]
    # Empty cloud store → None (never mirror an empty block over local files).
    assert cloud_memory.frozen_block_entries("memory") is None


def test_snapshot_fetch_failure_returns_false(cloud_env, monkeypatch):
    def handler(method, url, kw):
        raise RuntimeError("connect timeout")

    _install_http(monkeypatch, handler)
    assert cloud_memory.load_frozen_snapshot() is False
    assert cloud_memory.frozen_snapshot_text() is None


# ── 3. Local mirror ───────────────────────────────────────────────────


def test_mirror_entries_writes_files_verbatim(cloud_env):
    file_memory.mirror_entries("user", ["Cloud fact.", "Second fact."])
    raw = (cloud_env / "memories" / "USER.md").read_text(encoding="utf-8")
    assert raw == "Cloud fact." + ENTRY_DELIMITER + "Second fact."
    store = file_memory.reload_store()
    assert store.user_entries == ["Cloud fact.", "Second fact."]


def test_mirror_entries_rejects_bad_target(cloud_env):
    with pytest.raises(ValueError):
        file_memory.mirror_entries("nope", ["x"])


# ── 4. Memory tool: cloud-first + fallback ────────────────────────────


def test_memory_tool_cloud_success_mirrors_local(cloud_env, monkeypatch):
    posted = {}

    def handler(method, url, kw):
        assert method == "POST" and url.endswith("/api/memory")
        posted.update(kw["json"])
        return _FakeResponse(
            payload={"success": True, "target": "user", "entries": ["New fact."], "message": "Entry added."}
        )

    _install_http(monkeypatch, handler)
    out = json.loads(_handle_memory({"action": "add", "target": "user", "content": "New fact."}))
    assert out["success"] is True
    assert posted["user_id"] == "test-user-uuid"
    assert posted["content"] == "New fact."
    # Mirror-on-success: the local fallback file is warm.
    assert "New fact." in (cloud_env / "memories" / "USER.md").read_text(encoding="utf-8")


def test_memory_tool_procedure_name_sent_raw_but_mirrored_with_heading(cloud_env, monkeypatch):
    posted = {}

    def handler(method, url, kw):
        posted.update(kw["json"])
        return _FakeResponse(payload={"success": True, "target": "procedure"})

    _install_http(monkeypatch, handler)
    _handle_memory({"action": "add", "target": "procedure", "name": "deploy-app", "content": "1. step"})
    # Cloud gets the RAW name + content (server does its own heading handling)…
    assert posted["name"] == "deploy-app"
    assert posted["content"] == "1. step"
    # …the local mirror replays the LOCAL contract (## heading prepend).
    raw = (cloud_env / "memories" / "PROCEDURES.md").read_text(encoding="utf-8")
    assert raw.startswith("## deploy-app\n1. step")


def test_memory_tool_transport_error_falls_back_to_local(cloud_env, monkeypatch):
    def handler(method, url, kw):
        raise RuntimeError("connection refused")

    _install_http(monkeypatch, handler)
    out = json.loads(_handle_memory({"action": "add", "target": "user", "content": "Offline fact."}))
    # Local path served the write — the session never breaks offline.
    assert out["success"] is True
    assert "Offline fact." in (cloud_env / "memories" / "USER.md").read_text(encoding="utf-8")


def test_memory_tool_contract_failure_returned_verbatim_no_local_write(cloud_env, monkeypatch):
    err = {"success": False, "error": "Memory at 2,000/2,000 chars."}
    _install_http(monkeypatch, lambda m, u, kw: _FakeResponse(payload=err))
    out = json.loads(_handle_memory({"action": "add", "target": "user", "content": "Over budget."}))
    assert out == err
    # A contract failure is a tool result, NOT a transport error: no local
    # fallback write happened.
    assert not (cloud_env / "memories" / "USER.md").exists() or (
        "Over budget." not in (cloud_env / "memories" / "USER.md").read_text(encoding="utf-8")
    )


# ── 5. Recall provider: cloud-first + local fallback ──────────────────


def test_provider_recall_cloud_primary(cloud_env, monkeypatch):
    fake = _FakeFallback()
    prov = cloud_memory.CloudMemoryProvider(fallback=fake)
    prov.initialize("sess-1")

    async def _cloud_ok(query):
        return "CLOUD-RECALL"

    monkeypatch.setattr(cloud_memory, "recall_query", _cloud_ok)
    assert asyncio.run(prov.recall("what do you know")) == "CLOUD-RECALL"
    assert ("recall", "what do you know") not in fake.calls


def test_provider_recall_falls_back_on_cloud_miss(cloud_env, monkeypatch):
    fake = _FakeFallback()
    prov = cloud_memory.CloudMemoryProvider(fallback=fake)
    prov.initialize("sess-1")

    async def _cloud_miss(query):
        return None

    monkeypatch.setattr(cloud_memory, "recall_query", _cloud_miss)
    assert asyncio.run(prov.recall("q")) == "LOCAL-RECALL"
    assert ("recall", "q") in fake.calls


def test_provider_recall_context_is_local_only(cloud_env, monkeypatch):
    fake = _FakeFallback()
    prov = cloud_memory.CloudMemoryProvider(fallback=fake)

    async def _boom(*a, **kw):  # pragma: no cover - called = regression
        raise AssertionError("recall_context must not hit the cloud (1.5s budget)")

    monkeypatch.setattr(cloud_memory, "recall_query", _boom)
    assert asyncio.run(prov.recall_context("hint")) == "LOCAL-CTX"
    assert ("ctx", "hint") in fake.calls


def test_provider_sync_cloud_then_local_fallback(cloud_env, monkeypatch):
    fake = _FakeFallback()
    prov = cloud_memory.CloudMemoryProvider(fallback=fake)
    prov.initialize("sess-9")
    seen = {}

    async def _sync_ok(role, text, sid):
        seen["args"] = (role, text, sid)
        return True

    monkeypatch.setattr(cloud_memory, "recall_sync", _sync_ok)
    asyncio.run(prov.sync_message("user", "hello"))
    assert seen["args"] == ("user", "hello", "sess-9")
    assert not [c for c in fake.calls if c[0] == "sync"]  # cloud took it

    async def _sync_fail(role, text, sid):
        return False

    monkeypatch.setattr(cloud_memory, "recall_sync", _sync_fail)
    asyncio.run(prov.sync_message("assistant", "offline turn"))
    assert ("sync", "assistant", "offline turn") in fake.calls  # local took it


# ── 6. Offline journal-and-replay ─────────────────────────────────────
#
# Data-loss fix: an offline curated write lands locally + journals its exact
# tool args; the next ONLINE session start replays the journal against the
# cloud BEFORE mirroring (then RE-fetches so the mirror includes it). A
# replay that is still offline keeps the journal AND skips mirroring for the
# affected target so the pending write is never clobbered.


def _journal_file(home) -> Path:
    return home / "cloud-memory-pending.jsonl"


def _fail_all(method, url, kw):
    raise RuntimeError("connection refused")


def test_offline_write_journals_exact_args(cloud_env, monkeypatch):
    _install_http(monkeypatch, _fail_all)
    out = json.loads(_handle_memory({"action": "add", "target": "user", "content": "Offline fact."}))
    assert out["success"] is True  # local fallback served the write
    lines = _journal_file(cloud_env).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"action": "add", "target": "user", "content": "Offline fact."}


def test_offline_procedure_journals_raw_args_not_heading(cloud_env, monkeypatch):
    _install_http(monkeypatch, _fail_all)
    _handle_memory({"action": "add", "target": "procedure", "name": "deploy-app", "content": "1. step"})
    line = json.loads(_journal_file(cloud_env).read_text(encoding="utf-8").strip())
    # The cloud contract wants the RAW name + content (server does its own
    # heading handling) — never the locally-mutated "## deploy-app\n…".
    assert line == {"action": "add", "target": "procedure", "content": "1. step", "name": "deploy-app"}


def test_contract_failure_does_not_journal(cloud_env, monkeypatch):
    err = {"success": False, "error": "Memory at 2,000/2,000 chars."}
    _install_http(monkeypatch, lambda m, u, kw: _FakeResponse(payload=err))
    out = json.loads(_handle_memory({"action": "add", "target": "user", "content": "Over budget."}))
    assert out == err
    # HTTP-200 success:false is an authoritative rejection, not a lost write.
    assert not _journal_file(cloud_env).exists()


def test_offline_local_reject_does_not_journal(cloud_env, monkeypatch):
    _install_http(monkeypatch, _fail_all)
    out = json.loads(_handle_memory({"action": "remove", "target": "user", "old_text": "never existed"}))
    # The local fallback rejected too — nothing landed anywhere, nothing to replay.
    assert out["success"] is False
    assert not _journal_file(cloud_env).exists()


def test_next_online_session_replays_then_mirrors(cloud_env, monkeypatch):
    # Session 1 (offline): write lands locally + journals.
    _install_http(monkeypatch, _fail_all)
    _handle_memory({"action": "add", "target": "user", "content": "Offline fact."})
    assert _journal_file(cloud_env).exists()

    # Session 2 (online): new process → clean module state (backoff etc.).
    cloud_memory._reset_for_tests()
    server = {"user": ["Cloud-only fact."], "posts": []}

    def handler(method, url, kw):
        if method == "GET":
            return _FakeResponse(payload=_snapshot(user=server["user"]))
        payload = kw["json"]
        server["posts"].append(payload)
        server["user"] = server["user"] + [payload["content"]]
        return _FakeResponse(payload={"success": True, "message": "Entry added."})

    _install_http(monkeypatch, handler)
    assert cloud_memory.sync_at_session_start() is True

    # Replay POSTed the exact journaled args (+ user_id), IN ORDER, once.
    assert server["posts"] == [
        {"user_id": "test-user-uuid", "action": "add", "target": "user", "content": "Offline fact."}
    ]
    # Journal drained.
    assert not _journal_file(cloud_env).exists()
    # Snapshot was RE-fetched AFTER the clean replay → frozen text includes
    # the replayed write…
    frozen = cloud_memory.frozen_snapshot_text()
    assert "Offline fact." in frozen and "Cloud-only fact." in frozen
    # …and the local mirror includes it too (no clobber).
    raw = (cloud_env / "memories" / "USER.md").read_text(encoding="utf-8")
    assert "Offline fact." in raw and "Cloud-only fact." in raw


def test_replay_still_offline_keeps_journal_and_never_clobbers_local(cloud_env, monkeypatch):
    # Session 1 (offline): journaled write.
    _install_http(monkeypatch, _fail_all)
    _handle_memory({"action": "add", "target": "user", "content": "Offline fact."})

    # Session 2: snapshot GET succeeds, but the network dies for the POSTs.
    cloud_memory._reset_for_tests()

    def handler(method, url, kw):
        if method == "GET":
            return _FakeResponse(payload=_snapshot(user=["Cloud-only fact."], memory=["Cloud note."]))
        raise RuntimeError("network died")

    _install_http(monkeypatch, handler)
    assert cloud_memory.sync_at_session_start() is True  # snapshot still froze

    # Journal intact — retried next session.
    lines = _journal_file(cloud_env).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["content"] == "Offline fact."
    # The affected target ('user') was NOT mirrored — the pending offline
    # write survives locally and the cloud-only state did not clobber it.
    raw = (cloud_env / "memories" / "USER.md").read_text(encoding="utf-8")
    assert "Offline fact." in raw
    assert "Cloud-only fact." not in raw
    # Unaffected targets still mirror normally.
    mem = (cloud_env / "memories" / "MEMORY.md").read_text(encoding="utf-8")
    assert mem == "Cloud note."


def test_replay_contract_rejection_drops_line(cloud_env, monkeypatch):
    # Journal a write, then have the cloud reject it authoritatively on replay.
    _install_http(monkeypatch, _fail_all)
    _handle_memory({"action": "add", "target": "user", "content": "Offline fact."})
    cloud_memory._reset_for_tests()

    def handler(method, url, kw):
        if method == "GET":
            return _FakeResponse(payload=_snapshot(user=["Cloud-only fact."]))
        return _FakeResponse(payload={"success": False, "error": "over budget"})

    _install_http(monkeypatch, handler)
    assert cloud_memory.sync_at_session_start() is True
    # The rejection is final: line dropped (no wedged journal)…
    assert not _journal_file(cloud_env).exists()
    # …no re-fetch was needed (nothing landed), and the mirror proceeds
    # from the session snapshot.
    raw = (cloud_env / "memories" / "USER.md").read_text(encoding="utf-8")
    assert "Cloud-only fact." in raw


def test_sync_at_session_start_offline_leaves_journal_untouched(cloud_env, monkeypatch):
    _install_http(monkeypatch, _fail_all)
    _handle_memory({"action": "add", "target": "user", "content": "Offline fact."})
    before = _journal_file(cloud_env).read_text(encoding="utf-8")
    cloud_memory._reset_for_tests()
    _install_http(monkeypatch, _fail_all)
    # Still offline next session: snapshot fetch fails → no replay attempt,
    # no mirror, journal byte-identical.
    assert cloud_memory.sync_at_session_start() is False
    assert _journal_file(cloud_env).read_text(encoding="utf-8") == before
    raw = (cloud_env / "memories" / "USER.md").read_text(encoding="utf-8")
    assert "Offline fact." in raw


# ── 7. Offline backoff ────────────────────────────────────────────────


def test_backoff_short_circuits_after_failure(cloud_env, monkeypatch):
    calls = {"n": 0}

    def failing(method, url, kw):
        calls["n"] += 1
        raise RuntimeError("network down")

    _install_http(monkeypatch, failing)
    assert cloud_memory.fetch_snapshot() is None
    assert calls["n"] == 1
    # While down, calls short-circuit — no second HTTP attempt, and the
    # write path raises instantly so the memory tool falls to local fast.
    assert cloud_memory.fetch_snapshot() is None
    assert calls["n"] == 1
    with pytest.raises(RuntimeError):
        cloud_memory.post_memory_tool({"action": "read", "target": "user"})
    assert calls["n"] == 1
    assert asyncio.run(cloud_memory.recall_query("q")) is None
    assert asyncio.run(cloud_memory.recall_sync("user", "t", "s")) is False
    assert calls["n"] == 1
    # Backoff expiry → the cloud is retried.
    monkeypatch.setattr(cloud_memory, "_down_until", 0.0)
    assert cloud_memory.fetch_snapshot() is None
    assert calls["n"] == 2

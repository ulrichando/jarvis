"""Spec B (Plane 3) — async subprocess spawner."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def _seed_queue(tmp_path, intents: list[dict]):
    queue = tmp_path / "auto-mods" / "queue.jsonl"
    queue.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(r) for r in intents)
    if body:
        body += "\n"
    queue.write_text(body)


def _make_intent(id_, **overrides):
    base = {"id": id_, "kind": "explicit", "intent": "fix X",
            "rationale": "test", "created_at": "2026-05-24T00:00:00Z"}
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _stub_worktrees(tmp_path, monkeypatch):
    """Spawner tests should never create real git worktrees in the checkout."""
    from pipeline.automod import spawner

    def _prepare(rec_id: str) -> tuple[Path, str]:
        p = tmp_path / "worktrees" / rec_id
        p.mkdir(parents=True, exist_ok=True)
        return p, "0" * 40  # (worktree_path, base_sha) — base_sha pins the diff

    monkeypatch.setattr(spawner, "_prepare_worktree", _prepare)
    monkeypatch.setattr(spawner, "_cleanup_worktree", lambda _p: None)


def test_shadow_mode_returns_zero_no_spawn(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("JARVIS_AUTOMOD_SPAWN_LIVE", raising=False)
    _seed_queue(tmp_path, [_make_intent("id1")])
    from pipeline.automod import spawner

    n = asyncio.run(spawner.drain_queue())
    assert n == 0
    # Queue intact
    queue = (tmp_path / "auto-mods" / "queue.jsonl").read_text().strip()
    assert "id1" in queue


def test_empty_queue_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "1")
    from pipeline.automod import spawner
    n = asyncio.run(spawner.drain_queue())
    assert n == 0


def test_missing_queue_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "1")
    # Don't even create the queue file
    from pipeline.automod import spawner
    n = asyncio.run(spawner.drain_queue())
    assert n == 0


def test_spawn_serializes_via_lockfile(tmp_path, monkeypatch):
    """3 intents -> 3 sequential spawns (lockfile held across all)."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "1")
    monkeypatch.setenv("JARVIS_AUTOMOD_DAILY_CAP", "10")
    _seed_queue(tmp_path, [_make_intent(f"id{i}") for i in range(3)])

    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        class _Fake:
            returncode = 0
            pid = 1234
            async def wait(self): return 0
        return _Fake()

    from pipeline.automod import spawner
    with patch.object(asyncio, "create_subprocess_exec", side_effect=fake_exec):
        n = asyncio.run(spawner.drain_queue())

    assert n == 3
    assert len(calls) == 3
    # Queue drained
    queue_path = tmp_path / "auto-mods" / "queue.jsonl"
    if queue_path.exists():
        assert not queue_path.read_text().strip()


def test_daily_cap_rejection_stays_queued_for_tomorrow(tmp_path, monkeypatch):
    """Budget exhaustion is deferral, not rejection; keep ranked work queued.

    The daily cap now counts REVIEWABLE proposals (finalize marks them when it
    writes a PENDING artifact), NOT spawns — so we pre-spend today's single slot
    directly rather than relying on a spawn to fill it (user 2026-06-23: a failed
    build must not consume the budget)."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "1")
    monkeypatch.setenv("JARVIS_AUTOMOD_DAILY_CAP", "1")
    # Today's single reviewable-proposal slot is already spent.
    from pipeline.automod import throttle
    throttle.mark_admitted("already-reviewed-today")
    _seed_queue(tmp_path, [
        _make_intent("id1"),
        _make_intent("id2"),
    ])

    async def fake_exec(*args, **kwargs):
        class _Fake:
            returncode = 0
            pid = 1
            async def wait(self): return 0
        return _Fake()

    from pipeline.automod import spawner
    with patch.object(asyncio, "create_subprocess_exec", side_effect=fake_exec):
        n = asyncio.run(spawner.drain_queue())

    assert n == 0  # budget exhausted → nothing admitted
    queue_path = tmp_path / "auto-mods" / "queue.jsonl"
    remaining = [json.loads(line)["id"] for line in queue_path.read_text().splitlines()]
    assert remaining == ["id1", "id2"]  # both deferred for tomorrow


def test_drain_queue_only_id_preserves_other_intents(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "1")
    monkeypatch.setenv("JARVIS_AUTOMOD_DAILY_CAP", "10")
    _seed_queue(tmp_path, [
        _make_intent("id1"),
        _make_intent("id2"),
        _make_intent("id3"),
    ])

    async def fake_exec(*args, **kwargs):
        class _Fake:
            returncode = 0
            pid = 1
            async def wait(self): return 0
        return _Fake()

    from pipeline.automod import spawner
    with patch.object(asyncio, "create_subprocess_exec", side_effect=fake_exec):
        n = asyncio.run(spawner.drain_queue(only_id="id2"))

    assert n == 1
    queue_path = tmp_path / "auto-mods" / "queue.jsonl"
    remaining = [json.loads(line)["id"] for line in queue_path.read_text().splitlines()]
    assert remaining == ["id1", "id3"]


def test_timeout_treated_as_consumed(tmp_path, monkeypatch):
    """A spawn that times out is logged + dropped from queue."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "1")
    _seed_queue(tmp_path, [_make_intent("id1")])

    async def slow_exec(*args, **kwargs):
        class _Slow:
            returncode = 0
            pid = 1
            async def wait(self):
                await asyncio.sleep(100)  # would exceed timeout
        return _Slow()

    from pipeline.automod import spawner
    # Force timeout to 0.1s for this test
    monkeypatch.setattr(spawner, "SPAWN_TIMEOUT_S", 0.1)
    with patch.object(asyncio, "create_subprocess_exec", side_effect=slow_exec):
        n = asyncio.run(spawner.drain_queue())
    assert n == 0  # spawned but timed out -> not counted as success
    # Queue still drained
    queue_path = tmp_path / "auto-mods" / "queue.jsonl"
    if queue_path.exists():
        assert not queue_path.read_text().strip()


def test_intent_file_written_before_spawn(tmp_path, monkeypatch):
    """Before launching the subprocess, the intent text is written to disk."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "1")
    _seed_queue(tmp_path, [_make_intent("id1", intent="MY-TEST-INTENT")])

    captured_args = []
    captured_env = []

    async def fake_exec(*args, **kwargs):
        captured_args.append(args)
        captured_env.append(kwargs.get("env") or {})
        class _Fake:
            returncode = 0
            pid = 1
            async def wait(self): return 0
        return _Fake()

    from pipeline.automod import spawner
    with patch.object(asyncio, "create_subprocess_exec", side_effect=fake_exec):
        asyncio.run(spawner.drain_queue())

    assert captured_args
    assert captured_env
    # Second positional arg should be the intent file path; the wrapper should
    # run in an isolated worktree, not the live checkout.
    intent_file = tmp_path / "auto-mods" / "id1.intent.txt"
    assert intent_file.exists()
    body = intent_file.read_text()
    assert "MY-TEST-INTENT" in body
    assert "EVOLUTION:" in body
    assert captured_env[0]["JARVIS_AUTOMOD_REPO_ROOT"].endswith("worktrees/id1")
    # The diff base is PINNED to the worktree's exact base SHA (from
    # _prepare_worktree), NOT the moving `master` ref — so a concurrent commit to
    # master can't pollute the proposal's diff (the too_many_files bug). The
    # _stub_worktrees fixture returns "0"*40 as the stub base_sha.
    assert captured_env[0]["JARVIS_AUTOMOD_BASE_REF"] == "0" * 40

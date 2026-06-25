"""Spec B (Plane 3) — async subprocess spawner."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
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


# ── Loop-reliability hardening (2026-06-25) ───────────────────────────


def test_skip_rebuild_when_already_pending(tmp_path, monkeypatch):
    """An id that already has a pending (or merged) artifact is NEVER rebuilt:
    its commit lives on automod/<id> awaiting review, and re-spawning both wipes
    that branch and dies 'branch already exists' in the wrapper (live 2026-06-25:
    the cycle re-spawned an already-built 59b830 → fatal)."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "1")
    monkeypatch.setenv("JARVIS_AUTOMOD_DAILY_CAP", "10")
    from pipeline.automod import artifact, spawner
    artifact.write({"id": "id1", "status": "pending"})
    _seed_queue(tmp_path, [_make_intent("id1")])

    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        class _Fake:
            returncode = 0
            pid = 1
            async def wait(self): return 0
        return _Fake()

    with patch.object(asyncio, "create_subprocess_exec", side_effect=fake_exec):
        n = asyncio.run(spawner.drain_queue())

    assert n == 0           # nothing built
    assert calls == []      # the wrapper subprocess was never launched
    qp = tmp_path / "auto-mods" / "queue.jsonl"
    assert not (qp.read_text().strip() if qp.exists() else "")  # entry consumed


def test_stale_branch_deleted_before_spawn(tmp_path, monkeypatch):
    """A leftover automod/<id> branch from a prior failed build is force-deleted
    before the rebuild so the wrapper's `checkout -b` can't die 'already exists'.
    (_prepare_worktree is stubbed, so the only _git calls come from the cleanup.)"""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "1")
    monkeypatch.setenv("JARVIS_AUTOMOD_DAILY_CAP", "10")
    _seed_queue(tmp_path, [_make_intent("id1")])
    from pipeline.automod import spawner

    git_calls = []

    def fake_git(*args):
        git_calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")  # rev-parse 0 = exists

    monkeypatch.setattr(spawner, "_git", fake_git)

    async def fake_exec(*args, **kwargs):
        class _Fake:
            returncode = 0
            pid = 1
            async def wait(self): return 0
        return _Fake()

    with patch.object(asyncio, "create_subprocess_exec", side_effect=fake_exec):
        asyncio.run(spawner.drain_queue())

    assert ("rev-parse", "--verify", "--quiet", "automod/id1") in git_calls
    assert ("branch", "-D", "automod/id1") in git_calls


def test_prune_orphan_branches_keeps_landable(tmp_path, monkeypatch):
    """Prune drops automod/* branches with no landable proposal (failed / no
    artifact) but KEEPS pending (review) + merged (rollback handle), and never
    touches a branch checked out in a worktree (an in-flight build)."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import artifact, spawner
    artifact.write({"id": "keep-pending", "status": "pending"})
    artifact.write({"id": "keep-merged", "status": "merged"})
    artifact.write({"id": "drop-failed", "status": "failed"})
    # 'drop-orphan' has a branch but NO artifact; 'inflight' is checked out.
    branches = ["automod/keep-pending", "automod/keep-merged",
                "automod/drop-failed", "automod/drop-orphan", "automod/inflight"]
    deleted = []

    def fake_git(*args):
        r = SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ("branch", "--list"):
            r.stdout = "\n".join(branches) + "\n"
        elif args[:2] == ("worktree", "list"):
            r.stdout = "worktree /x\nbranch refs/heads/automod/inflight\n"
        elif args[:2] == ("branch", "-D"):
            deleted.append(args[2])
        return r

    monkeypatch.setattr(spawner, "_git", fake_git)
    n = spawner.prune_orphan_branches()

    assert set(deleted) == {"automod/drop-failed", "automod/drop-orphan"}
    assert n == 2
    assert "automod/keep-pending" not in deleted   # awaiting review
    assert "automod/keep-merged" not in deleted     # deployed / rollback handle
    assert "automod/inflight" not in deleted         # in-flight build


# ── Additional robustness tests (2026-06-25) ─────────────────────────────────


def test_skip_rebuild_when_already_merged(tmp_path, monkeypatch):
    """A MERGED artifact (deployed) must also block rebuild, not just PENDING.
    The branch is the rollback handle; wiping it is as dangerous as wiping pending."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "1")
    monkeypatch.setenv("JARVIS_AUTOMOD_DAILY_CAP", "10")
    from pipeline.automod import artifact, spawner
    artifact.write({"id": "merged-id", "status": "merged"})
    _seed_queue(tmp_path, [_make_intent("merged-id")])

    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        class _Fake:
            returncode = 0
            pid = 1
            async def wait(self): return 0
        return _Fake()

    with patch.object(asyncio, "create_subprocess_exec", side_effect=fake_exec):
        n = asyncio.run(spawner.drain_queue())

    assert n == 0        # merged proposal never rebuilt
    assert calls == []   # subprocess never launched


def test_already_built_resilient_to_missing_status_key(tmp_path, monkeypatch):
    """An artifact whose JSON is valid but has no 'status' key is treated as
    NOT landable — the id is safe to rebuild (returns False)."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import artifact, spawner
    artifact.write({"id": "no-status-id"})  # no 'status' key
    assert spawner._already_built("no-status-id") is False


def test_already_built_resilient_to_filenotfound(tmp_path, monkeypatch):
    """A missing artifact file → _already_built returns False (not landable).
    The id is safe to build for the first time."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import spawner
    assert spawner._already_built("completely-new-id-zz99") is False


def test_already_built_resilient_to_malformed_json(tmp_path, monkeypatch):
    """A corrupt artifact JSON → _already_built returns False (not landable).
    Never crashes the spawner on a disk-corrupt file."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod._state import artifact_path
    p = artifact_path("corrupt-id")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{bad json!!!", encoding="utf-8")
    from pipeline.automod import spawner
    assert spawner._already_built("corrupt-id") is False


def test_delete_stale_branch_noop_when_branch_absent(tmp_path, monkeypatch):
    """_delete_stale_branch is a no-op when the branch does not exist.
    Must NOT issue a `git branch -D` call (which would error on nonexistent)."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import spawner

    git_calls = []

    def fake_git(*args):
        git_calls.append(args)
        # rev-parse returns nonzero → branch does not exist
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(spawner, "_git", fake_git)
    spawner._delete_stale_branch("nonexistent-branch-id")

    # Only the existence-check should have been called; no delete.
    assert ("rev-parse", "--verify", "--quiet", "automod/nonexistent-branch-id") in git_calls
    assert not any(args[:2] == ("branch", "-D") for args in git_calls), (
        "_delete_stale_branch must not call 'git branch -D' when the branch is absent"
    )


def test_delete_stale_branch_deletes_when_branch_exists(tmp_path, monkeypatch):
    """_delete_stale_branch force-deletes the branch when it IS present.
    Verifies the -D flag (not -d) to handle un-merged commits."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import spawner

    git_calls = []

    def fake_git(*args):
        git_calls.append(args)
        # rev-parse returncode=0 means branch EXISTS
        return SimpleNamespace(returncode=0, stdout="abc123", stderr="")

    monkeypatch.setattr(spawner, "_git", fake_git)
    spawner._delete_stale_branch("stale-id")

    assert ("rev-parse", "--verify", "--quiet", "automod/stale-id") in git_calls
    assert ("branch", "-D", "automod/stale-id") in git_calls, (
        "_delete_stale_branch must use 'git branch -D' (force) to drop un-merged commits"
    )


def test_delete_stale_branch_survives_failed_delete(tmp_path, monkeypatch):
    """If the `git branch -D` itself fails (e.g. locked), _delete_stale_branch
    logs a warning but does NOT raise — the spawner loop must stay alive."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import spawner

    def fake_git(*args):
        if args[:3] == ("rev-parse", "--verify", "--quiet"):
            return SimpleNamespace(returncode=0, stdout="sha", stderr="")
        if args[:2] == ("branch", "-D"):
            return SimpleNamespace(returncode=1, stdout="", stderr="locked")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawner, "_git", fake_git)
    # Must not raise even though the delete failed
    spawner._delete_stale_branch("locked-branch-id")


def test_prune_orphan_branches_empty_git_list(tmp_path, monkeypatch):
    """When git returns no automod/* branches, prune returns 0 immediately
    and never calls 'git worktree list' or 'git branch -D'."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import spawner

    git_calls = []

    def fake_git(*args):
        git_calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawner, "_git", fake_git)
    n = spawner.prune_orphan_branches()

    assert n == 0
    # Only the branch --list call was made; no worktree list, no deletes
    assert all(args[:2] == ("branch", "--list") for args in git_calls), (
        "prune should exit after an empty branch list without further git calls"
    )
    assert not any(args[:2] == ("branch", "-D") for args in git_calls)


def test_prune_orphan_branches_drops_artifact_with_no_status(tmp_path, monkeypatch):
    """An automod/* branch whose artifact exists but has no 'status' key is
    treated as non-landable and must be deleted by prune."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import artifact, spawner
    artifact.write({"id": "no-status-id"})  # no 'status' key

    deleted = []

    def fake_git(*args):
        r = SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ("branch", "--list"):
            r.stdout = "automod/no-status-id\n"
        elif args[:2] == ("worktree", "list"):
            r.stdout = "worktree /x\n"  # nothing checked out
        elif args[:2] == ("branch", "-D"):
            deleted.append(args[2])
        return r

    monkeypatch.setattr(spawner, "_git", fake_git)
    n = spawner.prune_orphan_branches()

    assert n == 1
    assert "automod/no-status-id" in deleted


def test_prune_orphan_branches_drops_corrupt_artifact(tmp_path, monkeypatch):
    """An automod/* branch whose artifact JSON is corrupt is treated as
    non-landable (status=None) and must be deleted by prune."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod._state import artifact_path
    p = artifact_path("bad-json-id")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{corrupt!!", encoding="utf-8")

    from pipeline.automod import spawner

    deleted = []

    def fake_git(*args):
        r = SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ("branch", "--list"):
            r.stdout = "automod/bad-json-id\n"
        elif args[:2] == ("worktree", "list"):
            r.stdout = "worktree /x\n"
        elif args[:2] == ("branch", "-D"):
            deleted.append(args[2])
        return r

    monkeypatch.setattr(spawner, "_git", fake_git)
    n = spawner.prune_orphan_branches()

    assert n == 1
    assert "automod/bad-json-id" in deleted


def test_prune_orphan_branches_never_touches_checked_out(tmp_path, monkeypatch):
    """A branch checked out in a live worktree (in-flight build) must NEVER
    be deleted by prune, even if its artifact is missing or failed."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import artifact, spawner
    artifact.write({"id": "inflight-id", "status": "failed"})  # failed but in-flight

    deleted = []

    def fake_git(*args):
        r = SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ("branch", "--list"):
            r.stdout = "automod/inflight-id\n"
        elif args[:2] == ("worktree", "list"):
            # Simulate worktree with this branch checked out
            r.stdout = "worktree /tmp/wt\nbranch refs/heads/automod/inflight-id\n"
        elif args[:2] == ("branch", "-D"):
            deleted.append(args[2])
        return r

    monkeypatch.setattr(spawner, "_git", fake_git)
    n = spawner.prune_orphan_branches()

    assert n == 0
    assert deleted == [], (
        "prune must never delete a branch checked out in an active worktree"
    )


def test_malformed_queue_entry_json_is_dropped(tmp_path, monkeypatch):
    """A line in queue.jsonl that is not valid JSON is silently dropped;
    subsequent valid entries continue to be processed."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "1")
    monkeypatch.setenv("JARVIS_AUTOMOD_DAILY_CAP", "10")
    queue = tmp_path / "auto-mods" / "queue.jsonl"
    queue.parent.mkdir(parents=True, exist_ok=True)
    # First line is corrupt JSON; second is valid
    queue.write_text(
        "{bad json}\n"
        + json.dumps(_make_intent("good-id")) + "\n"
    )

    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        class _Fake:
            returncode = 0
            pid = 1
            async def wait(self): return 0
        return _Fake()

    from pipeline.automod import spawner
    with patch.object(asyncio, "create_subprocess_exec", side_effect=fake_exec):
        n = asyncio.run(spawner.drain_queue())

    assert n == 1            # the valid entry was spawned
    assert len(calls) == 1  # exactly one subprocess call


def test_missing_intent_field_returns_error_not_crash(tmp_path, monkeypatch):
    """A queue entry with valid JSON but no 'intent' key must return 'error'
    cleanly — it must NOT propagate a KeyError that crashes the drain loop
    and silently discards remaining queue entries.

    This was a real bug: intent['intent'] raised KeyError outside the
    try/except in _spawn_one, killing the whole drain iteration.
    """
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "1")
    monkeypatch.setenv("JARVIS_AUTOMOD_DAILY_CAP", "10")
    # First entry is missing 'intent'; second is fully valid
    _seed_queue(tmp_path, [
        {"id": "no-intent-id", "kind": "explicit", "rationale": "t",
         "created_at": "2026-05-24T00:00:00Z"},
        _make_intent("after-id"),
    ])

    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        class _Fake:
            returncode = 0
            pid = 1
            async def wait(self): return 0
        return _Fake()

    from pipeline.automod import spawner
    with patch.object(asyncio, "create_subprocess_exec", side_effect=fake_exec):
        n = asyncio.run(spawner.drain_queue())

    # The malformed entry returns 'error'; the valid one after it gets spawned.
    assert n == 1, (
        "drain_queue must continue processing remaining entries after a "
        "malformed (no-intent) entry — a KeyError here kills the loop"
    )
    assert len(calls) == 1


def test_evolution_paused_leaves_queue_intact(tmp_path, monkeypatch):
    """When evolution is paused, drain_queue returns 0 AND leaves the queue
    file untouched so entries are not lost."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "1")
    from pipeline.automod._state import set_evolution_paused
    from pipeline.automod import spawner
    set_evolution_paused(True)
    _seed_queue(tmp_path, [_make_intent("queued-id")])

    n = asyncio.run(spawner.drain_queue())

    assert n == 0
    queue_text = (tmp_path / "auto-mods" / "queue.jsonl").read_text()
    assert "queued-id" in queue_text, (
        "paused evolution must NOT drain the queue — entries must survive for later"
    )


# ── Fused from parallel agent A: complementary coverage
# (wrapper-missing, daily-budget no-consume, rejected-status prune) 2026-06-25 ──

def test_prune_orphan_branches_drops_rejected_status(tmp_path, monkeypatch):
    """A branch whose artifact has status='rejected' (not pending/merged) must
    be pruned — only pending and merged are landable."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import artifact, spawner
    artifact.write({"id": "rej-1", "status": "rejected"})

    deleted = []

    def fake_git(*args):
        r = SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ("branch", "--list"):
            r.stdout = "automod/rej-1\n"
        elif args[:2] == ("worktree", "list"):
            r.stdout = ""
        elif args[:2] == ("branch", "-D"):
            deleted.append(args[2])
        return r

    monkeypatch.setattr(spawner, "_git", fake_git)
    n = spawner.prune_orphan_branches()

    assert "automod/rej-1" in deleted, (
        "rejected-status branch must be pruned"
    )
    assert n == 1


def test_spawn_one_returns_error_when_wrapper_missing(tmp_path, monkeypatch):
    """If the wrapper script doesn't exist, _spawn_one returns 'error' and
    drain_queue counts 0 spawned (no budget consumed)."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "1")
    monkeypatch.setenv("JARVIS_AUTOMOD_DAILY_CAP", "10")
    _seed_queue(tmp_path, [_make_intent("id-nowrapper")])

    from pipeline.automod import spawner
    # Point WRAPPER_SCRIPT at a path that definitely doesn't exist
    monkeypatch.setattr(spawner, "WRAPPER_SCRIPT", tmp_path / "no-such-script")

    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        class _Fake:
            returncode = 0
            pid = 1
            async def wait(self): return 0
        return _Fake()

    with patch.object(asyncio, "create_subprocess_exec", side_effect=fake_exec):
        n = asyncio.run(spawner.drain_queue())

    assert n == 0, "missing wrapper script must not count as a successful spawn"
    assert calls == [], "create_subprocess_exec must not be called for missing wrapper"


def test_skip_rebuild_does_not_consume_daily_budget(tmp_path, monkeypatch):
    """When _spawn_one returns 'skipped' (already-built id), the daily cap
    must NOT be incremented — finalize.mark_admitted is only called on PENDING
    artifacts, but the spawner's 'spawned' counter also must stay 0 so no
    false budget consumption occurs upstream."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "1")
    monkeypatch.setenv("JARVIS_AUTOMOD_DAILY_CAP", "1")
    from pipeline.automod import artifact, throttle, spawner
    # Pre-write a pending artifact so id1 is "already built"
    artifact.write({"id": "id1", "status": "pending"})
    _seed_queue(tmp_path, [_make_intent("id1")])

    async def fake_exec(*args, **kwargs):
        class _Fake:
            returncode = 0; pid = 1
            async def wait(self): return 0
        return _Fake()

    budget_before = throttle.admitted_today()
    with patch.object(asyncio, "create_subprocess_exec", side_effect=fake_exec):
        n = asyncio.run(spawner.drain_queue())

    assert n == 0
    assert throttle.admitted_today() == budget_before, (
        "skipped rebuild must not consume the daily budget"
    )

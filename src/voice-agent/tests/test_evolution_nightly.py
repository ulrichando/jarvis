"""Tests for the nightly self-evolution trigger (proposal-only orchestration)."""
from __future__ import annotations

import pytest

from pipeline.automod import _state, deploy, nightly


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    # Default: no real telemetry → user not "recently active" unless a test says so.
    monkeypatch.setattr(nightly, "_user_recently_active", lambda: False)
    # HERMETIC GIT: never let a test touch the real repo. Default = clean tree
    # (no stash) + a stable branch (no checkout). Tests that exercise the
    # stash/branch paths override deploy._git themselves.
    import subprocess

    def _fake_git(*args):
        if args[:1] == ("status",):
            return subprocess.CompletedProcess(args, 0, "", "")       # clean
        if args[:1] == ("rev-parse",):
            return subprocess.CompletedProcess(args, 0, "feat/test\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(deploy, "_git", _fake_git)
    return tmp_path


def _enable_auto():
    _state.set_auto_mode(True)


def _patch_pipeline(monkeypatch, *, detected=0, spawned=0):
    import pipeline.automod.cycle as cycle
    monkeypatch.setattr(cycle, "run_cycle", lambda **_kwargs: {
        "mode": "auto",
        "detected": detected,
        "spawned": spawned,
        "built": [{"id": "automod-x", "status": "pending", "attempts": spawned}] if spawned else [],
        "budget": {"cap": 5, "admitted_today": spawned, "remaining": max(0, 5 - spawned)},
    })


def test_skips_when_deploy_in_flight(home, monkeypatch):
    deploy.write_marker({"automod_id": "x", "rollback_sha": "y"})
    assert nightly.run() == {"skipped": "deploy-in-flight"}


def test_skips_in_manual_mode_by_default(home, monkeypatch):
    assert nightly.run() == {"skipped": "manual-mode", "mode": "manual"}


def test_skips_when_user_active(home, monkeypatch):
    _enable_auto()
    monkeypatch.setattr(nightly, "_user_recently_active", lambda: True)
    assert nightly.run() == {"skipped": "user-active"}


def test_auto_mode_runs_cycle_without_publish(home, monkeypatch):
    _enable_auto()
    _patch_pipeline(monkeypatch, detected=3, spawned=0)
    out = nightly.run()
    assert out["detected"] == 3 and out["spawned"] == 0 and out["published"] == 0


def test_no_publish_without_autopublish(home, monkeypatch):
    _enable_auto()
    monkeypatch.delenv("JARVIS_EVOLUTION_AUTOPUBLISH", raising=False)
    _patch_pipeline(monkeypatch, detected=1, spawned=2)
    out = nightly.run()
    assert out["spawned"] == 2 and out["published"] == 0  # spawned but not published


def test_publishes_when_spawned_and_autopublish(home, monkeypatch):
    _enable_auto()
    monkeypatch.setenv("JARVIS_EVOLUTION_AUTOPUBLISH", "1")
    _patch_pipeline(monkeypatch, detected=1, spawned=1)
    import pipeline.automod.cli as cli
    import pipeline.automod.publish as publish
    monkeypatch.setattr(cli, "cmd_list",
                        lambda only_pending=True: [{"id": "automod-x"}])
    published = {}

    def fake_publish(i):
        published["id"] = i
        return True, "https://gh/pr/1"

    monkeypatch.setattr(publish, "publish", fake_publish)
    out = nightly.run()
    assert out["spawned"] == 1 and out["published"] == 1
    assert published["id"] == "automod-x"


def test_publish_skips_already_published(home, monkeypatch):
    _enable_auto()
    monkeypatch.setenv("JARVIS_EVOLUTION_AUTOPUBLISH", "1")
    _patch_pipeline(monkeypatch, detected=0, spawned=1)
    import pipeline.automod.cli as cli
    import pipeline.automod.publish as publish
    # One already has a PR; one doesn't.
    monkeypatch.setattr(cli, "cmd_list", lambda only_pending=True: [
        {"id": "automod-old", "pr_url": "https://gh/pr/9"},
        {"id": "automod-new"},
    ])
    calls = []
    monkeypatch.setattr(publish, "publish",
                        lambda i: calls.append(i) or (True, "https://gh/pr/2"))
    out = nightly.run()
    assert calls == ["automod-new"]      # only the unpublished one
    assert out["published"] == 1


def test_dirty_tree_is_not_touched_by_scheduler(home, monkeypatch):
    """Spawning now happens in disposable worktrees, so a dirty live tree does
    not need to be stashed, reset, or restored by the scheduler."""
    import subprocess
    _enable_auto()
    _patch_pipeline(monkeypatch, detected=0, spawned=1)
    calls = []

    def tracking_git(*args):
        calls.append(args)
        if args[:1] == ("status",):
            return subprocess.CompletedProcess(args, 0, " M live_edit.py\n", "")  # dirty
        if args[:1] == ("rev-parse",):
            return subprocess.CompletedProcess(args, 0, "feat/test\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(deploy, "_git", tracking_git)
    out = nightly.run()
    stash = [a for a in calls if a and a[0] == "stash"]
    reset = [a for a in calls if a and a[:2] == ("reset", "--hard")]
    assert stash == []
    assert reset == []
    assert out["spawned"] == 1


def test_spawn_still_runs_when_live_tree_is_dirty(home, monkeypatch):
    """A dirty checkout is safe because the spawner isolates generated edits."""
    import subprocess
    _enable_auto()
    _patch_pipeline(monkeypatch, detected=0, spawned=1)

    def dirty_git(*args):
        if args[:1] == ("status",):
            return subprocess.CompletedProcess(args, 0, " M live_edit.py\n", "")
        if args[:1] == ("rev-parse",):
            return subprocess.CompletedProcess(args, 0, "feat/test\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(deploy, "_git", dirty_git)
    out = nightly.run()
    assert out["spawned"] == 1
    assert "skipped" not in out


def test_run_never_raises_on_detector_error(home, monkeypatch):
    _enable_auto()
    import pipeline.automod.cycle as cycle

    def _boom():
        raise RuntimeError("telemetry locked")

    monkeypatch.setattr(cycle, "run_cycle", lambda **_kwargs: _boom())
    out = nightly.run()   # must not raise
    assert out["detected"] == 0 and "cycle_error" in out

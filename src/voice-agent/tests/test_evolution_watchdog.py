"""Tests for the self-evolution deploy watchdog — the survive-a-bad-deploy net.

The watchdog (pipeline.automod.watchdog) is the load-bearing safety piece: after
JARVIS deploys self-written code, it verifies health and AUTO-ROLLS-BACK to the
last-good SHA if the new code is unhealthy. These tests pin the state machine
(no-marker / boot-grace / confirmed / watching / rolled-back) with the health
signals + rollback mocked, so no real git/restart happens.
"""
from __future__ import annotations

import subprocess
import time

import pytest

from pipeline.automod import deploy, watchdog


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate the deploy marker + evolution log under a tmp JARVIS_HOME."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    return tmp_path


def _iso_ago(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))


def _marker(home, **over):
    m = {
        "automod_id": "automod-test-001",
        "rollback_sha": "0123456789abcdef",
        "deployed_at": _iso_ago(60),  # past boot-grace, inside window by default
        "deadline_s": 300,
    }
    m.update(over)
    deploy.write_marker(m)


# ── marker round-trip ──────────────────────────────────────────────────────

def test_marker_roundtrip_and_clear(home):
    assert deploy.read_marker() is None
    deploy.write_marker({"automod_id": "z", "rollback_sha": "s"})
    assert deploy.read_marker()["automod_id"] == "z"
    deploy.clear_marker()
    assert deploy.read_marker() is None


def test_deploy_refuses_only_if_proposal_files_dirty(home, monkeypatch):
    # The proposal's OWN files being dirty would conflict the ff-merge → refuse.
    monkeypatch.setattr(deploy, "_proposal_files_dirty",
                        lambda _id: ["src/voice-agent/foo.py"])
    ok, reason = deploy.deploy("automod-x")
    assert ok is False
    assert "uncommitted changes" in reason.lower()
    assert deploy.read_marker() is None  # never armed the watchdog


def test_deploy_proceeds_with_unrelated_dirty_tree(home, monkeypatch):
    # Unrelated dirty files must NOT block deploy — the ff-merge leaves them and
    # the watchdog stashes on rollback. Proposal's own files clean → proceed.
    import pipeline.automod.cli as cli
    monkeypatch.setattr(deploy, "_proposal_files_dirty", lambda _id: [])
    monkeypatch.setattr(deploy, "_wait_for_quiet", lambda: None)
    monkeypatch.setattr(cli, "cmd_merge", lambda _id: (True, "merge-sha"))
    monkeypatch.setattr(deploy, "_restart_agent", lambda: (True, ""))

    def fake_git(*args):
        if args[:2] == ("rev-parse", "HEAD"):
            return subprocess.CompletedProcess(args, 0, "rollback-sha\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(deploy, "_git", fake_git)

    ok, info = deploy.deploy("automod-x")
    assert ok is True
    assert info == "merge-sha"
    assert deploy.read_marker()["state"] == "watching"  # armed the watchdog


# ── watchdog state machine ─────────────────────────────────────────────────

def test_no_marker_is_noop(home):
    assert watchdog.run_once() == "no-marker"


def test_boot_grace_skips_health_checks(home, monkeypatch):
    _marker(home, deployed_at=_iso_ago(5))  # within BOOT_GRACE_S (30)
    # If health were probed during grace this would blow up:
    monkeypatch.setattr(watchdog, "_liveness",
                        lambda: (_ for _ in ()).throw(AssertionError("probed too early")))
    assert watchdog.run_once() == "boot-grace"
    assert deploy.read_marker() is not None


def test_confirmed_when_live_and_smoke_passes(home, monkeypatch):
    _marker(home)
    monkeypatch.setattr(watchdog, "_liveness", lambda: True)
    monkeypatch.setattr(watchdog, "_real_turn_since", lambda d: False)
    monkeypatch.setattr(watchdog, "_smoke_turn", lambda: True)
    assert watchdog.run_once() == "confirmed"
    assert deploy.read_marker() is None  # deploy confirmed → marker cleared


def test_confirmed_on_real_post_deploy_turn(home, monkeypatch):
    _marker(home)
    monkeypatch.setattr(watchdog, "_liveness", lambda: True)
    monkeypatch.setattr(watchdog, "_real_turn_since", lambda d: True)
    # Smoke-turn must NOT be needed when a real turn already landed.
    monkeypatch.setattr(watchdog, "_smoke_turn",
                        lambda: (_ for _ in ()).throw(AssertionError("should not smoke")))
    assert watchdog.run_once() == "confirmed"


def test_watching_when_unhealthy_within_window(home, monkeypatch):
    _marker(home, deployed_at=_iso_ago(60), deadline_s=300)
    monkeypatch.setattr(watchdog, "_liveness", lambda: False)
    assert watchdog.run_once() == "watching"
    assert deploy.read_marker() is not None  # keep watching, no rollback


def test_rollback_when_unhealthy_past_deadline(home, monkeypatch):
    _marker(home, deployed_at=_iso_ago(400), deadline_s=300)  # past deadline
    monkeypatch.setattr(watchdog, "_liveness", lambda: False)
    seen = {}
    monkeypatch.setattr(watchdog, "_rollback",
                        lambda sha: seen.setdefault("sha", sha) or True)
    assert watchdog.run_once() == "rolled-back"
    assert seen["sha"] == "0123456789abcdef"   # reset to the recorded last-good
    # OPS-04: the marker is NOT cleared — it transitions to a verify state so the
    # next tick confirms the rolled-back code is actually healthy.
    m = deploy.read_marker()
    assert m is not None and m["state"] == "rolled-back-verifying"


def test_rollback_impossible_without_sha(home, monkeypatch):
    _marker(home, deployed_at=_iso_ago(400), deadline_s=300, rollback_sha=None)
    monkeypatch.setattr(watchdog, "_liveness", lambda: False)
    assert watchdog.run_once() == "rollback-impossible"
    assert deploy.read_marker() is None


def test_failed_rollback_keeps_marker_for_retry(home, monkeypatch):
    _marker(home, deployed_at=_iso_ago(400), deadline_s=300)
    monkeypatch.setattr(watchdog, "_liveness", lambda: False)
    monkeypatch.setattr(watchdog, "_rollback", lambda sha: False)  # reset failed
    assert watchdog.run_once() == "rollback-failed"
    m = deploy.read_marker()
    assert m is not None                       # retained so a later tick retries
    assert m.get("rollback_attempts") == 1


# ── REAL git reset (the destructive path), against a throwaway repo ─────────

def test_rollback_resets_a_real_git_repo(tmp_path, monkeypatch):
    """Exercise the actual `git reset --hard` rollback (not mocked) on a
    disposable repo, so we know the destructive path truly returns the tree to
    the last-good SHA. The live restart is stubbed."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*a):
        return subprocess.run(["git", "-C", str(repo), *a],
                              capture_output=True, text=True, check=False)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "f.txt").write_text("good", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "A")
    good = git("rev-parse", "HEAD").stdout.strip()
    (repo / "f.txt").write_text("BAD self-edit", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "B")
    bad = git("rev-parse", "HEAD").stdout.strip()
    assert good and good != bad

    # Point the rollback's git at the throwaway repo; stub the systemctl restart.
    monkeypatch.setattr(deploy, "REPO_ROOT", repo)
    restarts = []
    real_run = subprocess.run

    def fake_run(cmd, *a, **k):
        if cmd and cmd[0] == "systemctl":
            restarts.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(watchdog.subprocess, "run", fake_run)

    assert watchdog._rollback(good) is True
    assert git("rev-parse", "HEAD").stdout.strip() == good   # reset to last-good
    assert (repo / "f.txt").read_text(encoding="utf-8") == "good"
    assert restarts, "rollback must restart the agent onto the reverted code"


# ── rollback health re-verification (OPS-04) ───────────────────────────────

def _verify_marker(home, **over):
    m = {
        "automod_id": "automod-test-001",
        "rollback_sha": "0123456789abcdef",
        "state": "rolled-back-verifying",
        "rolled_back_at": _iso_ago(60),   # past boot-grace, inside verify window
        "verify_deadline_s": 300,
    }
    m.update(over)
    deploy.write_marker(m)


def test_rollback_verify_healthy_confirms_and_clears(home, monkeypatch):
    _verify_marker(home)
    monkeypatch.setattr(watchdog, "_liveness", lambda: True)
    monkeypatch.setattr(watchdog, "_real_turn_since", lambda d: True)
    assert watchdog.run_once() == "rollback-healthy"
    assert deploy.read_marker() is None   # rolled-back code is healthy → done


def test_rollback_verify_within_window_keeps_watching(home, monkeypatch):
    _verify_marker(home, rolled_back_at=_iso_ago(60), verify_deadline_s=300)
    monkeypatch.setattr(watchdog, "_liveness", lambda: False)
    monkeypatch.setattr(watchdog, "_real_turn_since", lambda d: False)
    monkeypatch.setattr(watchdog, "_smoke_turn", lambda: False)
    assert watchdog.run_once() == "rollback-verifying"
    assert deploy.read_marker() is not None   # still confirming → keep the marker


def test_rollback_verify_unhealthy_past_window_escalates(home, monkeypatch):
    _verify_marker(home, rolled_back_at=_iso_ago(9999), verify_deadline_s=300)
    monkeypatch.setattr(watchdog, "_liveness", lambda: False)
    monkeypatch.setattr(watchdog, "_real_turn_since", lambda d: False)
    monkeypatch.setattr(watchdog, "_smoke_turn", lambda: False)
    assert watchdog.run_once() == "rollback-unhealthy"
    assert deploy.read_marker() is None   # last-good is bad → escalate + stop, no loop

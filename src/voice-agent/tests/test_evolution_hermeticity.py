"""Phase 1 — environment-hermeticity matrix for the self-evolution loop.

The loop runs inside a LIVE, shared, mutating repo: concurrent agent sessions, a
dirty working tree, a master that moves under it. Every recent evolution fix was
a *reactive* patch for one of those conditions (dirty-tree tolerance, cherry-pick
over a moved master, cycle-marker reclaim). This module turns those fixes into
*proactive* invariants so they can never silently regress — the loop must always
either complete correctly or abort clean, with zero corruption, regardless of
repo state.

Covered here:
  • state a — dirty tree: ``deploy._proposal_files_dirty`` discriminates the
    proposal's OWN dirty files (which would conflict the merge → block) from
    unrelated dirty files (a parallel session's WIP → safe to deploy past),
    plus the deploy() integration that branches on it.
  • states c+d — concurrency / crash: ``cycle._acquire_cycle_marker`` refuses a
    LIVE holder (no double-build) but reclaims a DEAD holder's marker
    (crash-resume), and ``_release`` never deletes another process's marker.

Deliberately NOT duplicated: state b (master moved past base) is already covered
by ``test_automod_cli.py`` — ``test_merge_succeeds_on_divergent_master`` (cherry-
pick over divergence) and the clean-abort-on-real-conflict test.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))

from pipeline.automod import cycle, deploy, watchdog  # noqa: E402
from pipeline.automod._state import cycle_marker_path  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate every auto-mod path (markers, artifacts, cycle lock) under a tmp
    JARVIS_HOME so a test can NEVER touch the live loop's ~/.jarvis state."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    return tmp_path


def _seed_artifact(home: Path, automod_id: str, files_changed: list[str]) -> None:
    d = home / "auto-mods"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{automod_id}.json").write_text(
        json.dumps({"id": automod_id, "files_changed": files_changed}),
        encoding="utf-8",
    )


def _fake_status(monkeypatch, porcelain_lines: list[str]) -> None:
    """Stub deploy._git so `status --porcelain` returns a controlled tree state.
    (deploy._git hard-codes cwd=REPO_ROOT, so faking it is how we drive the
    porcelain parser against a known dirty/clean tree without a real repo.)"""
    body = ("\n".join(porcelain_lines) + "\n") if porcelain_lines else ""

    def _git(*args):
        if args[:2] == ("status", "--porcelain"):
            return subprocess.CompletedProcess(list(args), 0, body, "")
        return subprocess.CompletedProcess(list(args), 0, "", "")

    monkeypatch.setattr(deploy, "_git", _git)


# ─────────────────────── state a · dirty-tree discrimination ──────────────────

def test_unrelated_dirty_files_do_not_block(home, monkeypatch):
    # A parallel session's WIP must NOT block the deploy.
    _seed_artifact(home, "a1", ["src/voice-agent/x.py"])
    _fake_status(monkeypatch, [" M src/voice-agent/other.py", "?? scratch.txt"])
    assert deploy._proposal_files_dirty("a1") == []


def test_proposal_own_dirty_file_blocks(home, monkeypatch):
    # The proposal's OWN uncommitted file would conflict the merge → flag it.
    _seed_artifact(home, "a1", ["src/voice-agent/x.py"])
    _fake_status(monkeypatch, [" M src/voice-agent/x.py"])
    assert deploy._proposal_files_dirty("a1") == ["src/voice-agent/x.py"]


def test_mixed_dirty_returns_only_proposal_files(home, monkeypatch):
    _seed_artifact(home, "a1", ["src/voice-agent/a.py", "src/voice-agent/b.py"])
    _fake_status(monkeypatch, [
        " M src/voice-agent/b.py",          # the proposal's own → flagged
        " M src/voice-agent/unrelated.py",  # someone else's → ignored
        "?? note.md",
    ])
    assert deploy._proposal_files_dirty("a1") == ["src/voice-agent/b.py"]


def test_untracked_proposal_file_is_detected(home, monkeypatch):
    _seed_artifact(home, "a1", ["src/voice-agent/new.py"])
    _fake_status(monkeypatch, ["?? src/voice-agent/new.py"])
    assert deploy._proposal_files_dirty("a1") == ["src/voice-agent/new.py"]


def test_clean_tree_returns_empty(home, monkeypatch):
    _seed_artifact(home, "a1", ["src/voice-agent/x.py"])
    _fake_status(monkeypatch, [])
    assert deploy._proposal_files_dirty("a1") == []


def test_missing_artifact_returns_empty(home, monkeypatch):
    _fake_status(monkeypatch, [" M src/voice-agent/x.py"])
    assert deploy._proposal_files_dirty("missing-id") == []


def test_empty_files_changed_returns_empty(home, monkeypatch):
    _seed_artifact(home, "a1", [])
    _fake_status(monkeypatch, [" M src/voice-agent/x.py"])
    assert deploy._proposal_files_dirty("a1") == []


# ─────────────── state a · deploy() integration over real detection ───────────

def test_deploy_refuses_when_real_detection_finds_own_dirty(home, monkeypatch):
    # End-to-end (real _proposal_files_dirty, only git stubbed): own file dirty
    # → deploy refuses BEFORE arming the watchdog.
    _seed_artifact(home, "a1", ["src/voice-agent/x.py"])
    _fake_status(monkeypatch, [" M src/voice-agent/x.py"])
    ok, reason = deploy.deploy("a1")
    assert ok is False
    assert "uncommitted changes" in reason.lower()
    assert deploy.read_marker() is None  # never armed


def test_deploy_proceeds_when_only_unrelated_dirty(home, monkeypatch):
    # The live 2026-06-23 scenario: a parallel session left the tree dirty, but
    # NOT the proposal's files → deploy must proceed, arm the watchdog, and pin
    # the pre-merge HEAD as the rollback target.
    import pipeline.automod.cli as cli

    _seed_artifact(home, "a1", ["src/voice-agent/x.py"])

    def fake_git(*args):
        if args[:2] == ("status", "--porcelain"):
            return subprocess.CompletedProcess(
                list(args), 0, " M src/voice-agent/other.py\n?? scratch\n", "")
        if args[:2] == ("rev-parse", "HEAD"):
            return subprocess.CompletedProcess(list(args), 0, "rollback-sha\n", "")
        return subprocess.CompletedProcess(list(args), 0, "", "")

    monkeypatch.setattr(deploy, "_git", fake_git)
    monkeypatch.setattr(cli, "cmd_merge", lambda _id: (True, "merge-sha"))
    monkeypatch.setattr(deploy, "_wait_for_quiet", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_restart_agent", lambda: (True, ""))

    ok, info = deploy.deploy("a1")
    assert ok is True and info == "merge-sha"
    marker = deploy.read_marker()
    assert marker["state"] == "watching"          # watchdog armed
    assert marker["rollback_sha"] == "rollback-sha"  # pre-merge HEAD pinned


# ─────────────── states c + d · cycle-marker concurrency / crash ──────────────

def _write_marker(pid: int) -> None:
    p = cycle_marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"pid": pid, "started_at": "2026-01-01T00:00:00Z"}),
                 encoding="utf-8")


def test_acquire_on_clean_writes_our_pid(home):
    ok, reason = cycle._acquire_cycle_marker()
    assert ok and reason == ""
    assert json.loads(cycle_marker_path().read_text())["pid"] == os.getpid()
    cycle._release_cycle_marker()


def test_acquire_refuses_a_live_holder(home):
    # state c: a concurrent cycle is genuinely running → refuse, don't double-build.
    # Our own pid is unambiguously alive, so this exercises the REAL _pid_alive.
    _write_marker(os.getpid())
    ok, reason = cycle._acquire_cycle_marker()
    assert ok is False
    assert reason == "cycle-running"
    # the live holder's marker is left intact
    assert json.loads(cycle_marker_path().read_text())["pid"] == os.getpid()


def test_acquire_reclaims_dead_holder(home, monkeypatch):
    # state d: the previous cycle's process died mid-run → reclaim the stale lock.
    _write_marker(999_999)
    monkeypatch.setattr(cycle, "_pid_alive", lambda _pid: False)
    ok, reason = cycle._acquire_cycle_marker()
    assert ok and reason == ""
    assert json.loads(cycle_marker_path().read_text())["pid"] == os.getpid()
    cycle._release_cycle_marker()


def test_acquire_reclaims_corrupt_marker(home):
    # A half-written marker (crash during write) must not wedge the loop forever.
    p = cycle_marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not valid json", encoding="utf-8")
    ok, reason = cycle._acquire_cycle_marker()
    assert ok and reason == ""
    assert json.loads(cycle_marker_path().read_text())["pid"] == os.getpid()
    cycle._release_cycle_marker()


def test_release_does_not_delete_another_processes_marker(home):
    # No cross-process clobber: releasing must only ever remove OUR marker.
    _write_marker(os.getpid() + 1)
    cycle._release_cycle_marker()
    assert cycle_marker_path().exists()


def test_acquire_release_roundtrip(home):
    ok, _ = cycle._acquire_cycle_marker()
    assert ok and cycle_marker_path().exists()
    cycle._release_cycle_marker()
    assert not cycle_marker_path().exists()


def test_pid_alive_true_for_self(home):
    assert cycle._pid_alive(os.getpid()) is True


# ─────────── rollback data-safety · never destroy a concurrent session's WIP ──

def test_rollback_stashes_unrelated_dirty_work_before_reset(home, tmp_path, monkeypatch):
    """The emergency rollback (`git reset --hard`) is the loop's most destructive
    op. A parallel session's uncommitted work on an UNRELATED tracked file must
    survive it — `_rollback` stashes (-u) before resetting, so nothing is lost."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True,
                       capture_output=True, text=True)

    git("init", "-q", "-b", "master")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "app.py").write_text("good\n")
    (repo / "other.py").write_text("v1\n")
    git("add", ".")
    git("commit", "-qm", "good")
    good_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                              capture_output=True, text=True).stdout.strip()
    # a bad self-deploy lands on top of the good SHA
    (repo / "app.py").write_text("BROKEN\n")
    git("add", ".")
    git("commit", "-qm", "bad deploy")
    # a parallel session has uncommitted work on an UNRELATED tracked file
    (repo / "other.py").write_text("precious-uncommitted\n")

    # Drive deploy._git inside the temp repo; neutralize ONLY the systemctl
    # restart (git + systemctl share the subprocess module, so guard by argv).
    monkeypatch.setattr(deploy, "REPO_ROOT", repo)
    real_run = subprocess.run

    def guarded_run(cmd, *a, **k):
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "systemctl":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", guarded_run)

    assert watchdog._rollback(good_sha) is True
    assert (repo / "app.py").read_text() == "good\n"        # bad deploy undone
    stash = real_run(["git", "stash", "list"], cwd=repo,
                     capture_output=True, text=True).stdout
    assert "evolution-watchdog-rollback" in stash           # WIP was stashed
    real_run(["git", "stash", "pop"], cwd=repo, check=True,
             capture_output=True, text=True)
    assert (repo / "other.py").read_text() == "precious-uncommitted\n"  # recovered

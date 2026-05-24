"""Spec B (Plane 3) — end-to-end propose → finalize → merge → revert.

Test-only task. Exercises pipeline.automod.finalize (B-T9) and
pipeline.automod.cli (B-T10) on a tmp git repo to keep the rollback
path tested.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def _git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=repo, check=check,
                          capture_output=True, text=True)


def _setup_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "src" / "voice-agent" / "prompts").mkdir(parents=True)
    f = repo / "src" / "voice-agent" / "prompts" / "supervisor.md"
    f.write_text("hello\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    return repo, f


def test_full_cycle_propose_finalize_merge_revert(tmp_path, monkeypatch):
    """Propose → branch+commit → finalize → merge → revert cycle.
    Validates that the rollback path restores the file to pre-change."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo, f = _setup_repo(tmp_path)

    # Simulate the CLI subprocess: branch + change + commit.
    _git(repo, "checkout", "-qb", "automod/cycle-001")
    f.write_text("world\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "feat: change")
    monkeypatch.chdir(repo)

    # Seed the intent file the wrapper would have written.
    intent_file = tmp_path / "auto-mods" / "cycle-001.intent.txt"
    intent_file.parent.mkdir(parents=True, exist_ok=True)
    intent_file.write_text("INTENT: change supervisor prompt\n"
                            "RATIONALE: test\nKIND: explicit\n")

    # 1. finalize — artifact written, status=pending.
    from pipeline.automod import finalize
    finalize.finalize_branch("cycle-001", "automod/cycle-001",
                              skip_test_rerun=True)
    art = json.loads((tmp_path / "auto-mods" / "cycle-001.json").read_text())
    assert art["status"] == "pending"
    assert art["files_changed"] == ["src/voice-agent/prompts/supervisor.md"]

    # 2. merge — ff-only into master.
    from pipeline.automod import cli
    ok, merge_sha = cli.cmd_merge("cycle-001")
    assert ok, merge_sha

    art = json.loads((tmp_path / "auto-mods" / "cycle-001.json").read_text())
    assert art["status"] == "merged"
    assert art["merge_sha"] == merge_sha
    assert f.read_text() == "world\n"

    # 3. revert.
    ok, revert_sha = cli.cmd_revert(merge_sha)
    assert ok, revert_sha
    # File restored.
    assert f.read_text() == "hello\n"


def test_revert_preserves_history(tmp_path, monkeypatch):
    """git revert produces a NEW commit, not a rewrite."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    (repo / "a.txt").write_text("b\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "change")
    monkeypatch.chdir(repo)

    sha_to_revert = _git(repo, "rev-parse", "HEAD").stdout.strip()

    from pipeline.automod import cli
    ok, new_sha = cli.cmd_revert(sha_to_revert)
    assert ok
    assert new_sha != sha_to_revert

    # History has 3 commits: init, change, revert.
    log = _git(repo, "log", "--oneline").stdout.strip().splitlines()
    assert len(log) == 3


def test_full_cycle_logs_to_evolution_log(tmp_path, monkeypatch):
    """The full cycle writes the 4 expected entries to evolution_log.jsonl:
    committed (from finalize) + merged + reverted."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo, f = _setup_repo(tmp_path)
    _git(repo, "checkout", "-qb", "automod/cycle-evlog")
    f.write_text("changed\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "change")
    monkeypatch.chdir(repo)

    intent_file = tmp_path / "auto-mods" / "cycle-evlog.intent.txt"
    intent_file.parent.mkdir(parents=True, exist_ok=True)
    intent_file.write_text("INTENT: test\n")

    from pipeline.automod import finalize, cli
    finalize.finalize_branch("cycle-evlog", "automod/cycle-evlog",
                              skip_test_rerun=True)
    ok, merge_sha = cli.cmd_merge("cycle-evlog")
    assert ok
    cli.cmd_revert(merge_sha)

    evlog = (tmp_path / "evolution_log.jsonl").read_text().strip().splitlines()
    kinds = [json.loads(line)["kind"] for line in evlog]
    assert "automod_committed" in kinds
    assert "automod_merged" in kinds
    assert "automod_reverted" in kinds

"""Spec B (Plane 3) — finalize.py validation + artifact write."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def _setup_tmp_repo_with_commit(tmp_path):
    """Tmp git repo with master + an automod branch carrying a small clean diff."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "src" / "voice-agent" / "prompts").mkdir(parents=True)
    f = repo / "src" / "voice-agent" / "prompts" / "supervisor.md"
    f.write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-qb", "automod/test-001"], cwd=repo, check=True)
    f.write_text("world\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "feat: change"], cwd=repo, check=True)
    return repo


def _seed_intent_file(tmp_path, automod_id, text="INTENT: test change\nRATIONALE: t\nKIND: explicit\n"):
    p = tmp_path / "auto-mods" / f"{automod_id}.intent.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_green_diff_writes_pending_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = _setup_tmp_repo_with_commit(tmp_path)
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "test-001")

    from pipeline.automod import finalize
    finalize.finalize_branch("test-001", "automod/test-001", skip_test_rerun=True)

    art = json.loads((tmp_path / "auto-mods" / "test-001.json").read_text())
    assert art["status"] == "pending"
    assert art["files_changed"] == ["src/voice-agent/prompts/supervisor.md"]
    assert art["parent_sha"]
    assert art["head_sha"]
    assert art["parent_sha"] != art["head_sha"]


def test_diff_with_blocked_path_marks_failed(tmp_path, monkeypatch):
    """A diff touching a blocked path is marked failed + branch deleted."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    bad = repo / "src" / "voice-agent" / "sanitizers"
    bad.mkdir(parents=True)
    f = bad / "dsml.py"
    f.write_text("x\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-qb", "automod/blocked-001"], cwd=repo, check=True)
    f.write_text("y\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "bad"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "blocked-001", "INTENT: bad\n")

    from pipeline.automod import finalize
    finalize.finalize_branch("blocked-001", "automod/blocked-001", skip_test_rerun=True)

    art = json.loads((tmp_path / "auto-mods" / "blocked-001.json").read_text())
    assert art["status"] == "failed"
    assert "block" in art.get("rejection_reason", "").lower()
    out = subprocess.check_output(["git", "branch"], cwd=repo).decode()
    assert "automod/blocked-001" not in out


def test_no_commit_marks_failed(tmp_path, monkeypatch):
    """If the CLI didn't commit anything, finalize marks artifact as failed."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "x.txt").write_text("hi\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-qb", "automod/nocommit-001"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "nocommit-001", "INTENT: nothing\n")

    from pipeline.automod import finalize
    finalize.finalize_branch("nocommit-001", "automod/nocommit-001", skip_test_rerun=True)

    art = json.loads((tmp_path / "auto-mods" / "nocommit-001.json").read_text())
    assert art["status"] == "failed"
    assert "no_commit" in art.get("rejection_reason", "")


def test_test_deletion_in_diff_marks_failed(tmp_path, monkeypatch):
    """A diff that DELETES a test should be rejected even though it
    touches src/voice-agent/."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    tdir = repo / "src" / "voice-agent" / "tests"
    tdir.mkdir(parents=True)
    f = tdir / "test_thing.py"
    f.write_text("def test_thing():\n    assert True\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-qb", "automod/deltest-001"], cwd=repo, check=True)
    f.write_text("# deleted\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "delete test"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "deltest-001")

    from pipeline.automod import finalize
    finalize.finalize_branch("deltest-001", "automod/deltest-001", skip_test_rerun=True)

    art = json.loads((tmp_path / "auto-mods" / "deltest-001.json").read_text())
    assert art["status"] == "failed"
    assert "test" in art.get("rejection_reason", "").lower()


def test_intent_text_threaded_into_artifact(tmp_path, monkeypatch):
    """The intent file's content lands in artifact['intent']."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = _setup_tmp_repo_with_commit(tmp_path)
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "test-001",
                       "INTENT: my-specific-test-intent\nRATIONALE: x\nKIND: explicit\n")

    from pipeline.automod import finalize
    art = finalize.finalize_branch("test-001", "automod/test-001", skip_test_rerun=True)
    assert art["intent"] == "my-specific-test-intent" or "my-specific-test-intent" in art["intent"]

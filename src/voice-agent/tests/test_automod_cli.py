"""Spec B (Plane 3) — bin/jarvis-automod CLI subcommands."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def _seed_artifact(tmp_path, automod_id, status="pending", **extra):
    home = tmp_path / "auto-mods"
    home.mkdir(parents=True, exist_ok=True)
    art = {
        "id": automod_id,
        "kind": "explicit",
        "intent": "fix X",
        "branch": f"automod/{automod_id}",
        "parent_sha": "abc",
        "head_sha": "def",
        "files_changed": ["src/voice-agent/x.py"],
        "diff_summary": "+2/-1",
        "test_output_tail": "ok",
        "status": status,
        "created_at": "2026-05-24T00:00:00Z",
    }
    art.update(extra)
    (home / f"{automod_id}.json").write_text(json.dumps(art))
    return art


def test_list_shows_pending_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    _seed_artifact(tmp_path, "automod-2026-05-24-id1")
    _seed_artifact(tmp_path, "automod-2026-05-24-id2", status="merged")
    from pipeline.automod import cli
    rows = cli.cmd_list(only_pending=True)
    assert len(rows) == 1
    assert rows[0]["id"] == "automod-2026-05-24-id1"


def test_list_all_includes_merged(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    _seed_artifact(tmp_path, "automod-2026-05-24-id1")
    _seed_artifact(tmp_path, "automod-2026-05-24-id2", status="merged")
    from pipeline.automod import cli
    rows = cli.cmd_list(only_pending=False)
    assert len(rows) == 2


def test_list_sorts_by_created_at_desc(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    _seed_artifact(tmp_path, "automod-2026-05-22-old", created_at="2026-05-22T00:00:00Z")
    _seed_artifact(tmp_path, "automod-2026-05-24-new", created_at="2026-05-24T00:00:00Z")
    from pipeline.automod import cli
    rows = cli.cmd_list(only_pending=False)
    assert rows[0]["id"] == "automod-2026-05-24-new"


def test_show_returns_full_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    _seed_artifact(tmp_path, "automod-2026-05-24-id1")
    from pipeline.automod import cli
    art = cli.cmd_show("automod-2026-05-24-id1")
    assert art["id"] == "automod-2026-05-24-id1"
    assert art["status"] == "pending"


def test_reject_updates_status_and_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    _seed_artifact(tmp_path, "automod-2026-05-24-id1")
    # Need a git repo for branch -D to work
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-qb", "automod/automod-2026-05-24-id1"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "master"], cwd=repo, check=True)
    monkeypatch.chdir(repo)

    from pipeline.automod import cli
    cli.cmd_reject("automod-2026-05-24-id1", "wrong scope")
    art = json.loads(
        (tmp_path / "auto-mods" / "automod-2026-05-24-id1.json").read_text()
    )
    assert art["status"] == "rejected"
    assert art["rejection_reason"] == "wrong scope"


def test_merge_ff_only_succeeds_on_clean_branch(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
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
    subprocess.run(["git", "checkout", "-qb", "automod/automod-2026-05-24-id1"], cwd=repo, check=True)
    f.write_text("world\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "change"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "master"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    _seed_artifact(tmp_path, "automod-2026-05-24-id1")

    from pipeline.automod import cli
    ok, info = cli.cmd_merge("automod-2026-05-24-id1")
    assert ok, info
    # File now has the merged content
    assert f.read_text() == "world\n"
    art = json.loads((tmp_path / "auto-mods" / "automod-2026-05-24-id1.json").read_text())
    assert art["status"] == "merged"
    assert art["merge_sha"]


def test_merge_succeeds_on_divergent_master(tmp_path, monkeypatch):
    """master advancing on UNRELATED files no longer blocks the merge — the
    proposal is cherry-picked on top (was ff-only → aborted). 2026-06-23."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "src" / "voice-agent" / "prompts").mkdir(parents=True)
    f = repo / "src" / "voice-agent" / "prompts" / "supervisor.md"
    f.write_text("a\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-qb", "automod/automod-2026-05-24-id1"], cwd=repo, check=True)
    f.write_text("b\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "branch"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "master"], cwd=repo, check=True)
    # Diverge master on an UNRELATED file (ff-only would abort here).
    (repo / "src" / "voice-agent" / "other.md").write_text("c\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "diverge"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    _seed_artifact(tmp_path, "automod-2026-05-24-id1")

    from pipeline.automod import cli
    ok, info = cli.cmd_merge("automod-2026-05-24-id1")
    assert ok, info                                  # cherry-pick handles divergence
    assert f.read_text() == "b\n"                    # proposal applied
    assert (repo / "src" / "voice-agent" / "other.md").read_text() == "c\n"  # master's move kept


def test_merge_aborts_on_conflict(tmp_path, monkeypatch):
    """If master changed the proposal's OWN file, the cherry-pick conflicts and
    aborts cleanly (a real conflict that needs a human)."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "src" / "voice-agent" / "prompts").mkdir(parents=True)
    f = repo / "src" / "voice-agent" / "prompts" / "supervisor.md"
    f.write_text("a\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-qb", "automod/automod-2026-05-24-id1"], cwd=repo, check=True)
    f.write_text("b\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "branch"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "master"], cwd=repo, check=True)
    # Master changes the SAME file → cherry-pick conflict.
    f.write_text("z\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "conflict"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    _seed_artifact(tmp_path, "automod-2026-05-24-id1")

    from pipeline.automod import cli
    ok, reason = cli.cmd_merge("automod-2026-05-24-id1")
    assert not ok
    assert "cherry" in reason.lower() or "conflict" in reason.lower()
    # the abort must leave the tree clean (no half-applied cherry-pick)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                            capture_output=True, text=True).stdout
    assert "UU" not in status and "AA" not in status


def test_merge_refuses_non_pending_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    _seed_artifact(tmp_path, "automod-2026-05-24-id1", status="merged")
    from pipeline.automod import cli
    ok, reason = cli.cmd_merge("automod-2026-05-24-id1")
    assert not ok
    assert "status" in reason.lower() or "pending" in reason.lower()


def test_revert_creates_new_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    (repo / "a.txt").write_text("b\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "change"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()

    from pipeline.automod import cli
    ok, new_sha = cli.cmd_revert(sha)
    assert ok
    assert new_sha != sha
    # Log has 3 commits: init, change, revert
    log = subprocess.check_output(["git", "log", "--oneline"], cwd=repo).decode().strip().splitlines()
    assert len(log) == 3


def test_main_list_exits_zero_with_no_artifacts(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import cli
    code = cli.main(["jarvis-automod", "list"])
    assert code == 0
    out = capsys.readouterr().out
    assert "no auto-mod artifacts" in out.lower() or "(none)" in out.lower() or out.strip() == "" or "no " in out.lower()

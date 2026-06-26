"""Tests for the self-evolution publish step (push branch + open PR).

git push + gh are mocked — no network, no real PRs.
"""
from __future__ import annotations

import subprocess

import pytest

from pipeline.automod import artifact
from pipeline.automod import publish as pub


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    return tmp_path


def _write_art(automod_id, **over):
    art = {
        "id": automod_id,
        "intent": "Cache the route regex",
        "branch": f"automod/{automod_id}",
        "files_changed": ["src/voice-agent/pipeline/turn_router.py"],
        "test_output_tail": "3219 passed",
        "status": "pending",
    }
    art.update(over)
    artifact.write(art)
    return art


def _ok(*out):
    return subprocess.CompletedProcess(out, 0, out[0] if out else "", "")


def test_publish_pushes_and_opens_pr(home, monkeypatch):
    _write_art("automod-test-1")
    pushed = {}

    def fake_git(*a):
        pushed["args"] = a
        return _ok()

    monkeypatch.setattr(pub, "_git", fake_git)

    def fake_gh(*a):
        if a[:2] == ("pr", "create"):
            return subprocess.CompletedProcess(
                a, 0, "https://github.com/ulrichando/jarvis/pull/7\n", "")
        return subprocess.CompletedProcess(a, 1, "", "")

    monkeypatch.setattr(pub, "_gh", fake_gh)
    ok, url = pub.publish("automod-test-1")
    assert ok and url.endswith("/pull/7")
    assert pushed["args"][:3] == ("push", "-u", "origin")
    assert artifact.load("automod-test-1").get("pr_url") == url  # recorded


def test_publish_no_branch_is_refused(home):
    _write_art("automod-test-2", branch=None)
    ok, reason = pub.publish("automod-test-2")
    assert not ok and "branch" in reason.lower()


def test_publish_existing_pr_returns_its_url(home, monkeypatch):
    _write_art("automod-test-3")
    monkeypatch.setattr(pub, "_git", lambda *a: _ok())

    def fake_gh(*a):
        if a[:2] == ("pr", "create"):
            return subprocess.CompletedProcess(
                a, 1, "", "a pull request for branch already exists")
        if a[:2] == ("pr", "view"):
            return subprocess.CompletedProcess(
                a, 0, "https://github.com/ulrichando/jarvis/pull/3\n", "")
        return subprocess.CompletedProcess(a, 1, "", "")

    monkeypatch.setattr(pub, "_gh", fake_gh)
    ok, url = pub.publish("automod-test-3")
    assert ok and url.endswith("/pull/3")


def test_publish_push_failure_is_reported(home, monkeypatch):
    _write_art("automod-test-4")
    monkeypatch.setattr(
        pub, "_git",
        lambda *a: subprocess.CompletedProcess(a, 1, "", "permission denied"))
    ok, reason = pub.publish("automod-test-4")
    assert not ok and "push failed" in reason


def test_publish_deploy_pushes_master_and_opens_closed_issue(home, monkeypatch):
    """A confirmed deploy is pushed to origin/master and recorded as a closed
    GitHub Issue (the shipped-fix record the user asked for)."""
    _write_art("automod-test-9", status="merged")
    calls = {"git": [], "gh": []}

    def fake_git(*a):
        calls["git"].append(a)
        if a[:1] == ("rev-parse",):
            return _ok("abcdef1234567890")
        return _ok()

    monkeypatch.setattr(pub, "_git", fake_git)

    def fake_gh(*a):
        calls["gh"].append(a)
        if a[:2] == ("issue", "create"):
            return subprocess.CompletedProcess(
                a, 0, "https://github.com/ulrichando/jarvis/issues/42\n", "")
        return _ok()

    monkeypatch.setattr(pub, "_gh", fake_gh)
    ok, url = pub.publish_deploy("automod-test-9")
    assert ok and url.endswith("/issues/42")
    assert ("push", "origin", "master") in calls["git"]
    assert any(g[:2] == ("issue", "create") for g in calls["gh"])
    assert any(g[:2] == ("issue", "close") for g in calls["gh"])  # closed = shipped record


def test_publish_deploy_fails_gracefully_when_push_rejected(home, monkeypatch):
    """A rejected push aborts cleanly without creating a phantom Issue."""
    _write_art("automod-test-10", status="merged")
    monkeypatch.setattr(
        pub, "_git",
        lambda *a: subprocess.CompletedProcess(a, 1, "", "rejected (non-fast-forward)"))
    called = {"gh": False}
    monkeypatch.setattr(pub, "_gh", lambda *a: called.__setitem__("gh", True) or _ok())
    ok, reason = pub.publish_deploy("automod-test-10")
    assert not ok and "push failed" in reason
    assert called["gh"] is False  # no Issue when the push didn't land


def test_publish_rollback_opens_open_issue_without_touching_origin(home, monkeypatch):
    """The normal rollback (deploy never reached origin): open a triage Issue,
    do NOT force-push (origin isn't ahead of the last-good SHA)."""
    _write_art("automod-test-11", status="auto-rolled-back")
    calls = {"git": [], "gh": []}

    def fake_git(*a):
        calls["git"].append(a)
        if a[:1] == ("rev-list",):
            return _ok("0")  # origin NOT ahead of rollback_sha → nothing to rewind
        return _ok()

    monkeypatch.setattr(pub, "_git", fake_git)

    def fake_gh(*a):
        calls["gh"].append(a)
        if a[:2] == ("issue", "create"):
            return subprocess.CompletedProcess(
                a, 0, "https://github.com/ulrichando/jarvis/issues/77\n", "")
        return _ok()

    monkeypatch.setattr(pub, "_gh", fake_gh)
    ok, url = pub.publish_rollback("automod-test-11", "deadbeefcafe1234")
    assert ok and url.endswith("/issues/77")
    assert any(g[:2] == ("issue", "create") for g in calls["gh"])
    assert not any(g[:2] == ("issue", "close") for g in calls["gh"])  # left OPEN for triage
    assert not any("--force-with-lease" in g for g in calls["git"])  # origin untouched


def test_publish_rollback_rewinds_origin_when_it_regressed(home, monkeypatch):
    """A confirmed-then-regressed deploy: origin leads the last-good SHA, so
    force-with-lease rewind it, then open the triage Issue."""
    _write_art("automod-test-12", status="auto-rolled-back")
    calls = {"git": [], "gh": []}

    def fake_git(*a):
        calls["git"].append(a)
        if a[:1] == ("rev-list",):
            return _ok("2")  # origin is 2 commits ahead → rewind needed
        return _ok()

    monkeypatch.setattr(pub, "_git", fake_git)
    monkeypatch.setattr(
        pub, "_gh",
        lambda *a: subprocess.CompletedProcess(
            a, 0, "https://github.com/ulrichando/jarvis/issues/78\n", "")
        if a[:2] == ("issue", "create") else _ok())
    ok, url = pub.publish_rollback("automod-test-12", "deadbeefcafe1234")
    assert ok and url.endswith("/issues/78")
    assert any(
        g[:2] == ("push", "--force-with-lease") for g in calls["git"]
    )  # origin rewound to the last-good SHA

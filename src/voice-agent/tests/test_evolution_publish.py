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

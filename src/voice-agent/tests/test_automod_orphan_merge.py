"""Orphan-proposal merge (2026-06-26).

A proposal can be reviewable (`status=pending`) yet have its git branch reaped —
the live `836e3d` bug, where `cmd_merge`/`deploy` failed because they referenced
the deleted branch. `_resolve_merge_ref` falls back to the recorded `head_sha`
(the commit object outlives the ref) so an orphan still deploys by SHA.
"""
from __future__ import annotations

from types import SimpleNamespace

from pipeline.automod import cli


def _fake_git(returncodes: dict[str, int]):
    """A `_git` stub keyed by a substring of the ref in the last positional arg."""
    def fake(*args: str, **kw):
        ref = args[-1] if args else ""
        for needle, rc in returncodes.items():
            if needle in ref:
                return SimpleNamespace(returncode=rc, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return fake


def test_resolve_merge_ref_prefers_existing_branch(monkeypatch):
    monkeypatch.setattr(cli, "_git", _fake_git({"feature-x": 0}))
    assert cli._resolve_merge_ref({"branch": "feature-x", "head_sha": "abc123"}) == "feature-x"


def test_resolve_merge_ref_falls_back_to_head_sha_when_branch_reaped(monkeypatch):
    # branch ref missing (rc=1), head_sha commit present (rc=0)
    monkeypatch.setattr(cli, "_git", _fake_git({"feature-x": 1, "abc123": 0}))
    assert cli._resolve_merge_ref({"branch": "feature-x", "head_sha": "abc123"}) == "abc123"


def test_resolve_merge_ref_none_when_branch_and_sha_both_gone(monkeypatch):
    monkeypatch.setattr(cli, "_git", _fake_git({"feature-x": 1, "abc123": 1}))
    assert cli._resolve_merge_ref({"branch": "feature-x", "head_sha": "abc123"}) is None

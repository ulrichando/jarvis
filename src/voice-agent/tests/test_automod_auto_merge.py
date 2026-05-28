"""Tests for the auto-merge + rollback ref feature.
Spec 2026-05-28."""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest.mock as mock
from pathlib import Path

import pytest


@pytest.fixture
def automod_home(tmp_path, monkeypatch):
    """Isolate ~/.jarvis/auto-mods to tmp."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis-home"))
    home = tmp_path / "jarvis-home" / "auto-mods"
    home.mkdir(parents=True, exist_ok=True)
    yield home


def test_mark_auto_merged_creates_new_artifact_when_missing(automod_home):
    """If no artifact exists yet, mark_auto_merged creates a minimal one
    so the revert path can find it."""
    from pipeline.automod.finalize import mark_auto_merged
    mark_auto_merged(
        "automod-2026-05-28-aaaa",
        rollback_ref="refs/automod-rollback/automod-2026-05-28-aaaa",
        rollback_sha="abc123",
        merge_sha="def456",
    )
    artifact = automod_home / "automod-2026-05-28-aaaa.json"
    assert artifact.exists()
    rec = json.loads(artifact.read_text(encoding="utf-8"))
    assert rec["id"] == "automod-2026-05-28-aaaa"
    assert rec["rollback_ref"] == "refs/automod-rollback/automod-2026-05-28-aaaa"
    assert rec["rollback_sha"] == "abc123"
    assert rec["merge_sha"] == "def456"
    assert "auto_merged_at" in rec


def test_mark_auto_merged_updates_existing_artifact(automod_home):
    """If an artifact already exists (from the normal finalize flow),
    mark_auto_merged should ADD the rollback metadata, not overwrite the
    record entirely."""
    from pipeline.automod.finalize import mark_auto_merged
    artifact = automod_home / "automod-2026-05-28-bbbb.json"
    existing = {
        "id": "automod-2026-05-28-bbbb",
        "branch": "automod/automod-2026-05-28-bbbb",
        "files_changed": ["src/voice-agent/foo.py"],
        "test_status": "passed",
    }
    artifact.write_text(json.dumps(existing), encoding="utf-8")

    mark_auto_merged(
        "automod-2026-05-28-bbbb",
        rollback_ref="refs/automod-rollback/automod-2026-05-28-bbbb",
        rollback_sha="cafe1",
        merge_sha="cafe2",
    )
    rec = json.loads(artifact.read_text(encoding="utf-8"))
    # Original fields preserved
    assert rec["branch"] == "automod/automod-2026-05-28-bbbb"
    assert rec["files_changed"] == ["src/voice-agent/foo.py"]
    assert rec["test_status"] == "passed"
    # New fields added
    assert rec["rollback_ref"] == "refs/automod-rollback/automod-2026-05-28-bbbb"
    assert rec["rollback_sha"] == "cafe1"
    assert rec["merge_sha"] == "cafe2"


def test_mark_auto_merged_is_idempotent(automod_home):
    """Re-calling overwrites the rollback metadata + auto_merged_at.
    Should not crash."""
    from pipeline.automod.finalize import mark_auto_merged
    mark_auto_merged("automod-2026-05-28-cccc", rollback_ref="r1",
                     rollback_sha="s1", merge_sha="m1")
    mark_auto_merged("automod-2026-05-28-cccc", rollback_ref="r2",
                     rollback_sha="s2", merge_sha="m2")
    rec = json.loads(
        (automod_home / "automod-2026-05-28-cccc.json").read_text()
    )
    assert rec["rollback_ref"] == "r2"
    assert rec["rollback_sha"] == "s2"
    assert rec["merge_sha"] == "m2"

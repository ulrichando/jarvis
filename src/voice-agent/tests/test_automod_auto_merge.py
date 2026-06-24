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


def test_blocklist_includes_automod_wrappers():
    """The CLI + wrapper scripts must be on the blocklist so auto-mod
    can't propose fixes to its own rollback machinery."""
    from pipeline.automod._state import HARD_BLOCKLIST_PATHS
    assert "bin/jarvis-automod-impl" in HARD_BLOCKLIST_PATHS
    assert "bin/jarvis-automod" in HARD_BLOCKLIST_PATHS
    assert "bin/jarvis-evolution-ondemand" in HARD_BLOCKLIST_PATHS
    assert "src/voice-agent/evolution/" in HARD_BLOCKLIST_PATHS


def test_is_blocked_path_rejects_wrapper_edits():
    """The is_blocked_path helper should return True for protected machinery."""
    from pipeline.automod._state import is_blocked_path
    assert is_blocked_path("bin/jarvis-automod-impl") is True
    assert is_blocked_path("bin/jarvis-automod") is True
    assert is_blocked_path("bin/jarvis-evolution-ondemand") is True
    assert is_blocked_path("src/voice-agent/evolution/fitness.py") is True


def test_revert_by_automod_id_reads_rollback_ref(automod_home):
    """Revert with an automod ID should look up the rollback ref from
    the artifact and use it for git reset."""
    from pipeline.automod import cli as automod_cli
    artifact = automod_home / "automod-2026-05-28-dddd.json"
    artifact.write_text(json.dumps({
        "id": "automod-2026-05-28-dddd",
        "rollback_ref": "refs/automod-rollback/automod-2026-05-28-dddd",
        "rollback_sha": "deadbeef",
        "merge_sha": "feedface",
    }), encoding="utf-8")

    with mock.patch("subprocess.check_call") as mock_check, \
         mock.patch("subprocess.run") as mock_run:
        # Simulate argparse Namespace
        class _Args:
            target = "automod-2026-05-28-dddd"
        rc = automod_cli.revert(_Args())
    assert rc == 0
    # check_call should have been invoked at least with a git reset --hard.
    calls = [args[0] for args, _kw in mock_check.call_args_list]
    flat = [item for sublist in calls for item in sublist]
    assert "reset" in flat
    assert "--hard" in flat
    assert "deadbeef" in flat


def test_revert_by_automod_id_returns_2_when_artifact_missing(automod_home):
    from pipeline.automod import cli as automod_cli
    class _Args:
        target = "automod-2026-05-28-eeee"  # doesn't exist
    rc = automod_cli.revert(_Args())
    assert rc == 2


def test_revert_by_automod_id_returns_2_when_no_rollback_metadata(automod_home):
    """Artifact exists but has no rollback_ref/sha (e.g. it was manually
    merged via the legacy path) → reject."""
    from pipeline.automod import cli as automod_cli
    artifact = automod_home / "automod-2026-05-28-ffff.json"
    artifact.write_text(json.dumps({
        "id": "automod-2026-05-28-ffff",
        "branch": "automod/automod-2026-05-28-ffff",
        # No rollback_ref, no rollback_sha
    }), encoding="utf-8")

    class _Args:
        target = "automod-2026-05-28-ffff"
    rc = automod_cli.revert(_Args())
    assert rc == 2

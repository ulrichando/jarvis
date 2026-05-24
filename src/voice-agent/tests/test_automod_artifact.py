"""Spec B (Plane 3) — artifact + audit log."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def test_write_artifact_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import artifact
    art = {
        "id": "automod-2026-05-24-001",
        "kind": "correction",
        "intent": "fix X",
        "branch": "automod/automod-2026-05-24-001",
        "parent_sha": "abc",
        "head_sha": "def",
        "files_changed": ["src/voice-agent/prompts/supervisor.md"],
        "diff_summary": "+2/-1",
        "test_output_tail": "2400 passed",
        "status": "pending",
        "created_at": "2026-05-24T00:00:00Z",
    }
    artifact.write(art)
    p = tmp_path / "auto-mods" / "automod-2026-05-24-001.json"
    assert p.exists()
    loaded = json.loads(p.read_text())
    assert loaded["status"] == "pending"
    assert loaded["id"] == "automod-2026-05-24-001"


def test_load_artifact_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import artifact
    art = {"id": "x", "kind": "explicit", "intent": "x", "status": "pending",
           "created_at": "2026-05-24T00:00:00Z"}
    artifact.write(art)
    loaded = artifact.load("x")
    assert loaded["id"] == "x"
    assert loaded["status"] == "pending"


def test_update_status(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import artifact
    art = {"id": "x", "kind": "explicit", "intent": "x", "status": "pending",
           "created_at": "2026-05-24T00:00:00Z"}
    artifact.write(art)
    artifact.update_status("x", "merged", merged_at="2026-05-24T01:00:00Z",
                           merge_sha="ghi")
    loaded = artifact.load("x")
    assert loaded["status"] == "merged"
    assert loaded["merged_at"] == "2026-05-24T01:00:00Z"
    assert loaded["merge_sha"] == "ghi"


def test_update_status_returns_updated_dict(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import artifact
    art = {"id": "x", "status": "pending", "created_at": "2026-05-24T00:00:00Z"}
    artifact.write(art)
    out = artifact.update_status("x", "rejected", rejection_reason="bad scope")
    assert out["status"] == "rejected"
    assert out["rejection_reason"] == "bad scope"


def test_audit_log_appends_with_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import artifact
    artifact.audit("automod_proposed", id="automod-2026-05-24-001",
                   intent_class="correction")
    p = tmp_path / "evolution_log.jsonl"
    assert p.exists()
    line = p.read_text().strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["kind"] == "automod_proposed"
    assert rec["id"] == "automod-2026-05-24-001"
    assert "ts" in rec


def test_audit_filter_drops_pytest_tmp_pollution(tmp_path, monkeypatch):
    """The audit writer must NOT amplify the pre-existing pytest-tmp pollution
    pattern documented in Spec A — drop any entry with 'anchor_path'
    starting with '/tmp/pytest-'."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import artifact
    artifact.audit("anchor_baseline_refreshed",
                   anchor_path="/tmp/pytest-of-ulrich/x/anchor.md")
    p = tmp_path / "evolution_log.jsonl"
    assert not p.exists() or not p.read_text().strip()


def test_audit_with_no_anchor_path_writes(tmp_path, monkeypatch):
    """Records without 'anchor_path' are unaffected by the filter."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import artifact
    artifact.audit("automod_merged", id="x", merge_sha="abc")
    p = tmp_path / "evolution_log.jsonl"
    assert p.exists()
    rec = json.loads(p.read_text().strip().splitlines()[-1])
    assert rec["id"] == "x"


def test_audit_records_have_ts_field(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import artifact
    artifact.audit("test_kind", x=1)
    p = tmp_path / "evolution_log.jsonl"
    rec = json.loads(p.read_text().strip().splitlines()[-1])
    assert "ts" in rec
    # Sanity: ISO 8601 UTC format
    assert rec["ts"].endswith("Z")

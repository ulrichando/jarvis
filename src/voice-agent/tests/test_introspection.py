"""Tests for JARVIS self-assessment (2026-06-23). No real model calls."""
from __future__ import annotations

from pipeline.automod import introspection


def test_read_self_assessment_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert introspection.read_self_assessment() is None


def test_gather_evidence_never_crashes(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    ev = introspection.gather_evidence()
    assert "generated_at" in ev
    assert "recent_failed_builds" in ev  # empty list when no artifacts


def test_run_self_assessment_errors_without_key(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = introspection.run_self_assessment()
    assert "error" in out
    assert "evidence" in out

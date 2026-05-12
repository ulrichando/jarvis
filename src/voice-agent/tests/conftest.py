"""Pytest config — set the env vars that production gates default-off.

The 2026-05-08 subagent disable (E in the voice-channel audit) wraps
each subagent's `enabled=` flag behind a `JARVIS_SUBAGENT_<NAME>=1`
opt-in. Tests still want to validate the registration / tool-factory /
schema shape AS IF those subagents were enabled — so we flip every
opt-in on at session start.

Tests that specifically validate the disable behavior should
monkeypatch the env var to "0" within their own scope.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_evolution_changelog(monkeypatch, tmp_path):
    """Every test gets a tmp_path-scoped evolution changelog dir.

    The evolution lifecycle (auto_stage/rollback/promote_eligible_staged)
    calls `changelog.append(...)` which writes to
    `~/Documents/jarvis-evolution/<date>.md` in production. Without this
    fixture, any test that exercises a tier transition pollutes the
    user's real changelog. autouse keeps it global — no per-test opt-in
    needed.
    """
    try:
        from pipeline.evolution import changelog
    except ImportError:
        return
    monkeypatch.setattr(changelog, "CHANGELOG_DIR", tmp_path / "changelog")


def pytest_configure(config) -> None:
    for name in (
        "SUMMARIZE",
        "WEATHER",
        "RESEARCHER",
        "VALIDATOR",
        "CODE_REVIEWER",
        "MEMORY_RECALL",
        "GITHUB",
    ):
        os.environ.setdefault(f"JARVIS_SUBAGENT_{name}", "1")
    # Memory consolidator: default OFF in tests so existing extractor
    # tests (test_extractor_marks_success_on_parse etc.) don't trip
    # the trigger counter and schedule background asyncio tasks that
    # leak across tests. Tests that specifically validate the
    # consolidator monkeypatch.setenv("JARVIS_MEMORY_CONSOLIDATOR", "1")
    # in their own scope.
    os.environ.setdefault("JARVIS_MEMORY_CONSOLIDATOR", "0")

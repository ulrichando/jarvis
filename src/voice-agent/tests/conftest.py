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


@pytest.fixture(scope="session")
def _provider_registry_baseline():
    """Capture the BUILT-IN provider registry (image: openai/xai, video: xai).

    Built-in providers register at module import; plugin backends
    (fal / openai-codex / web/*) register only when ``discover_plugins()`` runs
    inside a test. Importing the built-in tool modules here and snapshotting —
    without triggering discovery — yields a built-ins-only baseline.
    """
    import tools.image_gen  # noqa: F401 — registers image providers (openai, xai)
    import tools.video_gen  # noqa: F401 — registers video provider (xai)
    from tools import _provider_registry as pr

    return {kind: dict(names) for kind, names in pr._providers.items()}


@pytest.fixture(autouse=True)
def _isolate_provider_registry(_provider_registry_baseline):
    """Restore the global provider registry to its built-in baseline after each test.

    The process-global ``tools._provider_registry`` is mutated by plugin
    discovery (which registers fal / openai-codex / the web backends) and by
    provider unit tests calling ``reset_providers(kind)`` — the latter wipes the
    module-import-registered built-ins, which (being cached) never re-register.
    Without isolation, those mutations leak into later tests that assert on exact
    provider sets: e.g. ``test_image_gen`` (``== {openai, xai}``) or
    ``test_video_gen`` (``"xai" in video providers``). Restoring after every test
    keeps them independent. Regression guard for the 2026-05-22 Hermes plugin port.
    """
    yield
    from tools import _provider_registry as pr

    pr._providers.clear()
    pr._providers.update(
        {kind: dict(names) for kind, names in _provider_registry_baseline.items()}
    )


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

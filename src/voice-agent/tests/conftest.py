"""Pytest config — fixtures and env-var setup for the voice-agent test suite."""
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
    # Memory consolidator: default OFF in tests so existing extractor
    # tests don't trip the trigger counter and schedule background asyncio
    # tasks that leak across tests. Tests that specifically validate the
    # consolidator use monkeypatch.setenv("JARVIS_MEMORY_CONSOLIDATOR", "1").
    os.environ.setdefault("JARVIS_MEMORY_CONSOLIDATOR", "0")

    # Strip the auto-mod build wrapper's injected env vars before the suite runs.
    # A build runs THIS suite (the agent's pre-commit run + finalize's re-run)
    # with JARVIS_AUTOMOD_BASE_REF pinned to the worktree's base SHA (+ worktree
    # paths). Those must NOT leak into the finalize/revert tests: they call
    # finalize_branch(), which would resolve the diff base to a SHA absent from
    # their tmp repos → 'failed' instead of 'pending'. Live 2026-06-23: every
    # build's own pytest run went red on 6 finalize tests → the agent refused to
    # commit → no_commit_landed. Popping here (not the separate finalize process)
    # isolates the tests while leaving the real build's finalize env intact.
    for _var in (
        "JARVIS_AUTOMOD_BASE_REF", "JARVIS_AUTOMOD_REPO_ROOT",
        "JARVIS_AUTOMOD_TOOLING_ROOT", "JARVIS_AUTOMOD_SKIP_BASE_FETCH",
        "JARVIS_AUTOMOD_BUILD_MODEL", "JARVIS_AUTOMOD_NO_NETWORK",
    ):
        os.environ.pop(_var, None)

    # Hermetic suite: live per-machine runtime state must never reach
    # prompt-shape assertions. A developer's ~/.jarvis/SOUL.md override
    # replaces the entire 18-section persona (so soul-parity tests would
    # assert against the override), and the real conversations.db gets
    # rendered into the RECENT CONVERSATIONS block of the volatile
    # suffix. Point both at paths that don't exist; tests that exercise
    # these features set their own fixture paths explicitly.
    os.environ.setdefault(
        "JARVIS_SOUL_OVERRIDE_PATH",
        str(Path(tempfile.gettempdir()) / "jarvis-test-no-soul" / "SOUL.md"),
    )
    os.environ.setdefault(
        "JARVIS_CONVERSATION_PATH",
        str(Path(tempfile.mkdtemp(prefix="jarvis-test-conv-")) / "conversations.db"),
    )

    # Same hermeticity rule for telemetry: pipeline.turn_telemetry binds
    # DEFAULT_DB_PATH at import from JARVIS_TELEMETRY_PATH, and any code
    # path that logs by default (e.g. the computer_use audit trail) would
    # otherwise write into the developer's real
    # ~/.local/share/jarvis/turn_telemetry.db during the suite. Tests that
    # assert on telemetry pass their own tmp db_path explicitly.
    os.environ.setdefault(
        "JARVIS_TELEMETRY_PATH",
        str(Path(tempfile.mkdtemp(prefix="jarvis-test-tele-")) / "turn_telemetry.db"),
    )

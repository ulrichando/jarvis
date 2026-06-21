"""Tests for the nightly self-evolution trigger (proposal-only orchestration)."""
from __future__ import annotations

import pytest

from pipeline.automod import deploy, nightly


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    # Default: no real telemetry → user not "recently active" unless a test says so.
    monkeypatch.setattr(nightly, "_user_recently_active", lambda: False)
    return tmp_path


def _patch_pipeline(monkeypatch, *, detected=0, spawned=0):
    import pipeline.automod.patterns as patterns
    import pipeline.automod.spawner as spawner
    monkeypatch.setattr(patterns, "scan_and_emit", lambda: detected)

    async def _drain():
        return spawned

    monkeypatch.setattr(spawner, "drain_queue", _drain)


def test_skips_when_deploy_in_flight(home, monkeypatch):
    deploy.write_marker({"automod_id": "x", "rollback_sha": "y"})
    assert nightly.run() == {"skipped": "deploy-in-flight"}


def test_skips_when_user_active(home, monkeypatch):
    monkeypatch.setattr(nightly, "_user_recently_active", lambda: True)
    assert nightly.run() == {"skipped": "user-active"}


def test_shadow_mode_detector_only(home, monkeypatch):
    # Detector finds intents; spawn is shadow (drain returns 0); no publish.
    _patch_pipeline(monkeypatch, detected=3, spawned=0)
    out = nightly.run()
    assert out["detected"] == 3 and out["spawned"] == 0 and out["published"] == 0


def test_no_publish_without_autopublish(home, monkeypatch):
    monkeypatch.delenv("JARVIS_EVOLUTION_AUTOPUBLISH", raising=False)
    _patch_pipeline(monkeypatch, detected=1, spawned=2)
    out = nightly.run()
    assert out["spawned"] == 2 and out["published"] == 0  # spawned but not published


def test_publishes_when_spawned_and_autopublish(home, monkeypatch):
    monkeypatch.setenv("JARVIS_EVOLUTION_AUTOPUBLISH", "1")
    _patch_pipeline(monkeypatch, detected=1, spawned=1)
    import pipeline.automod.cli as cli
    import pipeline.automod.publish as publish
    monkeypatch.setattr(cli, "cmd_list",
                        lambda only_pending=True: [{"id": "automod-x"}])
    published = {}

    def fake_publish(i):
        published["id"] = i
        return True, "https://gh/pr/1"

    monkeypatch.setattr(publish, "publish", fake_publish)
    out = nightly.run()
    assert out["spawned"] == 1 and out["published"] == 1
    assert published["id"] == "automod-x"


def test_publish_skips_already_published(home, monkeypatch):
    monkeypatch.setenv("JARVIS_EVOLUTION_AUTOPUBLISH", "1")
    _patch_pipeline(monkeypatch, detected=0, spawned=1)
    import pipeline.automod.cli as cli
    import pipeline.automod.publish as publish
    # One already has a PR; one doesn't.
    monkeypatch.setattr(cli, "cmd_list", lambda only_pending=True: [
        {"id": "automod-old", "pr_url": "https://gh/pr/9"},
        {"id": "automod-new"},
    ])
    calls = []
    monkeypatch.setattr(publish, "publish",
                        lambda i: calls.append(i) or (True, "https://gh/pr/2"))
    out = nightly.run()
    assert calls == ["automod-new"]      # only the unpublished one
    assert out["published"] == 1


def test_run_never_raises_on_detector_error(home, monkeypatch):
    import pipeline.automod.patterns as patterns
    import pipeline.automod.spawner as spawner

    def _boom():
        raise RuntimeError("telemetry locked")

    monkeypatch.setattr(patterns, "scan_and_emit", _boom)

    async def _drain():
        return 0

    monkeypatch.setattr(spawner, "drain_queue", _drain)
    out = nightly.run()   # must not raise
    assert out["detected"] == 0 and "detect_error" in out

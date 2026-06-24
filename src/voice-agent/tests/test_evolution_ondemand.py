"""Tests for the on-demand self-evolution runner."""
from __future__ import annotations


def test_ondemand_refuses_when_spawn_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("JARVIS_AUTOMOD_SPAWN_LIVE", raising=False)
    from pipeline.automod import ondemand

    out = ondemand.run("automod-test")
    assert out["skipped"] == "spawn-disabled"
    assert out["spawned"] == 0


def test_ondemand_drains_one_intent_and_publishes(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "1")
    monkeypatch.setenv("JARVIS_EVOLUTION_AUTOPUBLISH", "1")

    from pipeline.automod import ondemand
    import pipeline.automod.spawner as spawner
    import pipeline.automod.publish as publish

    async def fake_drain_queue(*, only_id=None, force=False):
        assert only_id == "automod-test"
        assert force is False  # manual builds share the 5/day evolution cap
        return 1

    monkeypatch.setattr(spawner, "drain_queue", fake_drain_queue)
    monkeypatch.setattr(publish, "publish",
                        lambda _id: (True, "https://example.test/pr/1"))

    out = ondemand.run("automod-test")
    assert out["spawned"] == 1
    assert out["published"] == 1
    assert out["pr_url"].endswith("/pr/1")

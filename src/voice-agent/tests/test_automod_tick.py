"""Phase 1, Task 4: _automod_tick always scans (queues for review) but only
builds (drains) in AUTO mode. asyncio.run() per the repo convention."""
import asyncio

from pipeline.automod import _state


def _patch(monkeypatch):
    calls = {"scan": 0, "drain": 0}
    import pipeline.automod.patterns as patterns
    import pipeline.automod.spawner as spawner
    monkeypatch.setattr(patterns, "scan_and_emit",
                        lambda: calls.__setitem__("scan", calls["scan"] + 1))

    async def fake_drain(**kw):
        calls["drain"] += 1
        return 0

    monkeypatch.setattr(spawner, "drain_queue", fake_drain)
    return calls


def test_tick_scans_but_does_not_build_in_manual(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))  # manual: no .evolution-auto
    calls = _patch(monkeypatch)
    from jarvis_agent import _automod_tick
    asyncio.run(_automod_tick())
    assert calls["scan"] == 1   # always queues for review
    assert calls["drain"] == 0  # manual mode never builds


def test_tick_builds_in_auto(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    _state.set_auto_mode(True)  # .evolution-auto present
    calls = _patch(monkeypatch)
    from jarvis_agent import _automod_tick
    asyncio.run(_automod_tick())
    assert calls["scan"] == 1
    assert calls["drain"] == 1  # auto mode builds

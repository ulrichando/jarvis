"""Settings file watcher: scans the watched-files mapping at boot
+ on each tick, publishes settings.value.changed events when values
have changed since last seen. Sensitive files (keys.env) NEVER get
published, even if their path is in the watched mapping."""
import json
import sys
from pathlib import Path

import fakeredis.aioredis
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import settings_watcher


def _decode(entries):
    return [json.loads(f["data"]) for _, f in entries]


@pytest.mark.asyncio
async def test_first_pass_publishes_one_event_per_file(tmp_path):
    """Initial scan: each present file in the watched mapping → one event."""
    (tmp_path / "voice-model").write_text("llama-3.3-70b-versatile\n")
    (tmp_path / "tts-provider").write_text("groq:troy\n")
    # cli-model deliberately absent — should be skipped, not crash.

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    watched = {
        "voice-model": tmp_path / "voice-model",
        "tts-provider": tmp_path / "tts-provider",
        "cli-model": tmp_path / "cli-model",  # missing
    }
    state: dict[str, str] = {}
    n = await settings_watcher.scan_once(redis, watched, state)
    assert n == 2

    entries = await redis.xrange("events:settings")
    assert len(entries) == 2
    keys = {e["payload"]["key"] for e in _decode(entries)}
    assert keys == {"voice-model", "tts-provider"}
    values = {e["payload"]["key"]: e["payload"]["value"] for e in _decode(entries)}
    assert values["voice-model"] == "llama-3.3-70b-versatile"
    assert values["tts-provider"] == "groq:troy"


@pytest.mark.asyncio
async def test_unchanged_files_dont_republish(tmp_path):
    """Second pass over unchanged files publishes nothing."""
    (tmp_path / "voice-model").write_text("llama-3.3-70b-versatile\n")
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    watched = {"voice-model": tmp_path / "voice-model"}
    state: dict[str, str] = {}

    n1 = await settings_watcher.scan_once(redis, watched, state)
    n2 = await settings_watcher.scan_once(redis, watched, state)
    assert n1 == 1 and n2 == 0


@pytest.mark.asyncio
async def test_value_change_publishes_one(tmp_path):
    f = tmp_path / "voice-model"
    f.write_text("v1\n")
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    watched = {"voice-model": f}
    state: dict[str, str] = {}

    await settings_watcher.scan_once(redis, watched, state)
    f.write_text("v2\n")
    n = await settings_watcher.scan_once(redis, watched, state)
    assert n == 1

    entries = await redis.xrange("events:settings")
    last = json.loads(entries[-1][1]["data"])
    assert last["payload"]["value"] == "v2"


@pytest.mark.asyncio
async def test_keys_env_blocklist(tmp_path):
    """If a sensitive-named file is in the watched mapping, the
    watcher must REFUSE — fail loud at startup with no events
    published."""
    f = tmp_path / "keys.env"
    f.write_text("GROQ_API_KEY=secret\n")
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    watched = {"keys.env": f}
    state: dict[str, str] = {}

    with pytest.raises(ValueError, match="sensitive"):
        await settings_watcher.scan_once(redis, watched, state)

    entries = await redis.xrange("events:settings")
    assert entries == []

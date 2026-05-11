"""Tests for get_location — IP geolocation tool used by weather and
any other location-aware subagent.

We don't hit the live network here — that would be flaky in CI. Instead
we exercise:
  - The manual override path (~/.jarvis/location-override file)
  - The 10-min cache (cache hit returns without curl)
  - The output-format expectation when override is set

Live network paths (ipinfo.io / ip-api.com) are exercised by the agent
on startup; failures there log to debug and fall back gracefully.
"""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import jarvis_agent


def _call_get_location() -> str:
    """Run the @function_tool's underlying coroutine."""
    fn = jarvis_agent.get_location._func
    return asyncio.run(fn())


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Each test gets a fresh override-path + cleared cache so we can't
    leak state between cases."""
    fake_override = tmp_path / "location-override"
    monkeypatch.setattr(
        jarvis_agent, "_LOCATION_OVERRIDE_PATH", fake_override
    )
    jarvis_agent._LOCATION_CACHE["value"] = None
    jarvis_agent._LOCATION_CACHE["ts"] = 0.0
    yield
    jarvis_agent._LOCATION_CACHE["value"] = None
    jarvis_agent._LOCATION_CACHE["ts"] = 0.0


# ── Manual override path ────────────────────────────────────────────


def test_override_file_takes_precedence(tmp_path, monkeypatch):
    """If ~/.jarvis/location-override exists, its contents become the
    canonical answer regardless of cache or IP geo. This is how the
    user pins JARVIS to a specific city when their VPN / mobile NAT
    confuses the IP-based lookup."""
    override = tmp_path / "loc"
    override.write_text("Yaoundé, Centre, Cameroon\n")
    monkeypatch.setattr(jarvis_agent, "_LOCATION_OVERRIDE_PATH", override)

    result = _call_get_location()
    assert result == "Yaoundé, Centre, Cameroon"


def test_override_file_strips_whitespace(tmp_path, monkeypatch):
    override = tmp_path / "loc"
    override.write_text("  Paris  \n\n")
    monkeypatch.setattr(jarvis_agent, "_LOCATION_OVERRIDE_PATH", override)
    assert _call_get_location() == "Paris"


def test_empty_override_file_falls_through(tmp_path, monkeypatch):
    """An empty override file shouldn't shadow the cache / IP lookup —
    treat as 'no override'. We pre-fill the cache to verify it's used."""
    override = tmp_path / "loc"
    override.write_text("   \n")  # whitespace only
    monkeypatch.setattr(jarvis_agent, "_LOCATION_OVERRIDE_PATH", override)
    jarvis_agent._LOCATION_CACHE["value"] = "From cache"
    jarvis_agent._LOCATION_CACHE["ts"] = time.monotonic()

    assert _call_get_location() == "From cache"


# ── Cache path ──────────────────────────────────────────────────────


def test_fresh_cache_returns_without_network():
    """A cache value < TTL should be returned directly. We can verify
    this by setting the cache to a sentinel and confirming we get it
    back — no network call."""
    jarvis_agent._LOCATION_CACHE["value"] = "Tokyo, Japan"
    jarvis_agent._LOCATION_CACHE["ts"] = time.monotonic()
    assert _call_get_location() == "Tokyo, Japan"


def test_stale_cache_falls_through():
    """Cache older than TTL → fall through to network. We can't test
    the network half without mocking subprocess, but we CAN verify the
    cache is treated as stale by setting ts way in the past."""
    jarvis_agent._LOCATION_CACHE["value"] = "Stale value"
    # Set to far in the past — well beyond 10-min TTL
    jarvis_agent._LOCATION_CACHE["ts"] = time.monotonic() - 99999.0
    # Without network mocking the call may hit the live API or return
    # "Location unavailable". Either way it should NOT be "Stale value".
    result = _call_get_location()
    assert result != "Stale value"

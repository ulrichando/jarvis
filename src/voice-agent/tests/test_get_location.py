"""Tests for saved_address + current_location.

Split from the unified get_location() 2026-05-17 after a confab-failure
where the LLM extended an IP-geo "Columbus, Ohio" answer into a fake
"Parsons Avenue, Columbus, Ohio" street-level address. The split puts
the user's declared address (file-backed) on one tool and live IP/Wi-Fi
positioning on another, and tags current_location's output with a
precision marker so the LLM can't voice detail finer than the signal
supports.

We don't hit the live network here — that would be flaky in CI. Live
network paths (ipinfo.io / ip-api.com / Google Geolocation API) are
exercised by the agent on startup; failures there log to debug and
fall back gracefully.
"""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import jarvis_agent


def _call_saved_address() -> str:
    """Run the @function_tool's underlying coroutine."""
    fn = jarvis_agent.saved_address._func
    return asyncio.run(fn())


def _call_current_location() -> str:
    fn = jarvis_agent.current_location._func
    return asyncio.run(fn())


def _call_set_saved_address(address: str) -> str:
    fn = jarvis_agent.set_saved_address._func
    return asyncio.run(fn(address))


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Each test gets a fresh saved-address path + cleared current_location
    cache so state can't leak between cases."""
    fake_path = tmp_path / "saved-address"
    monkeypatch.setattr(jarvis_agent, "_SAVED_ADDRESS_PATH", fake_path)
    jarvis_agent._CURRENT_LOCATION_CACHE["value"] = None
    jarvis_agent._CURRENT_LOCATION_CACHE["ts"] = 0.0
    yield
    jarvis_agent._CURRENT_LOCATION_CACHE["value"] = None
    jarvis_agent._CURRENT_LOCATION_CACHE["ts"] = 0.0


# ── saved_address ───────────────────────────────────────────────────


def test_saved_address_returns_file_contents(tmp_path, monkeypatch):
    """File present → contents returned verbatim, prefixed with a
    'Saved address:' marker so the LLM knows the source."""
    p = tmp_path / "saved-address"
    p.write_text("Douala, Cameroon\n")
    monkeypatch.setattr(jarvis_agent, "_SAVED_ADDRESS_PATH", p)

    result = _call_saved_address()
    assert "Douala, Cameroon" in result
    assert "Saved address:" in result
    assert "set by user" in result


def test_saved_address_strips_whitespace(tmp_path, monkeypatch):
    p = tmp_path / "saved-address"
    p.write_text("  Paris, France  \n\n")
    monkeypatch.setattr(jarvis_agent, "_SAVED_ADDRESS_PATH", p)

    result = _call_saved_address()
    assert "Paris, France" in result
    # Trailing/leading whitespace must not survive into the marker.
    assert "  Paris" not in result


def test_saved_address_no_file_returns_unset_marker(tmp_path, monkeypatch):
    """Critical: when no address is saved, the tool MUST NOT guess.
    It returns an explicit 'No saved address' string that tells the
    LLM to ask the user and call set_saved_address — preventing the
    'voice an IP-geo answer as the user's address' failure mode."""
    p = tmp_path / "missing"  # doesn't exist
    monkeypatch.setattr(jarvis_agent, "_SAVED_ADDRESS_PATH", p)

    result = _call_saved_address()
    assert "No saved address" in result
    assert "set_saved_address" in result
    # The instruction "Do NOT guess" must be in the result so a confused
    # LLM reading the tool output can't rationalize a guess.
    assert "Do NOT guess" in result or "Do NOT voice" in result


def test_saved_address_empty_file_returns_unset_marker(tmp_path, monkeypatch):
    """Empty/whitespace-only file is the same as no file — must not
    silently substitute the IP-geo answer."""
    p = tmp_path / "saved-address"
    p.write_text("   \n\n")
    monkeypatch.setattr(jarvis_agent, "_SAVED_ADDRESS_PATH", p)

    result = _call_saved_address()
    assert "No saved address" in result


# ── set_saved_address ───────────────────────────────────────────────


def test_set_saved_address_writes_file(tmp_path, monkeypatch):
    p = tmp_path / "saved-address"
    monkeypatch.setattr(jarvis_agent, "_SAVED_ADDRESS_PATH", p)

    msg = _call_set_saved_address("Douala, Cameroon")
    assert "Douala, Cameroon" in msg
    assert p.read_text().strip() == "Douala, Cameroon"


def test_set_saved_address_round_trip(tmp_path, monkeypatch):
    """Write → read via saved_address must return the same value."""
    p = tmp_path / "saved-address"
    monkeypatch.setattr(jarvis_agent, "_SAVED_ADDRESS_PATH", p)

    _call_set_saved_address("Tokyo, Japan")
    result = _call_saved_address()
    assert "Tokyo, Japan" in result


def test_set_saved_address_empty_clears(tmp_path, monkeypatch):
    """Calling set_saved_address('') deletes the file so saved_address
    returns the unset marker again."""
    p = tmp_path / "saved-address"
    p.write_text("Old value\n")
    monkeypatch.setattr(jarvis_agent, "_SAVED_ADDRESS_PATH", p)

    _call_set_saved_address("")
    assert not p.exists()
    assert "No saved address" in _call_saved_address()


# ── current_location ────────────────────────────────────────────────


def test_current_location_does_not_consult_saved_address(tmp_path, monkeypatch):
    """Separation-of-concerns: current_location must NOT fall back to
    the saved-address file. Asking 'where am I' should never silently
    return the user's home address when they're traveling, and asking
    'what's my address' should never return an IP-geo guess.

    Pre-fill the saved-address file with a sentinel; current_location's
    result must not contain it (it'll go to network/cache instead).
    """
    sa = tmp_path / "saved-address"
    sa.write_text("Sentinel-Saved-Address\n")
    monkeypatch.setattr(jarvis_agent, "_SAVED_ADDRESS_PATH", sa)

    # Pre-fill cache so we don't hit the network in CI.
    jarvis_agent._CURRENT_LOCATION_CACHE["value"] = (
        "Columbus, Ohio, US (precision=city; source=ip-geolocation). "
        "Cannot resolve a street address from this signal; for the "
        "user's home/work address, call saved_address."
    )
    jarvis_agent._CURRENT_LOCATION_CACHE["ts"] = time.monotonic()

    result = _call_current_location()
    assert "Sentinel-Saved-Address" not in result
    assert "Columbus, Ohio, US" in result


def test_current_location_cache_hit_returns_cached():
    """A cache value < TTL returns directly with no network call."""
    sentinel = "Tokyo, Japan (precision=city; source=ip-geolocation). xyz"
    jarvis_agent._CURRENT_LOCATION_CACHE["value"] = sentinel
    jarvis_agent._CURRENT_LOCATION_CACHE["ts"] = time.monotonic()

    assert _call_current_location() == sentinel


def test_current_location_stale_cache_falls_through():
    """Cache older than TTL → fall through. Without network mocking the
    call may hit the live API or return 'Location unavailable'. Either
    way it should NOT be the stale sentinel."""
    jarvis_agent._CURRENT_LOCATION_CACHE["value"] = "Stale value"
    jarvis_agent._CURRENT_LOCATION_CACHE["ts"] = time.monotonic() - 99999.0
    result = _call_current_location()
    assert result != "Stale value"


def test_current_location_carries_precision_marker():
    """The whole point of the split: every successful current_location
    return must carry a precision=<level> marker the LLM can use to
    refuse street-level voicing. Test against a cache-hit string the
    real tool would have produced.
    """
    cached = (
        "Columbus, Ohio, US (precision=city; source=ip-geolocation). "
        "Cannot resolve a street address from this signal; for the "
        "user's home/work address, call saved_address."
    )
    jarvis_agent._CURRENT_LOCATION_CACHE["value"] = cached
    jarvis_agent._CURRENT_LOCATION_CACHE["ts"] = time.monotonic()

    result = _call_current_location()
    assert "precision=" in result
    assert "source=" in result


# ── Precision band derivation ───────────────────────────────────────


def test_precision_band_thresholds():
    """The precision band derived from Google's accuracy_m maps cleanly
    onto the voice-friendly enum. Conservative: never claim 'rooftop'
    from accuracy alone, even when very precise."""
    f = jarvis_agent._precision_from_accuracy_m
    assert f(25.0) == "street"
    assert f(200.0) == "block"
    assert f(2000.0) == "city"
    assert f(20000.0) == "region"
    assert f(200000.0) == "country"
    # Unknown accuracy → default to city (never claim block/street
    # without a real signal).
    assert f(None) == "city"

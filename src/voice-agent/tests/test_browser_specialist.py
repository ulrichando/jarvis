"""Tests for the browser specialist + tools.browser_ext client.

Smoke-level — verifies the registry spec is well-formed and the HTTP
client returns the expected `{ok: False, ...}` shape on unreachable
bridge. End-to-end (real Chrome + extension) is dogfood-only.
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from specialists.registry import clear, get
from specialists import browser as browser_mod


@pytest.fixture(autouse=True)
def _reset_registry():
    clear()
    yield
    clear()


def test_browser_spec_registers_enabled():
    browser_mod.register_browser()
    spec = get("browser")
    assert spec is not None
    assert spec.transfer_tool == "transfer_to_browser"
    assert spec.enabled is True
    # Routing-distinguishing keywords must be in the LLM-visible
    # description so it doesn't conflate with desktop/planner
    assert "web browser" in spec.when_to_use.lower() or "in a tab" in spec.when_to_use.lower()
    assert "transfer_to_desktop" in spec.when_to_use  # negative-routing example


def test_browser_spec_loads_all_ext_tools():
    browser_mod.register_browser()
    spec = get("browser")
    tools = spec.tool_factory()
    # ext_* tools — see tools.browser_ext.ALL_TOOLS (count tracks
    # additions like ext_new_tab; assert against the source list rather
    # than a magic number to avoid bit-rot).
    from tools.browser_ext import ALL_TOOLS
    assert len(tools) == len(ALL_TOOLS)
    assert len(tools) >= 37  # Phase C landed 2026-05-02: 26 base + 4 Phase A + 4 Phase B + 3 Phase C


def test_browser_instructions_mention_destructive_gate():
    """The prompt must teach the specialist to confirm before posting,
    sending, buying, etc. Without this the LLM may post a tweet from
    ambient room noise — verified failure mode in the legacy
    browser_task path that this specialist replaces."""
    browser_mod.register_browser()
    spec = get("browser")
    # Match by an explicit substring — confirmation gate is non-negotiable
    assert "confirm" in spec.instructions.lower()
    assert "confirmed=True" in spec.instructions or "confirmed" in spec.instructions


def test_ext_post_returns_structured_error_when_bridge_down(monkeypatch):
    """If the bridge isn't running, the client should return a
    `{ok: False, error: ...}` dict instead of raising — gives the LLM
    actionable text instead of crashing the specialist."""
    # Point at a definitely-unused port
    monkeypatch.setenv("JARVIS_BRIDGE_URL", "http://localhost:1")

    # Import AFTER monkeypatch so the env wins
    import importlib
    import tools.browser_ext
    importlib.reload(tools.browser_ext)

    out = asyncio.run(tools.browser_ext._post("get_url"))
    assert isinstance(out, dict)
    assert out.get("ok") is False
    assert "error" in out


def test_ext_summarize_handles_failure_payloads():
    import tools.browser_ext
    s = tools.browser_ext._summarize({"ok": False, "error": "extension not connected"})
    assert "extension not connected" in s


def test_ext_summarize_returns_value_field_when_present():
    import tools.browser_ext
    s = tools.browser_ext._summarize({"ok": True, "value": "https://example.com"})
    assert s == "https://example.com"


def test_ext_summarize_trims_long_payloads():
    import tools.browser_ext
    long_text = "a" * 3000
    s = tools.browser_ext._summarize({"ok": True, "text": long_text}, max_chars=500)
    assert len(s) <= 501  # 500 + the trailing ellipsis
    assert s.endswith("…")


def test_browser_when_to_use_distinguishes_from_desktop():
    """Routing test: the LLM should know browser ≠ desktop. Both
    when_to_use strings reference the other to make the distinction
    LLM-visible at routing time."""
    browser_mod.register_browser()
    spec = get("browser")
    # Mentions desktop as the "open the app" alternative
    assert "transfer_to_desktop" in spec.when_to_use

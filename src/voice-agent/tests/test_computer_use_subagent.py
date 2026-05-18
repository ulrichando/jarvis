"""Tests for subagents/computer_use.py — registration gating + Wayland
abort + safety_confirm_cb wiring."""
import asyncio
import os
import pytest


def test_register_skips_when_env_disabled(monkeypatch):
    """Default OFF: register_computer_use is a no-op when env var
    is unset or != '1'."""
    monkeypatch.delenv("JARVIS_SUBAGENT_COMPUTER_USE", raising=False)
    from subagents import computer_use as cu_mod
    from subagents.registry import _REGISTRY, clear
    clear()
    cu_mod.register_computer_use()
    assert "computer_use" not in _REGISTRY


def test_register_creates_spec_when_env_enabled(monkeypatch):
    monkeypatch.setenv("JARVIS_SUBAGENT_COMPUTER_USE", "1")
    from subagents import computer_use as cu_mod
    from subagents.registry import _REGISTRY, clear, get
    clear()
    cu_mod.register_computer_use()
    spec = get("computer_use")
    assert spec is not None
    assert spec.tools_required is False   # tool-less subagent
    assert spec.pre_transfer is not None


@pytest.mark.asyncio
async def test_pre_transfer_aborts_on_wayland(monkeypatch):
    """When WAYLAND_DISPLAY is set, pre_transfer returns an abort
    string before any X11 probe."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    from subagents.computer_use import _ensure_x11_session
    result = await _ensure_x11_session(
        context=None, request="open kdenlive", supervisor=None,
    )
    assert result is not None
    assert "X11" in result or "Wayland" in result


@pytest.mark.asyncio
async def test_pre_transfer_proceeds_on_x11(monkeypatch):
    """When WAYLAND_DISPLAY is unset and xdpyinfo succeeds, returns None."""
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    from subagents import computer_use as cu_mod

    async def fake_xdpyinfo():
        return True
    monkeypatch.setattr(cu_mod, "_xdpyinfo_ok", fake_xdpyinfo)

    result = await cu_mod._ensure_x11_session(
        context=None, request="x", supervisor=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_pre_transfer_aborts_when_xdpyinfo_fails(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    from subagents import computer_use as cu_mod

    async def fake_xdpyinfo():
        return False
    monkeypatch.setattr(cu_mod, "_xdpyinfo_ok", fake_xdpyinfo)

    result = await cu_mod._ensure_x11_session(
        context=None, request="x", supervisor=None,
    )
    assert result is not None
    assert "X11" in result or "display" in result.lower()


@pytest.mark.asyncio
async def test_safety_confirm_cb_round_trip():
    """The safety_confirm_cb posts a phrase to the supervisor session,
    awaits the next user turn's yes/no via a Future, returns the bool."""
    from subagents.computer_use import build_safety_confirm_cb

    class FakeSession:
        def __init__(self):
            self.spoken = []
            self._cua_confirm_future = None
            self._cua_confirm_phrase = None
        async def say(self, text):
            self.spoken.append(text)

    sess = FakeSession()
    cb = build_safety_confirm_cb(sess, timeout_s=1.0)
    fut = asyncio.create_task(cb("Click Delete? "))

    # Simulate user replying "yes" after a short delay
    await asyncio.sleep(0.05)
    assert sess._cua_confirm_future is not None
    sess._cua_confirm_future.set_result(True)

    result = await fut
    assert result is True
    assert "Click Delete?" in sess.spoken[0]


@pytest.mark.asyncio
async def test_safety_confirm_cb_timeout_returns_false():
    from subagents.computer_use import build_safety_confirm_cb

    class FakeSession:
        def __init__(self):
            self._cua_confirm_future = None
        async def say(self, text): pass

    sess = FakeSession()
    cb = build_safety_confirm_cb(sess, timeout_s=0.1)
    result = await cb("Delete X?")
    assert result is False

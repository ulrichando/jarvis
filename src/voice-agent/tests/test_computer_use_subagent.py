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

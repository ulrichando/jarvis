"""Tests for pipeline/service_control.py — Linux + Windows dispatch parity.

Phase 2.3 of the cross-platform footgun cleanup. The helper replaces
the previous inline ``subprocess.Popen(["systemctl", "--user", "restart",
...])`` + async create_subprocess patterns with a single platform-aware
function so:

  * On Linux the systemctl argv is preserved EXACTLY (back-compat with
    every restart call site that previously used the inline form).
  * On Windows + macOS callers get a ``ServiceControlError`` with the
    Phase 3 hint rather than an opaque ``FileNotFoundError`` from a
    missing ``systemctl`` binary or — worse — a silent no-op against
    some unrelated PATH binary.

We assert both branches by monkeypatching ``platform.system`` on the
helper module itself, plus the ``subprocess.Popen`` /
``asyncio.create_subprocess_exec`` call shape.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# Linux dispatch -------------------------------------------------------


def test_restart_service_linux_invokes_systemctl(monkeypatch):
    """On Linux, restart_service must call subprocess.Popen with the
    exact systemctl --user argv that pre-Phase-2.3 callers used."""
    from pipeline import service_control

    monkeypatch.setattr(service_control.platform, "system", lambda: "Linux")
    with patch("pipeline.service_control.subprocess.Popen") as mock_popen:
        service_control.restart_service("jarvis-voice-client")
        mock_popen.assert_called_once_with(
            ["systemctl", "--user", "restart", "jarvis-voice-client"],  # windows-footgun: ok (test asserts the Linux dispatch argv)
            stdout=service_control.subprocess.DEVNULL,
            stderr=service_control.subprocess.DEVNULL,
        )


def test_restart_service_async_linux_invokes_systemctl(monkeypatch):
    """Async variant must dispatch the same systemctl argv via
    asyncio.create_subprocess and return the child's returncode."""
    from pipeline import service_control

    fake_proc = MagicMock()
    fake_proc.wait = AsyncMock(return_value=None)
    fake_proc.returncode = 0

    seen = {"argv": None}

    async def fake_exec(*argv, **kw):
        seen["argv"] = argv
        return fake_proc

    monkeypatch.setattr(service_control.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        service_control.asyncio, "create_subprocess_exec", fake_exec
    )
    rc = asyncio.run(service_control.restart_service_async("jarvis-voice-agent"))
    assert rc == 0
    assert seen["argv"] == (
        "systemctl",
        "--user",
        "restart",
        "jarvis-voice-agent",
    )


def test_restart_service_async_returns_nonzero_when_systemctl_fails(monkeypatch):
    """A non-zero systemctl exit must propagate as the returncode."""
    from pipeline import service_control

    fake_proc = MagicMock()
    fake_proc.wait = AsyncMock(return_value=None)
    fake_proc.returncode = 5

    async def fake_exec(*argv, **kw):
        return fake_proc

    monkeypatch.setattr(service_control.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        service_control.asyncio, "create_subprocess_exec", fake_exec
    )
    rc = asyncio.run(service_control.restart_service_async("some-unit"))
    assert rc == 5


# Windows dispatch ----------------------------------------------------


def test_restart_service_windows_raises_phase3_error(monkeypatch):
    """On Windows the sync helper must raise ServiceControlError with a
    message that points to Phase 3 — callers can either catch + log
    (the watchdog pattern) or let it propagate."""
    from pipeline import service_control

    monkeypatch.setattr(service_control.platform, "system", lambda: "Windows")
    with pytest.raises(service_control.ServiceControlError) as exc:
        service_control.restart_service("jarvis-voice-client")
    assert "Windows" in str(exc.value) or "Phase 3" in str(exc.value)
    assert "jarvis-voice-client" in str(exc.value)


def test_restart_service_async_windows_raises_phase3_error(monkeypatch):
    """The async variant must raise the same ServiceControlError shape."""
    from pipeline import service_control

    monkeypatch.setattr(service_control.platform, "system", lambda: "Windows")
    with pytest.raises(service_control.ServiceControlError) as exc:
        asyncio.run(service_control.restart_service_async("jarvis-voice-agent"))
    assert "jarvis-voice-agent" in str(exc.value)


def test_restart_service_windows_does_not_call_subprocess(monkeypatch):
    """Confirm we DON'T silently fall through to subprocess on Windows —
    that's the failure mode the helper exists to prevent."""
    from pipeline import service_control

    monkeypatch.setattr(service_control.platform, "system", lambda: "Windows")
    with patch("pipeline.service_control.subprocess.Popen") as mock_popen:
        with pytest.raises(service_control.ServiceControlError):
            service_control.restart_service("any-unit")
        mock_popen.assert_not_called()


# macOS dispatch ------------------------------------------------------


def test_restart_service_macos_raises_phase3_error(monkeypatch):
    """macOS gets the same NotImplementedError-style behavior as Windows —
    we don't ship macOS service units yet either."""
    from pipeline import service_control

    monkeypatch.setattr(service_control.platform, "system", lambda: "Darwin")
    with pytest.raises(service_control.ServiceControlError):
        service_control.restart_service("jarvis-voice-client")


# Sequential helper ---------------------------------------------------


def test_restart_services_async_dispatches_each_unit(monkeypatch):
    """restart_services_async calls restart_service_async once per name,
    in order. Used by the model-swap path that restarts agent then client."""
    from pipeline import service_control

    calls: list[str] = []

    async def fake_restart(name: str) -> int:
        calls.append(name)
        return 0

    monkeypatch.setattr(
        service_control, "restart_service_async", fake_restart
    )
    asyncio.run(
        service_control.restart_services_async(
            ["jarvis-voice-agent", "jarvis-voice-client"],
            gap_seconds=0.0,
        )
    )
    assert calls == ["jarvis-voice-agent", "jarvis-voice-client"]


def test_restart_services_async_swallows_unsupported(monkeypatch, caplog):
    """If service control is unavailable on this platform, the sequential
    helper must log and continue rather than abort the chain — the model-
    swap caller is a fire-and-forget background task."""
    from pipeline import service_control

    async def fake_restart(name: str) -> int:
        raise service_control.ServiceControlError(f"unsupported for {name}")

    monkeypatch.setattr(
        service_control, "restart_service_async", fake_restart
    )
    # Should not raise.
    asyncio.run(
        service_control.restart_services_async(
            ["jarvis-voice-agent", "jarvis-voice-client"]
        )
    )

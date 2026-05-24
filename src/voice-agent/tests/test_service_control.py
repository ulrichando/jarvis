"""Tests for pipeline/service_control.py — Linux dispatch + cross-platform shape.

The helper replaces the previous inline
``subprocess.Popen(["systemctl", "--user", "restart", ...])`` + async
create_subprocess patterns with a single platform-aware function so:

  * On Linux the systemctl argv is preserved EXACTLY (back-compat with
    every restart call site that previously used the inline form).
  * On Windows the helper shells out to ``nssm`` (Phase 3.1 backend),
    locating ``nssm.exe`` under ``%LOCALAPPDATA%\\jarvis\\bin\\`` first
    (where install.ps1 will install it) or via PATH. macOS gets a
    ``ServiceControlError`` since we don't ship macOS units yet.
  * Callers that ran fine on Linux pre-Phase-2.3 see no behavior change.

Linux dispatch is asserted here. The Windows / nssm backend has its
own dedicated suite in ``test_service_control_windows.py`` so this
file stays focused on the Linux contract + the cross-platform helper
plumbing (sequential restart, macOS unsupported).
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


# Windows dispatch — see test_service_control_windows.py for the full
# nssm-backend coverage. This file only asserts the dispatch isn't
# silently falling through to systemctl on Windows.


def test_restart_service_windows_does_not_call_systemctl(monkeypatch):
    """Confirm we DON'T silently fall through to systemctl on Windows —
    that's the failure mode the helper exists to prevent. (The Windows
    branch may still raise ServiceControlError on this host because
    nssm.exe isn't installed; that's fine — what matters is that
    subprocess.Popen WITH systemctl argv wasn't called.)"""
    from pipeline import service_control

    monkeypatch.setattr(service_control.platform, "system", lambda: "Windows")
    # Force nssm-not-found so the Windows branch raises before reaching
    # any subprocess call — keeps this test hermetic on Linux hosts.
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(service_control.shutil, "which", lambda _: None)
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

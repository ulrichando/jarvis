"""Tests for tools.runtime path helpers — Linux + Windows parity.

The Phase 2.1 cross-platform footgun cleanup added
``get_jarvis_data_dir``, ``get_jarvis_log_dir``, and
``get_jarvis_models_dir`` alongside the pre-existing
``get_jarvis_home``. These tests lock in:

  * Linux behavior is unchanged (the ``~/.jarvis`` /
    ``~/.local/share/jarvis`` paths the rest of the codebase has always
    used).
  * Env var overrides round-trip (so tests + install profiles can
    isolate their state).
  * Every helper returns a ``pathlib.Path`` (not ``str``) so callers can
    use ``/`` joining without re-wrapping.
  * Windows-side paths resolve via ``%LOCALAPPDATA%`` /
    ``%USERPROFILE%`` when ``platform.system()`` is mocked to
    ``"Windows"``.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest


# ── Linux defaults ────────────────────────────────────────────────────


def test_get_jarvis_home_returns_path_under_real_home(monkeypatch):
    # The default branch produces <Path.home()>/.jarvis. We can't easily
    # remock Path.home for the module-level default (it's evaluated at
    # import) so the assertion is structural: the helper returns a Path
    # and its name is ".jarvis" when no env override is set.
    monkeypatch.delenv("JARVIS_HOME", raising=False)
    from tools import runtime
    result = runtime.get_jarvis_home()
    assert isinstance(result, Path)
    assert result.name == ".jarvis"
    assert result == Path.home() / ".jarvis"


def test_get_jarvis_data_dir_linux_default(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    with mock.patch("tools.runtime.platform.system", return_value="Linux"), \
         mock.patch("tools.runtime.Path.home", return_value=tmp_path):
        from tools import runtime
        result = runtime.get_jarvis_data_dir()
    assert isinstance(result, Path)
    assert result == tmp_path / ".local" / "share" / "jarvis"


def test_get_jarvis_log_dir_lives_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    with mock.patch("tools.runtime.platform.system", return_value="Linux"), \
         mock.patch("tools.runtime.Path.home", return_value=tmp_path):
        from tools import runtime
        log = runtime.get_jarvis_log_dir()
        data = runtime.get_jarvis_data_dir()
    assert isinstance(log, Path)
    assert log == data / "logs"


def test_get_jarvis_models_dir_under_install_root():
    # Models live next to the voice-agent source, NOT under per-user
    # state — so the path is the same on every platform and doesn't
    # care about env vars.
    from tools import runtime
    result = runtime.get_jarvis_models_dir()
    assert isinstance(result, Path)
    # runtime.py is at src/voice-agent/tools/runtime.py
    # → models should be src/voice-agent/models
    expected = Path(runtime.__file__).resolve().parent.parent / "models"
    assert result == expected


# ── Env var round-trips ───────────────────────────────────────────────


def test_jarvis_home_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "custom"))
    from tools import runtime
    result = runtime.get_jarvis_home()
    assert isinstance(result, Path)
    assert result == tmp_path / "custom"


def test_jarvis_data_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    from tools import runtime
    result = runtime.get_jarvis_data_dir()
    assert isinstance(result, Path)
    assert result == tmp_path / "data"


def test_jarvis_data_dir_xdg_override(monkeypatch, tmp_path):
    # XDG_DATA_HOME wins over the default ~/.local/share fallback.
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    with mock.patch("tools.runtime.platform.system", return_value="Linux"):
        from tools import runtime
        result = runtime.get_jarvis_data_dir()
    assert result == tmp_path / "xdg" / "jarvis"


# ── Windows-side paths (mocked) ───────────────────────────────────────


def test_get_jarvis_data_dir_windows_uses_localappdata(monkeypatch, tmp_path):
    """On Windows, data dir = %LOCALAPPDATA%\\jarvis\\data."""
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    with mock.patch("tools.runtime.platform.system", return_value="Windows"):
        from tools import runtime
        result = runtime.get_jarvis_data_dir()
    assert isinstance(result, Path)
    assert result == tmp_path / "AppData" / "Local" / "jarvis" / "data"


def test_get_jarvis_log_dir_windows_lives_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    with mock.patch("tools.runtime.platform.system", return_value="Windows"):
        from tools import runtime
        result = runtime.get_jarvis_log_dir()
    assert isinstance(result, Path)
    assert result == tmp_path / "AppData" / "Local" / "jarvis" / "data" / "logs"


def test_windows_localappdata_fallback_to_userprofile(monkeypatch, tmp_path):
    """If %LOCALAPPDATA% is unset on Windows, fall back to user profile."""
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with mock.patch("tools.runtime.platform.system", return_value="Windows"), \
         mock.patch("tools.runtime.Path.home", return_value=tmp_path / "user"):
        from tools import runtime
        result = runtime.get_jarvis_data_dir()
    # AppData/Local under the home dir is the documented Windows default.
    assert result == tmp_path / "user" / "AppData" / "Local" / "jarvis" / "data"


# ── Type / mkdir contract ────────────────────────────────────────────


def test_all_helpers_return_path():
    """Every helper must return pathlib.Path so callers can use `/` join."""
    from tools import runtime
    for fn in (
        runtime.get_jarvis_home,
        runtime.get_jarvis_data_dir,
        runtime.get_jarvis_log_dir,
        runtime.get_jarvis_models_dir,
    ):
        result = fn()
        assert isinstance(result, Path), f"{fn.__name__} returned {type(result).__name__}"


def test_helpers_create_directory_when_missing(monkeypatch, tmp_path):
    """The helpers mkdir(parents=True, exist_ok=True) so callers don't have to."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "d"))
    from tools import runtime
    h = runtime.get_jarvis_home()
    d = runtime.get_jarvis_data_dir()
    log = runtime.get_jarvis_log_dir()
    assert h.is_dir()
    assert d.is_dir()
    assert log.is_dir()

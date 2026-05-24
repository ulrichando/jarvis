"""Tests for the cross-platform audio probe dispatch layer (Phase 2.2).

Linux backends preserve the exact pw-dump + pactl behavior the old
aec_health.py and output_profile.py had inline; the existing tests for
those modules cover the Linux happy paths. The tests here verify the
PLATFORM DISPATCH: that the right backend runs for each platform.system()
value, and that non-Linux platforms degrade gracefully (no pw-dump call,
l1_echo_cancel_active always False).
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# --- Linux dispatch path ------------------------------------------------

def test_linux_default_input_uses_pw_dump(monkeypatch):
    """On Linux, default_input_source_name() must invoke pw-dump and
    parse the JSON for default.audio.source."""
    from audio import platform_audio

    fake_dump = [
        {
            "type": "PipeWire:Interface:Metadata",
            "metadata": [
                {"key": "default.audio.source", "value": {"name": "echo-cancel-source"}},
            ],
        }
    ]
    seen = {"argv": None}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        r = MagicMock()
        r.stdout = json.dumps(fake_dump)
        return r

    monkeypatch.setattr(platform_audio.platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform_audio.subprocess, "run", fake_run)
    assert platform_audio.default_input_source_name() == "echo-cancel-source"
    assert seen["argv"] == ["pw-dump"]  # windows-footgun: ok (test asserts the Linux dispatch path's argv)


def test_linux_l1_echo_cancel_active_true(monkeypatch):
    """On Linux, l1_echo_cancel_active() = True iff the default source
    name contains 'echo-cancel'."""
    from audio import platform_audio
    monkeypatch.setattr(platform_audio, "platform", types.SimpleNamespace(system=lambda: "Linux"))
    monkeypatch.setattr(platform_audio, "_linux_default_source",
                        lambda: "echo-cancel-source")
    assert platform_audio.l1_echo_cancel_active() is True


def test_linux_l1_echo_cancel_active_false_on_raw_mic(monkeypatch):
    from audio import platform_audio
    monkeypatch.setattr(platform_audio, "platform", types.SimpleNamespace(system=lambda: "Linux"))
    monkeypatch.setattr(platform_audio, "_linux_default_source",
                        lambda: "alsa_input.pci-0000_00_1f.3.analog-stereo")
    assert platform_audio.l1_echo_cancel_active() is False


def test_linux_default_output_uses_pwdump_then_pactl(monkeypatch):
    """On Linux, default_output_sink_name() must try pw-dump first and
    fall back to pactl on empty."""
    from audio import platform_audio
    monkeypatch.setattr(platform_audio, "platform", types.SimpleNamespace(system=lambda: "Linux"))
    monkeypatch.setattr(platform_audio, "_linux_pwdump_active_sink_desc",
                        lambda: "Built-in Audio Analog Stereo internal")
    # pactl should NOT be hit when pw-dump returns text.
    called_pactl = {"hit": False}

    def fake_pactl():
        called_pactl["hit"] = True
        return "should not be returned"

    monkeypatch.setattr(platform_audio, "_linux_pactl_active_sink_block", fake_pactl)
    out = platform_audio.default_output_sink_name()
    assert out == "Built-in Audio Analog Stereo internal"
    assert called_pactl["hit"] is False

    # Now pw-dump empty -> pactl should be consulted.
    monkeypatch.setattr(platform_audio, "_linux_pwdump_active_sink_desc", lambda: "")
    monkeypatch.setattr(platform_audio, "_linux_pactl_active_sink_block",
                        lambda: "pactl block text")
    assert platform_audio.default_output_sink_name() == "pactl block text"


# --- Windows dispatch path ---------------------------------------------

def _install_fake_sounddevice(monkeypatch, *, input_name="Mic (USB)", output_name="Speakers (USB)"):
    """Inject a stub `sounddevice` module so platform_audio's lazy import
    sees it without requiring a real audio device on the test box."""
    fake_sd = types.ModuleType("sounddevice")

    def query_devices(kind=None):
        if kind == "input":
            return {"name": input_name}
        if kind == "output":
            return {"name": output_name}
        return []

    fake_sd.query_devices = query_devices  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    return fake_sd


def test_windows_default_input_uses_sounddevice(monkeypatch):
    from audio import platform_audio
    monkeypatch.setattr(platform_audio, "platform", types.SimpleNamespace(system=lambda: "Windows"))
    _install_fake_sounddevice(monkeypatch, input_name="Microphone (Realtek)")
    assert platform_audio.default_input_source_name() == "Microphone (Realtek)"


def test_windows_default_output_uses_sounddevice(monkeypatch):
    from audio import platform_audio
    monkeypatch.setattr(platform_audio, "platform", types.SimpleNamespace(system=lambda: "Windows"))
    _install_fake_sounddevice(monkeypatch, output_name="Speakers (Realtek)")
    assert platform_audio.default_output_sink_name() == "Speakers (Realtek)"


def test_windows_l1_echo_cancel_always_false(monkeypatch):
    """No WASAPI equivalent of PipeWire's echo-cancel virtual source —
    even if the device name literally contained 'echo-cancel', the
    Windows path must report False so the cascade falls through to
    L2 APM + L3 DTLN."""
    from audio import platform_audio
    monkeypatch.setattr(platform_audio, "platform", types.SimpleNamespace(system=lambda: "Windows"))
    _install_fake_sounddevice(monkeypatch, input_name="echo-cancel-source (fake)")
    assert platform_audio.l1_echo_cancel_active() is False


def test_windows_default_input_empty_when_sounddevice_missing(monkeypatch):
    """If sounddevice import fails, default_input_source_name() must
    return '' rather than raising."""
    from audio import platform_audio
    monkeypatch.setattr(platform_audio, "platform", types.SimpleNamespace(system=lambda: "Windows"))
    # Block the import by inserting a sentinel that raises on attribute access.
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    assert platform_audio.default_input_source_name() == ""
    assert platform_audio.default_output_sink_name() == ""


def test_windows_default_input_empty_when_query_raises(monkeypatch):
    from audio import platform_audio
    monkeypatch.setattr(platform_audio, "platform", types.SimpleNamespace(system=lambda: "Windows"))
    fake_sd = types.ModuleType("sounddevice")

    def query_devices(kind=None):
        raise OSError("no default device")

    fake_sd.query_devices = query_devices  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    assert platform_audio.default_input_source_name() == ""


# --- macOS dispatch path -----------------------------------------------

def test_macos_default_input_uses_sounddevice(monkeypatch):
    from audio import platform_audio
    monkeypatch.setattr(platform_audio, "platform", types.SimpleNamespace(system=lambda: "Darwin"))
    _install_fake_sounddevice(monkeypatch, input_name="MacBook Pro Microphone")
    assert platform_audio.default_input_source_name() == "MacBook Pro Microphone"


def test_macos_default_output_uses_sounddevice(monkeypatch):
    from audio import platform_audio
    monkeypatch.setattr(platform_audio, "platform", types.SimpleNamespace(system=lambda: "Darwin"))
    _install_fake_sounddevice(monkeypatch, output_name="MacBook Pro Speakers")
    assert platform_audio.default_output_sink_name() == "MacBook Pro Speakers"


def test_macos_l1_echo_cancel_always_false(monkeypatch):
    """CoreAudio has no PipeWire-equivalent OS-level AEC; cascade falls
    through to L2 APM + L3 DTLN."""
    from audio import platform_audio
    monkeypatch.setattr(platform_audio, "platform", types.SimpleNamespace(system=lambda: "Darwin"))
    _install_fake_sounddevice(monkeypatch)
    assert platform_audio.l1_echo_cancel_active() is False


# --- Unknown platform graceful degrade ---------------------------------

def test_unknown_platform_returns_empty(monkeypatch):
    """An unrecognized platform.system() must NOT crash — return empty
    strings and l1_echo_cancel_active() False so callers can degrade."""
    from audio import platform_audio
    monkeypatch.setattr(platform_audio, "platform", types.SimpleNamespace(system=lambda: "Plan9"))
    # No sounddevice install — the unknown path skips the import entirely.
    assert platform_audio.default_input_source_name() == ""
    assert platform_audio.default_output_sink_name() == ""
    assert platform_audio.l1_echo_cancel_active() is False


# --- Sanity: existing aec_health.l1 still routes via the shim ----------

def test_aec_health_l1_delegates_to_platform_dispatch(monkeypatch):
    """`aec_health.l1_echo_cancel_active` must reflect the same Linux
    decision the platform layer makes — verified by mocking the LIFTED
    source-name probe in platform_audio and confirming aec_health sees it.

    The aec_health module exposes `_default_source_name` as a back-compat
    shim around `platform_audio._linux_default_source`; patching the
    shim (the documented seam used by test_aec_health.py) must keep
    working."""
    from audio import aec_health
    monkeypatch.setenv("JARVIS_PIPEWIRE_AEC", "1")
    monkeypatch.setattr(aec_health, "_default_source_name", lambda: "echo-cancel-source")
    aec_health._l1_cache_clear()
    assert aec_health.l1_echo_cancel_active() is True

    monkeypatch.setattr(aec_health, "_default_source_name",
                        lambda: "alsa_input.pci-0000_00_1f.3.analog-stereo")
    aec_health._l1_cache_clear()
    assert aec_health.l1_echo_cancel_active() is False

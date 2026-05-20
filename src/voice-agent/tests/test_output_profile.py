"""Output-device profile classification for AEC strategy gating."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Sample `pactl list sinks` fragments for the active sink.
_HEADSET_SINK = '''Sink #1
	State: RUNNING
	Name: bluez_output.AA_BB.1
	Active Port: headset-output
	Ports:
		headset-output: Headset (type: Headset, priority: 100)
	Properties:
		device.form_factor = "headset"
'''
_SPEAKER_SINK = '''Sink #0
	State: RUNNING
	Name: alsa_output.pci-0000_00_1f.3.analog-stereo
	Active Port: analog-output-speaker
	Ports:
		analog-output-speaker: Speaker (type: Speaker, priority: 100)
	Properties:
		device.form_factor = "internal"
'''


def test_classify_headset(monkeypatch):
    from audio import output_profile
    monkeypatch.setattr(output_profile, "_pwdump_active_sink_desc", lambda: "")
    monkeypatch.setattr(output_profile, "_active_sink_block", lambda: _HEADSET_SINK)
    output_profile.classify_output_device.cache_clear()
    assert output_profile.classify_output_device() == "headphones"


def test_classify_speaker(monkeypatch):
    from audio import output_profile
    monkeypatch.setattr(output_profile, "_pwdump_active_sink_desc", lambda: "")
    monkeypatch.setattr(output_profile, "_active_sink_block", lambda: _SPEAKER_SINK)
    output_profile.classify_output_device.cache_clear()
    assert output_profile.classify_output_device() == "speakers"


def test_classify_unknown_on_empty(monkeypatch):
    from audio import output_profile
    monkeypatch.setattr(output_profile, "_pwdump_active_sink_desc", lambda: "")
    monkeypatch.setattr(output_profile, "_active_sink_block", lambda: "")
    output_profile.classify_output_device.cache_clear()
    assert output_profile.classify_output_device() == "unknown"


def test_force_profile_override(monkeypatch):
    from audio import output_profile
    monkeypatch.setenv("JARVIS_AEC_FORCE_PROFILE", "headphones")
    monkeypatch.setattr(output_profile, "_pwdump_active_sink_desc", lambda: "")
    monkeypatch.setattr(output_profile, "_active_sink_block", lambda: _SPEAKER_SINK)
    output_profile.classify_output_device.cache_clear()
    assert output_profile.classify_output_device() == "headphones"


# --- pw-dump backend (PipeWire-native primary; this box has no pactl) ---

def test_pwdump_speaker(monkeypatch):
    from audio import output_profile
    monkeypatch.setattr(output_profile, "_pwdump_active_sink_desc",
                        lambda: "Built-in Audio Analog Stereo")
    monkeypatch.setattr(output_profile, "_active_sink_block", lambda: "")  # pactl empty
    output_profile.classify_output_device.cache_clear()
    assert output_profile.classify_output_device() == "speakers"


def test_pwdump_headset(monkeypatch):
    from audio import output_profile
    monkeypatch.setattr(output_profile, "_pwdump_active_sink_desc",
                        lambda: "Jabra Elite headset")
    monkeypatch.setattr(output_profile, "_active_sink_block", lambda: "")
    output_profile.classify_output_device.cache_clear()
    assert output_profile.classify_output_device() == "headphones"


def test_pwdump_takes_priority_over_pactl(monkeypatch):
    """pw-dump (PipeWire-native) is tried first; pactl is the fallback."""
    from audio import output_profile
    monkeypatch.setattr(output_profile, "_pwdump_active_sink_desc",
                        lambda: "Built-in Audio Analog Stereo")
    # pactl would say unknown (empty); pw-dump wins.
    monkeypatch.setattr(output_profile, "_active_sink_block", lambda: "")
    output_profile.classify_output_device.cache_clear()
    assert output_profile.classify_output_device() == "speakers"

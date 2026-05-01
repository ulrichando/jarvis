"""Tests for AcousticTap — the per-turn acoustic prosody collector.

We don't try to spin up a real LiveKit room; the audio-stream consume
path requires an `rtc.AudioStream(track)` and a webrtc track, which
aren't testable without infrastructure. Instead we exercise the
public surface that the agent actually queries: the rolling buffer
semantics of `mean_rms_db(start, end)` and the per-frame RMS-dB
computation logic by feeding samples directly into the deque.
"""
import math
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from acoustic_tap import AcousticTap, _RmsSample, _DB_FLOOR


def test_empty_tap_returns_zero():
    """A fresh tap with no samples yet returns 0.0 — the agent
    treats this as 'unknown' and skips the RMS branch."""
    tap = AcousticTap()
    assert tap.mean_rms_db(0.0, 1000.0) == 0.0


def test_invalid_window_returns_zero():
    """end <= start is a programming bug; return 0.0 rather than
    surfacing weird arithmetic."""
    tap = AcousticTap()
    tap._samples.append(_RmsSample(timestamp=100.0, rms_db=-30.0))
    assert tap.mean_rms_db(100.0, 100.0) == 0.0  # zero-width window
    assert tap.mean_rms_db(200.0, 100.0) == 0.0  # reversed window


def test_mean_rms_db_filters_to_window():
    """Samples outside [start, end] are excluded from the mean."""
    tap = AcousticTap()
    # Three samples: one before window, two inside, one after.
    tap._samples.append(_RmsSample(timestamp=50.0,  rms_db=-50.0))
    tap._samples.append(_RmsSample(timestamp=110.0, rms_db=-30.0))
    tap._samples.append(_RmsSample(timestamp=120.0, rms_db=-20.0))
    tap._samples.append(_RmsSample(timestamp=200.0, rms_db=-10.0))
    assert tap.mean_rms_db(100.0, 150.0) == pytest.approx(-25.0)


def test_mean_rms_db_no_samples_in_window():
    """Window with no samples in range returns 0.0."""
    tap = AcousticTap()
    tap._samples.append(_RmsSample(timestamp=50.0,  rms_db=-30.0))
    tap._samples.append(_RmsSample(timestamp=200.0, rms_db=-30.0))
    assert tap.mean_rms_db(100.0, 150.0) == 0.0


def test_mean_rms_db_inclusive_endpoints():
    """Boundary samples are included in the window."""
    tap = AcousticTap()
    tap._samples.append(_RmsSample(timestamp=100.0, rms_db=-30.0))
    tap._samples.append(_RmsSample(timestamp=150.0, rms_db=-20.0))
    assert tap.mean_rms_db(100.0, 150.0) == pytest.approx(-25.0)


def test_buffer_caps_oldest_eviction():
    """The deque has a fixed maxlen — older samples drop off when
    the buffer fills. This prevents unbounded memory growth on a
    long-running session."""
    from acoustic_tap import _BUFFER_MAXLEN
    tap = AcousticTap()
    # Push more than maxlen
    for i in range(_BUFFER_MAXLEN + 50):
        tap._samples.append(_RmsSample(timestamp=float(i), rms_db=-30.0))
    assert len(tap._samples) == _BUFFER_MAXLEN
    # The oldest 50 should have been evicted; first sample now starts at i=50.
    assert tap._samples[0].timestamp == 50.0


def test_db_floor_applied_to_silence():
    """Verify the silence-floor convention: log10(0)+1e-10 should
    clamp to _DB_FLOOR (-80 dB), not -inf. This mirrors the
    consume() path's RMS computation so a long silent stretch
    doesn't produce -inf samples that poison the mean."""
    # Manually compute what consume() would produce for an all-zero frame.
    rms = 0.0
    rms_db = max(_DB_FLOOR, 20.0 * math.log10(rms + 1e-10))
    assert rms_db == _DB_FLOOR


def test_db_conversion_typical_loud_speech():
    """RMS amplitude 0.1 (rough loud speech, -20 dBFS) → about -20 dB."""
    rms = 0.1
    rms_db = 20.0 * math.log10(rms + 1e-10)
    assert -21.0 < rms_db < -19.0


def test_db_conversion_typical_quiet_speech():
    """RMS amplitude 0.02 (quiet speech, -34 dBFS) → about -34 dB."""
    rms = 0.02
    rms_db = 20.0 * math.log10(rms + 1e-10)
    assert -35.0 < rms_db < -33.0


def test_shutdown_is_safe_when_never_attached():
    """Calling shutdown() on a tap that never attached must not raise.
    Happens during voice-only sessions where the user never publishes
    a mic, or during the brief window between session creation and
    track_subscribed firing."""
    tap = AcousticTap()
    tap.shutdown()  # Should be a no-op
    tap.shutdown()  # And idempotent

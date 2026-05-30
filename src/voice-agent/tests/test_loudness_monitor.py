import struct
import time

from animators import loudness_monitor as lm


def _pcm(amplitude: int, n: int = 512) -> bytes:
    """n signed-16 mono samples at a constant amplitude."""
    return struct.pack(f"<{n}h", *([amplitude] * n))


def test_rms_level_silence_is_zero():
    assert lm.rms_level(_pcm(0), gain=1.0, floor=0.0) == 0.0
    assert lm.rms_level(b"", gain=1.0, floor=0.0) == 0.0


def test_rms_level_full_scale_clamps_to_one():
    # rms of a constant 32767 sample is 32767/32768 ~= 0.99997; with gain 2
    # that exceeds 1.0 and must clamp to exactly 1.0.
    assert lm.rms_level(_pcm(32767), gain=2.0, floor=0.0) == 1.0
    # and a gain of 1 lands just under 1.0 (no clamp), proving the ceiling
    # isn't applied prematurely.
    assert lm.rms_level(_pcm(32767), gain=1.0, floor=0.0) > 0.999


def test_rms_level_floor_subtracts():
    # tiny signal below floor -> 0
    assert lm.rms_level(_pcm(50), gain=1.0, floor=0.5) == 0.0


def test_monitor_reports_level_from_injected_frames():
    frames = [_pcm(20000), _pcm(20000), _pcm(20000)]
    it = iter(frames)

    def source():
        try:
            return next(it)
        except StopIteration:
            return b""  # end-of-stream

    mon = lm.LoudnessMonitor(frame_source=source, gain=2.0, floor=0.0,
                             ema=1.0)  # ema=1 -> no smoothing, instant
    mon.start()
    time.sleep(0.05)
    assert mon.level() > 0.5
    mon.stop()


def test_monitor_degrades_when_source_unavailable():
    def source():
        raise RuntimeError("no pipewire here")

    mon = lm.LoudnessMonitor(frame_source=source, degraded_level=0.7)
    mon.start()
    time.sleep(0.05)
    # falls back to the degraded constant so the jaw still moves
    assert mon.level() == 0.7
    mon.stop()

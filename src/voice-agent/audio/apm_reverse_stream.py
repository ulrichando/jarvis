"""APM reverse-stream wiring: delay estimator + reference ring buffer.

The LiveKit APM's echo canceller (and the DTLN residual) need (a) the
playback reference fed via process_reverse_stream and (b) an accurate
stream-delay estimate. This module ports LiveKit's internal estimator
pattern (.venv/.../livekit/rtc/media_devices.py:478-510) and adds a
thread-safe ring buffer for the 16 kHz reference DTLN consumes.

Spec: docs/superpowers/specs/2026-05-19-echo-cancellation-cascade-design.md §5.2
"""
from __future__ import annotations

import os
import threading
from collections import deque
from typing import Optional

import numpy as np
from scipy.signal import firwin, resample_poly

# Anti-alias FIR for the fixed 48k→16k decimation, designed ONCE. With the
# default tuple window, resample_poly designs a fresh Kaiser FIR on EVERY
# call — at 100 frames/s on the playback path that filter design dominated
# the whole voice-client (74% CPU pinned on the event loop, 2026-06-11
# silence outage). These taps replicate scipy's internal design for
# (up=1, down=3, window=('kaiser', 5.0)): half_len = 10*max_rate = 30,
# cutoff = 1/max_rate — verified numerically identical (max diff ~3e-7).
_DECIM_48K_TO_16K_TAPS = firwin(2 * 30 + 1, 1.0 / 3.0, window=("kaiser", 5.0))


class APMDelayEstimator:
    """Tracks output(DAC) vs input(ADC) timestamps to estimate the
    round-trip stream delay for apm.set_stream_delay_ms(). Clamped to
    [0, 500] ms. JARVIS_APM_DELAY_BIAS_MS adds a manual offset."""

    def __init__(self, window: int = 50) -> None:
        self._out_t: Optional[float] = None
        self._samples: deque[float] = deque(maxlen=window)
        # `_out_t` is written by the playback thread (note_output) and read
        # by the mic thread (note_input); guard the read-then-use under a
        # lock so the read can't race a concurrent write to None/float.
        # `_samples` is a CPython deque — append is atomic — so the lock
        # only needs to cover `_out_t`. Spec 2026-05-19 §5.2 (T7 review #3).
        self._lock = threading.Lock()

    def note_output(self, dac_time: float) -> None:
        with self._lock:
            self._out_t = dac_time

    def note_input(self, adc_time: float) -> None:
        with self._lock:
            out_t = self._out_t
        if out_t is None:
            return
        delay_ms = (adc_time - out_t) * 1000.0
        self._samples.append(delay_ms)

    def current_delay_ms(self) -> int:
        if not self._samples:
            return 0
        # Median is robust to per-frame jitter.
        med = float(np.median(np.array(self._samples)))
        try:
            bias = float(os.environ.get("JARVIS_APM_DELAY_BIAS_MS", "0"))
        except ValueError:
            bias = 0.0
        val = med + bias
        if val < 0:
            return 0
        if val > 500:
            return 500
        return int(round(val))


class ReverseRefRingBuffer:
    """Thread-safe ring of 16 kHz reference frames. Writer (OutputStream
    callback, 48 kHz) downsamples to 16 kHz on write; reader (InputStream
    callback) returns the most-recent aligned 160-sample frame.

    Single lock; hold time is one slice copy (microseconds). The 48k→16k
    downsample happens off the mic-latency path (in the playback thread)."""

    def __init__(self, capacity_frames: int = 64) -> None:
        self._cap = capacity_frames
        self._buf: list[tuple[float, np.ndarray]] = []  # (dac_ts, 16k frame)
        self._lock = threading.Lock()

    def write(self, frame_48k: np.ndarray, dac_ts: float) -> None:
        # 48k → 16k (decimate by 3). 480 → 160 samples. Cached taps — see
        # _DECIM_48K_TO_16K_TAPS above; never pass a tuple window here.
        f16 = resample_poly(
            frame_48k.astype(np.float32), up=1, down=3, window=_DECIM_48K_TO_16K_TAPS
        ).astype(np.float32)
        with self._lock:
            self._buf.append((dac_ts, f16))
            if len(self._buf) > self._cap:
                self._buf.pop(0)

    def read_16k_aligned(self, input_ts: float) -> np.ndarray:
        """Return the most-recent reference frame at or before input_ts.
        Zeros if the buffer is empty (no playback → no echo to subtract)."""
        with self._lock:
            if not self._buf:
                return np.zeros(160, dtype=np.float32)
            # Most recent frame whose dac_ts <= input_ts; fall back to newest.
            chosen = self._buf[-1][1]
            for ts, f in reversed(self._buf):
                if ts <= input_ts:
                    chosen = f
                    break
            return chosen.copy()

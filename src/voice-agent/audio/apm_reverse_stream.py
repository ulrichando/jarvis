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


class APMDelayEstimator:
    """Tracks output(DAC) vs input(ADC) timestamps to estimate the
    round-trip stream delay for apm.set_stream_delay_ms(). Clamped to
    [0, 500] ms. JARVIS_APM_DELAY_BIAS_MS adds a manual offset."""

    def __init__(self, window: int = 50) -> None:
        self._out_t: Optional[float] = None
        self._samples: deque[float] = deque(maxlen=window)

    def note_output(self, dac_time: float) -> None:
        self._out_t = dac_time

    def note_input(self, adc_time: float) -> None:
        if self._out_t is None:
            return
        delay_ms = (adc_time - self._out_t) * 1000.0
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

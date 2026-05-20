"""APM stream-delay estimator (drives apm.set_stream_delay_ms)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_delay_from_dac_adc_pair():
    from audio.apm_reverse_stream import APMDelayEstimator
    est = APMDelayEstimator()
    # output played at t=1.000 (DAC), input captured at t=1.040 (ADC)
    est.note_output(1.000)
    est.note_input(1.040)
    # delay ≈ 40ms
    assert 30 <= est.current_delay_ms() <= 50


def test_delay_clamped_to_range(monkeypatch):
    from audio.apm_reverse_stream import APMDelayEstimator
    est = APMDelayEstimator()
    est.note_output(0.0)
    est.note_input(10.0)   # absurd 10s skew
    assert est.current_delay_ms() == 500   # clamped


def test_delay_bias_applied(monkeypatch):
    monkeypatch.setenv("JARVIS_APM_DELAY_BIAS_MS", "15")
    from audio.apm_reverse_stream import APMDelayEstimator
    est = APMDelayEstimator()
    est.note_output(1.000)
    est.note_input(1.020)   # 20ms + 15 bias = 35
    assert 30 <= est.current_delay_ms() <= 40


def test_delay_zero_before_any_data():
    from audio.apm_reverse_stream import APMDelayEstimator
    est = APMDelayEstimator()
    assert est.current_delay_ms() == 0

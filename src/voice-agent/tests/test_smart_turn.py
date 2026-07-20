"""Smart Turn v3 end-of-turn detector (voice-setup todo #2).

Covers the log-mel front-end shape, the rolling audio buffer, the _TurnDetector
protocol (threshold env-tunability, fail-safe on error/timeout, sigmoid mapping),
and the disabled-by-default factory. The heavy ONNX inference is mocked except
one opt-in integration test that runs the REAL model if it's present.
"""
import asyncio
import os

import numpy as np
import pytest

from pipeline import smart_turn as st
from pipeline._whisper_features import compute_whisper_log_mel_features


# ---- log-mel front-end -------------------------------------------------------

def test_logmel_shape_is_80x800_for_any_length():
    for secs in (0.3, 1.0, 8.0, 12.0):  # short (pad), exact, long (truncate)
        audio = np.random.randn(int(16000 * secs)).astype(np.float32) * 0.1
        feats = compute_whisper_log_mel_features(audio)
        assert feats.shape == (80, 800), secs
        assert feats.dtype == np.float32


def test_logmel_rejects_non_1d():
    with pytest.raises(ValueError):
        compute_whisper_log_mel_features(np.zeros((2, 100), dtype=np.float32))


# ---- rolling audio buffer ----------------------------------------------------

class _Frame:
    def __init__(self, samples: np.ndarray, sample_rate: int = 16000):
        self.data = samples.astype(np.int16).tobytes()
        self.sample_rate = sample_rate


def test_buffer_rolls_to_last_8s():
    buf = st.EOUAudioBuffer()
    # feed 10 s in 1 s frames — buffer must cap at 8 s (128000 samples)
    for _ in range(10):
        buf.feed_frame(_Frame(np.full(16000, 1000)))
    snap = buf.snapshot()
    assert snap.size <= st._MAX_SAMPLES
    assert snap.size == st._MAX_SAMPLES  # exactly 8 s after overflow
    assert snap.dtype == np.float32 and abs(snap).max() <= 1.0


def test_buffer_empty_snapshot_and_wrong_samplerate_skipped():
    buf = st.EOUAudioBuffer()
    assert buf.snapshot().size == 0
    buf.feed_frame(_Frame(np.full(16000, 1000), sample_rate=48000))  # skipped
    assert buf.snapshot().size == 0
    buf.feed_frame(_Frame(np.full(8000, 500)))
    assert buf.snapshot().size == 8000
    buf.reset()
    assert buf.snapshot().size == 0


# ---- detector protocol -------------------------------------------------------

class _StubSession:
    """Mock onnx session returning a fixed logit."""
    def __init__(self, logit=None, raises=False, slow=False):
        self._logit, self._raises, self._slow = logit, raises, slow

    def run(self, names, feed):
        if self._raises:
            raise RuntimeError("onnx boom")
        if self._slow:
            import time
            time.sleep(0.5)
        return [np.array([[self._logit]], dtype=np.float32)]


def _det(**kw):
    buf = st.EOUAudioBuffer()
    buf.feed_frame(_Frame(np.full(16000, 2000)))  # 1 s of audio
    return st.SmartTurnEOU(buffer=buf, onnx_session=_StubSession(**kw))


def test_predict_sigmoid_mapping():
    # logit 0 -> 0.5 ; large positive -> ~1 ; large negative -> ~0
    assert abs(asyncio.run(_det(logit=0.0).predict_end_of_turn(None)) - 0.5) < 1e-6
    assert asyncio.run(_det(logit=10.0).predict_end_of_turn(None)) > 0.99
    assert asyncio.run(_det(logit=-10.0).predict_end_of_turn(None)) < 0.01


def test_predict_failsafe_on_error_returns_finalize():
    assert asyncio.run(_det(raises=True).predict_end_of_turn(None)) == 1.0


def test_predict_failsafe_on_timeout_returns_finalize():
    d = _det(slow=True)
    assert asyncio.run(d.predict_end_of_turn(None, timeout=0.05)) == 1.0


def test_predict_empty_buffer_returns_finalize():
    d = st.SmartTurnEOU(buffer=st.EOUAudioBuffer(), onnx_session=_StubSession(logit=-10.0))
    assert asyncio.run(d.predict_end_of_turn(None)) == 1.0  # nothing captured


def test_unlikely_threshold_env_tunable(monkeypatch):
    d = _det(logit=0.0)
    assert asyncio.run(d.unlikely_threshold("en")) == 0.5
    monkeypatch.setenv("JARVIS_SMART_TURN_THRESHOLD", "0.7")
    assert asyncio.run(d.unlikely_threshold("en")) == 0.7
    monkeypatch.setenv("JARVIS_SMART_TURN_THRESHOLD", "garbage")
    assert asyncio.run(d.unlikely_threshold("en")) == 0.5  # falls back


def test_protocol_properties():
    d = _det(logit=0.0)
    assert d.model and d.provider
    assert asyncio.run(d.supports_language("en")) is True


# ---- factory gating ----------------------------------------------------------

def test_factory_disabled_by_default(monkeypatch):
    monkeypatch.delenv("JARVIS_SMART_TURN", raising=False)
    assert st.build_smart_turn_detector() is None


def test_factory_enabled_but_model_missing_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_SMART_TURN", "1")
    monkeypatch.setenv("JARVIS_SMART_TURN_MODEL", str(tmp_path / "nope.onnx"))
    assert st.build_smart_turn_detector() is None


# ---- real model integration (opt-in; skipped if the .onnx isn't present) -----

_MODEL = os.path.expanduser("~/.jarvis/models/smart-turn-v3.2-cpu.onnx")


@pytest.mark.skipif(not os.path.exists(_MODEL), reason="smart-turn onnx not present")
def test_real_model_end_to_end(monkeypatch):
    monkeypatch.setenv("JARVIS_SMART_TURN", "1")
    monkeypatch.setenv("JARVIS_SMART_TURN_MODEL", _MODEL)
    det = st.build_smart_turn_detector()
    assert det is not None
    det.buffer.feed_frame(_Frame((np.random.randn(16000 * 2) * 3000).astype(np.int16)))
    prob = asyncio.run(det.predict_end_of_turn(None))
    assert 0.0 <= prob <= 1.0  # real log-mel -> onnx -> sigmoid chain works

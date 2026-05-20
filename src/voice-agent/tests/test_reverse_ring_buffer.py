"""Thread-safe reference ring buffer (48k write → 16k aligned read)."""
import sys
import threading
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))


def _frame_48k(value: float, n: int = 480) -> np.ndarray:
    return np.full(n, value, dtype=np.float32)


def test_write_then_read_returns_downsampled():
    from audio.apm_reverse_stream import ReverseRefRingBuffer
    rb = ReverseRefRingBuffer(capacity_frames=10)
    rb.write(_frame_48k(0.5), dac_ts=1.0)
    out = rb.read_16k_aligned(input_ts=1.0)
    assert out.shape[0] == 160        # 10ms @ 16kHz
    assert out.dtype == np.float32


def test_empty_read_returns_zeros():
    from audio.apm_reverse_stream import ReverseRefRingBuffer
    rb = ReverseRefRingBuffer(capacity_frames=10)
    out = rb.read_16k_aligned(input_ts=5.0)
    assert out.shape[0] == 160
    assert np.allclose(out, 0.0)


def test_concurrent_write_read_no_crash():
    from audio.apm_reverse_stream import ReverseRefRingBuffer
    rb = ReverseRefRingBuffer(capacity_frames=64)
    stop = threading.Event()

    def writer():
        t = 0.0
        while not stop.is_set():
            rb.write(_frame_48k(0.1), dac_ts=t)
            t += 0.01

    def reader():
        t = 0.0
        while not stop.is_set():
            rb.read_16k_aligned(input_ts=t)
            t += 0.01

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for th in threads:
        th.start()
    threading.Event().wait(0.5)
    stop.set()
    for th in threads:
        th.join(timeout=2)
        assert not th.is_alive()

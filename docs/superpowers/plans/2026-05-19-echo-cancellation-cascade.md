# Echo-Cancellation Cascade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace JARVIS's "drop mic while speaking" workaround with an enterprise-grade 3-layer cascaded AEC (PipeWire AEC3 + LiveKit APM reverse-stream + DTLN neural residual) that restores barge-in, stops the echo-loop, and auto-adapts to headphones vs speakers.

**Architecture:** L1 = PipeWire `module-echo-cancel` (WebRTC AEC3, primary linear, tuned via install.sh). L2 = LiveKit APM NS/AGC/HPF with `process_reverse_stream` wired from the playback OutputStream + a delay estimator (AEC itself A/B-toggleable, default off — single primary linear AEC). L3 = DTLN-aec ONNX neural residual at 16 kHz, speakers-only via runtime output-device auto-detect. Cross-process telemetry bridges the voice-client (where AEC runs) to the agent (where turns are logged) via a stale-guarded `~/.jarvis/aec-state.json`.

**Tech Stack:** Python 3.13 (voice-agent venv `src/voice-agent/.venv/`), LiveKit Agents 1.5.9 (`rtc.apm.AudioProcessingModule`), sounddevice/PortAudio, numpy 2.4 + scipy 1.17 (resample + RIR fixtures), onnxruntime 1.26 (DTLN), PipeWire `module-echo-cancel`, pytest with synthetic-audio fixtures.

**Spec:** [`docs/superpowers/specs/2026-05-19-echo-cancellation-cascade-design.md`](../specs/2026-05-19-echo-cancellation-cascade-design.md) — read §4 (architecture + §4.1 single-linear-AEC rationale), §5 (components), §6 (data flow), §7 (error handling), §8 (acceptance).

**Env vars:** `JARVIS_PIPEWIRE_AEC` (1), `JARVIS_APM_AEC` (0, A/B toggle), `JARVIS_NEURAL_AEC` (1), `JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS` (8), `JARVIS_AEC_FORCE_PROFILE` (unset), `JARVIS_APM_DELAY_BIAS_MS` (0).

**Rollout:** Phase 1 = Tasks 1-8 (L1+L2 wiring, auto-detect, telemetry; restores barge-in, independently shippable). Phase 2 = Tasks 9-11 (L3 neural, A/B-gated).

---

## File Structure

### Files created
| Path | Responsibility |
|---|---|
| `src/voice-agent/audio/__init__.py` | New package marker. |
| `src/voice-agent/audio/output_profile.py` | Classify output device (headphones/speakers/unknown) via pactl; `pw-mon` change watcher; 30s cache; force-override. |
| `src/voice-agent/audio/apm_reverse_stream.py` | `APMDelayEstimator` + `ReverseRefRingBuffer` (thread-safe, 48k→16k downsample on write). |
| `src/voice-agent/audio/aec_state.py` | Cross-process AEC-state writer (voice-client) + reader (agent), atomic + stale-guarded. |
| `src/voice-agent/audio/dtln_aec.py` | `DTLNResidualFilter` — onnxruntime 16 kHz residual filter with latency-budget self-disable. (Phase 2) |
| `bin/jarvis-aec-reload` | Idempotent PipeWire `module-echo-cancel` reload with tuned args. |
| `bin/jarvis-aec-soak` | Observability rollup + echo-loop detector + HARD-FAIL gates. |
| `src/voice-agent/tests/test_output_profile.py` | L-detect unit tests. |
| `src/voice-agent/tests/test_apm_delay_estimator.py` | Delay estimator unit tests. |
| `src/voice-agent/tests/test_reverse_ring_buffer.py` | Ring buffer thread-safety + alignment tests. |
| `src/voice-agent/tests/test_aec_state_file.py` | Cross-process state round-trip + staleness tests. |
| `src/voice-agent/tests/test_aec_telemetry.py` | Telemetry migration + writer tests. |
| `src/voice-agent/tests/test_dtln_residual.py` | DTLN ERLE + latency + self-disable tests. (Phase 2) |

### Files modified
| Path | Change |
|---|---|
| `src/voice-agent/pipeline/turn_telemetry.py` | Online migration: 6 AEC columns on `turns`; `log_turn` kwargs. |
| `src/voice-agent/jarvis_voice_client.py` | Wire reverse-stream + delay estimator into `play_subscribed_track` OutputStream; remove mic-drop on speakers in `_mic_cb`; write aec-state.json; (Phase 2) wire L3 + 16k publish. |
| `src/voice-agent/jarvis_agent.py` | Read aec-state.json at turn-write time; pass AEC kwargs to `log_turn`. |
| `install.sh` | PipeWire AEC tuning; (Phase 2) DTLN model download + hash verify. |

---

## PHASE 1 — L1 + L2 wiring (restores barge-in)

## Task 1: AEC telemetry columns + writer kwargs

**Files:**
- Modify: `src/voice-agent/pipeline/turn_telemetry.py`
- Test: `src/voice-agent/tests/test_aec_telemetry.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_aec_telemetry.py`:

```python
"""AEC telemetry columns on the turns table (2026-05-19 echo-cancel cascade)."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.turn_telemetry import init_db, log_turn


def test_migration_adds_aec_columns(tmp_path):
    db = tmp_path / "tele.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(turns)")}
    for c in ("aec_layer1_active", "aec_layer2_aec_active", "aec_layer3_active",
              "output_profile", "apm_delay_ms_p50", "dtln_latency_ms_p95"):
        assert c in cols, f"missing column {c}"


def test_log_turn_persists_aec_fields(tmp_path):
    db = tmp_path / "tele.db"
    init_db(db)
    log_turn(
        db_path=db, user_text="hi", jarvis_text="Yes?", route="BANTER",
        aec_layer1_active=1, aec_layer2_aec_active=0, aec_layer3_active=1,
        output_profile="speakers", apm_delay_ms_p50=42, dtln_latency_ms_p95=3.1,
    )
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT aec_layer1_active, aec_layer3_active, output_profile, "
            "apm_delay_ms_p50, dtln_latency_ms_p95 FROM turns "
            "WHERE user_text='hi'"
        ).fetchone()
    assert row == (1, 1, "speakers", 42, 3.1)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_aec_telemetry.py -v
```

Expected: both FAIL (columns + kwargs absent).

- [ ] **Step 3: Add the migration block**

In `pipeline/turn_telemetry.py::init_db()`, after the most-recent `turns` migration (the `confab_check_state` block from 2026-05-19) and before any CREATE INDEX, ADD:

```python
        # 2026-05-19 — echo-cancellation cascade per-turn audit. Six
        # columns: which AEC layers were active, the detected output
        # profile, and the L2 delay / L3 latency observed. Written from
        # the agent by reading ~/.jarvis/aec-state.json (the AEC runs in
        # the voice-client process). Spec: 2026-05-19-echo-cancellation-cascade-design.md §5.5
        aec_cols = {r[1] for r in conn.execute("PRAGMA table_info(turns)")}
        for col, decl in (
            ("aec_layer1_active",     "INTEGER"),
            ("aec_layer2_aec_active", "INTEGER"),
            ("aec_layer3_active",     "INTEGER"),
            ("output_profile",        "TEXT"),
            ("apm_delay_ms_p50",      "INTEGER"),
            ("dtln_latency_ms_p95",   "REAL"),
        ):
            if col not in aec_cols:
                try:
                    conn.execute(f"ALTER TABLE turns ADD COLUMN {col} {decl}")
                except sqlite3.OperationalError:
                    pass
```

- [ ] **Step 4: Extend `log_turn` signature + INSERT**

Add these kwargs at the END of `log_turn`'s parameter list (after the existing `confab_check_state` kwarg):

```python
    aec_layer1_active: Optional[int] = None,
    aec_layer2_aec_active: Optional[int] = None,
    aec_layer3_active: Optional[int] = None,
    output_profile: Optional[str] = None,
    apm_delay_ms_p50: Optional[int] = None,
    dtln_latency_ms_p95: Optional[float] = None,
```

Update the INSERT column list + VALUES placeholders + value tuple to include all six (in the same order). Count placeholders carefully — six new `?` and six new tuple entries.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_aec_telemetry.py tests/test_turn_telemetry.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/pipeline/turn_telemetry.py src/voice-agent/tests/test_aec_telemetry.py
git commit -m "feat(telemetry): AEC cascade columns on turns

Six columns for the echo-cancellation cascade per-turn audit:
aec_layer{1,2,3}_active, output_profile, apm_delay_ms_p50,
dtln_latency_ms_p95. Online migration + log_turn kwargs. Written
from the agent by reading ~/.jarvis/aec-state.json. Per spec
2026-05-19 §5.5."
```

---

## Task 2: Output-device auto-detect

**Files:**
- Create: `src/voice-agent/audio/__init__.py`, `src/voice-agent/audio/output_profile.py`
- Test: `src/voice-agent/tests/test_output_profile.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_output_profile.py`:

```python
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
    monkeypatch.setattr(output_profile, "_active_sink_block", lambda: _HEADSET_SINK)
    output_profile.classify_output_device.cache_clear()
    assert output_profile.classify_output_device() == "headphones"


def test_classify_speaker(monkeypatch):
    from audio import output_profile
    monkeypatch.setattr(output_profile, "_active_sink_block", lambda: _SPEAKER_SINK)
    output_profile.classify_output_device.cache_clear()
    assert output_profile.classify_output_device() == "speakers"


def test_classify_unknown_on_empty(monkeypatch):
    from audio import output_profile
    monkeypatch.setattr(output_profile, "_active_sink_block", lambda: "")
    output_profile.classify_output_device.cache_clear()
    assert output_profile.classify_output_device() == "unknown"


def test_force_profile_override(monkeypatch):
    from audio import output_profile
    monkeypatch.setenv("JARVIS_AEC_FORCE_PROFILE", "headphones")
    monkeypatch.setattr(output_profile, "_active_sink_block", lambda: _SPEAKER_SINK)
    output_profile.classify_output_device.cache_clear()
    assert output_profile.classify_output_device() == "headphones"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_output_profile.py -v
```

Expected: 4 FAIL (module missing).

- [ ] **Step 3: Create the package + module**

Create `src/voice-agent/audio/__init__.py` (empty file).

Create `src/voice-agent/audio/output_profile.py`:

```python
"""Output-device profile detection for AEC strategy gating.

Classifies the active PipeWire/PulseAudio sink as headphones,
speakers, or unknown. The DTLN neural residual (L3) only runs on
speakers (headphones have no echo path). Re-detects on hot-plug via
a pw-mon subprocess watcher.

Spec: docs/superpowers/specs/2026-05-19-echo-cancellation-cascade-design.md §5.4
"""
from __future__ import annotations

import functools
import logging
import os
import subprocess
import threading
import time
from typing import Callable, Literal

logger = logging.getLogger("jarvis.audio.output_profile")

Profile = Literal["headphones", "speakers", "unknown"]

_HEADPHONE_PORT_TOKENS = ("headphone", "headset", "hands-free", "handsfree")
_SPEAKER_PORT_TOKENS = ("speaker", "line", "hdmi")
_HEADPHONE_FORM = ("headset", "headphone")

# 30s TTL applied via a coarse time bucket arg to lru_cache.
_TTL_S = 30


def _active_sink_block() -> str:
    """Return the full `pactl list sinks` block for the default sink.
    Empty string if pactl is unavailable. Split out for test mocking."""
    try:
        default = subprocess.run(
            ["pactl", "get-default-sink"], capture_output=True, text=True, timeout=2
        ).stdout.strip()
        full = subprocess.run(
            ["pactl", "list", "sinks"], capture_output=True, text=True, timeout=2
        ).stdout
    except Exception:
        return ""
    # Slice out the block for the default-named sink.
    blocks = full.split("Sink #")
    for b in blocks:
        if default and default in b:
            return b
    return blocks[1] if len(blocks) > 1 else ""


def _classify_block(block: str) -> Profile:
    low = block.lower()
    # Active port line takes priority.
    for line in low.splitlines():
        if line.strip().startswith("active port:"):
            if any(t in line for t in _HEADPHONE_PORT_TOKENS):
                return "headphones"
            if any(t in line for t in _SPEAKER_PORT_TOKENS):
                return "speakers"
    # Fall back to form factor.
    if any(f'form_factor = "{f}"' in low for f in _HEADPHONE_FORM):
        return "headphones"
    if "form_factor" in low or "speaker" in low or "analog-output" in low:
        return "speakers"
    return "unknown"


@functools.lru_cache(maxsize=4)
def _classify_cached(_ttl_bucket: int) -> Profile:
    forced = os.environ.get("JARVIS_AEC_FORCE_PROFILE", "").strip().lower()
    if forced in ("headphones", "speakers", "unknown"):
        return forced  # type: ignore[return-value]
    block = _active_sink_block()
    if not block.strip():
        return "unknown"
    return _classify_block(block)


def classify_output_device() -> Profile:
    """Classify the active output device. Cached for ~30s. Honors
    JARVIS_AEC_FORCE_PROFILE override."""
    return _classify_cached(int(time.time() // _TTL_S))


# `classify_output_device.cache_clear` shim for tests.
classify_output_device.cache_clear = _classify_cached.cache_clear  # type: ignore[attr-defined]


def watch_for_changes(callback: Callable[[Profile], None]) -> threading.Thread:
    """Spawn a daemon thread running `pw-mon` and invoke callback with
    the new profile on each node/port change. No-op thread if pw-mon
    is unavailable."""
    def _run() -> None:
        try:
            proc = subprocess.Popen(
                ["pw-mon"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
            )
        except FileNotFoundError:
            logger.warning("[output_profile] pw-mon unavailable; hot-plug detection off")
            return
        last: Profile = classify_output_device()
        for line in proc.stdout:  # type: ignore[union-attr]
            if any(k in line for k in ("changed", "Port", "Node")):
                classify_output_device.cache_clear()  # type: ignore[attr-defined]
                cur = classify_output_device()
                if cur != last:
                    last = cur
                    try:
                        callback(cur)
                    except Exception as e:
                        logger.warning(f"[output_profile] callback raised: {e}")

    t = threading.Thread(target=_run, name="aec-profile-watch", daemon=True)
    t.start()
    return t
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_output_profile.py -v
```

Expected: 4 pass.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/audio/__init__.py src/voice-agent/audio/output_profile.py \
        src/voice-agent/tests/test_output_profile.py
git commit -m "feat(audio): output-device auto-detect for AEC gating

classify_output_device() → headphones/speakers/unknown via pactl
active-port + form-factor parsing, 30s TTL cache,
JARVIS_AEC_FORCE_PROFILE override. watch_for_changes() spawns a
pw-mon daemon thread for hot-plug re-detect. L3 (DTLN) gates on
this. Per spec 2026-05-19 §5.4."
```

---

## Task 3: APM delay estimator

**Files:**
- Create: `src/voice-agent/audio/apm_reverse_stream.py` (estimator portion)
- Test: `src/voice-agent/tests/test_apm_delay_estimator.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_apm_delay_estimator.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_apm_delay_estimator.py -v
```

Expected: 4 FAIL (module missing).

- [ ] **Step 3: Create the module (estimator portion)**

Create `src/voice-agent/audio/apm_reverse_stream.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_apm_delay_estimator.py -v
```

Expected: 4 pass.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/audio/apm_reverse_stream.py \
        src/voice-agent/tests/test_apm_delay_estimator.py
git commit -m "feat(audio): APM stream-delay estimator

APMDelayEstimator tracks DAC vs ADC timestamps → median round-trip
delay for apm.set_stream_delay_ms(). Clamped [0,500] ms,
JARVIS_APM_DELAY_BIAS_MS offset. Ports LiveKit's internal estimator
pattern. Per spec 2026-05-19 §5.2."
```

---

## Task 4: Reverse-reference ring buffer

**Files:**
- Modify: `src/voice-agent/audio/apm_reverse_stream.py` (add ring buffer)
- Test: `src/voice-agent/tests/test_reverse_ring_buffer.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_reverse_ring_buffer.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_reverse_ring_buffer.py -v
```

Expected: 3 FAIL (`ReverseRefRingBuffer` missing).

- [ ] **Step 3: Add the ring buffer to apm_reverse_stream.py**

Append to `src/voice-agent/audio/apm_reverse_stream.py`:

```python
from scipy.signal import resample_poly


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
        # 48k → 16k (decimate by 3). 480 → 160 samples.
        f16 = resample_poly(frame_48k.astype(np.float32), up=1, down=3).astype(np.float32)
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_reverse_ring_buffer.py -v
```

Expected: 3 pass.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/audio/apm_reverse_stream.py \
        src/voice-agent/tests/test_reverse_ring_buffer.py
git commit -m "feat(audio): thread-safe reverse-reference ring buffer

ReverseRefRingBuffer: OutputStream thread writes 48k frames
(downsampled to 16k via scipy resample_poly off the mic path);
InputStream thread reads the most-recent dac_ts-aligned 160-sample
16k frame for DTLN's reference. Single lock, microsecond hold.
Empty read → zeros (no playback → no echo). Per spec 2026-05-19 §5.2."
```

---

## Task 5: Cross-process AEC state file

**Files:**
- Create: `src/voice-agent/audio/aec_state.py`
- Test: `src/voice-agent/tests/test_aec_state_file.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_aec_state_file.py`:

```python
"""Cross-process AEC state file (voice-client writer → agent reader)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_write_then_read_roundtrip(tmp_path):
    from audio.aec_state import write_aec_state, read_aec_state
    p = tmp_path / "aec-state.json"
    write_aec_state(p, output_profile="speakers", l1_active=True,
                    l2_aec_active=False, l3_active=True,
                    apm_delay_ms_p50=42, dtln_latency_ms_p95=3.1)
    state = read_aec_state(p, max_age_s=60)
    assert state["output_profile"] == "speakers"
    assert state["aec_layer1_active"] == 1
    assert state["aec_layer2_aec_active"] == 0
    assert state["aec_layer3_active"] == 1
    assert state["apm_delay_ms_p50"] == 42
    assert state["dtln_latency_ms_p95"] == 3.1


def test_stale_file_returns_nulls(tmp_path):
    from audio.aec_state import write_aec_state, read_aec_state
    p = tmp_path / "aec-state.json"
    write_aec_state(p, output_profile="speakers", l1_active=True,
                    l2_aec_active=False, l3_active=True,
                    apm_delay_ms_p50=42, dtln_latency_ms_p95=3.1)
    # Force the mtime/updated_utc to look 120s old by patching the file.
    import json
    data = json.loads(p.read_text())
    data["updated_utc"] = "2000-01-01T00:00:00Z"
    p.write_text(json.dumps(data))
    state = read_aec_state(p, max_age_s=60)
    assert state["output_profile"] is None
    assert state["aec_layer1_active"] is None


def test_missing_file_returns_nulls(tmp_path):
    from audio.aec_state import read_aec_state
    state = read_aec_state(tmp_path / "nope.json", max_age_s=60)
    assert all(v is None for v in state.values())
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_aec_state_file.py -v
```

Expected: 3 FAIL (module missing).

- [ ] **Step 3: Create the module**

Create `src/voice-agent/audio/aec_state.py`:

```python
"""Cross-process AEC state bridge.

AEC runs in the voice-client process; per-turn telemetry is written by
the agent process. This module is the bridge: the voice-client writes
a small JSON state file (atomic), the agent reads it at turn-write time
with a staleness guard.

Mirrors JARVIS's existing flat-file IPC convention (~/.jarvis/cli-model,
voice-model, tool-busy flags). Spec: 2026-05-19 §5.5.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis.audio.aec_state")

DEFAULT_PATH = Path.home() / ".jarvis" / "aec-state.json"

_NULL_STATE = {
    "output_profile": None,
    "aec_layer1_active": None,
    "aec_layer2_aec_active": None,
    "aec_layer3_active": None,
    "apm_delay_ms_p50": None,
    "dtln_latency_ms_p95": None,
}


def write_aec_state(
    path: Path = DEFAULT_PATH, *,
    output_profile: str,
    l1_active: bool,
    l2_aec_active: bool,
    l3_active: bool,
    apm_delay_ms_p50: Optional[int],
    dtln_latency_ms_p95: Optional[float],
) -> None:
    """Atomically write the current AEC state (voice-client side)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "output_profile": output_profile,
        "l1_active": bool(l1_active),
        "l2_aec_active": bool(l2_aec_active),
        "l3_active": bool(l3_active),
        "apm_delay_ms_p50": apm_delay_ms_p50,
        "dtln_latency_ms_p95": dtln_latency_ms_p95,
        "updated_utc": datetime.datetime.now(datetime.timezone.utc)
            .isoformat().replace("+00:00", "Z"),
    }
    try:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)   # atomic
    except Exception as e:
        logger.warning(f"[aec_state] write failed: {e}")


def read_aec_state(path: Path = DEFAULT_PATH, *, max_age_s: int = 60) -> dict:
    """Read the AEC state (agent side), mapping JSON keys to the
    turns-table column names. Returns all-None if the file is missing,
    malformed, or older than max_age_s (voice-client may have died)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(_NULL_STATE)
    ts = raw.get("updated_utc", "")
    try:
        t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        age = (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds()
        if age > max_age_s:
            return dict(_NULL_STATE)
    except Exception:
        return dict(_NULL_STATE)
    return {
        "output_profile": raw.get("output_profile"),
        "aec_layer1_active": int(bool(raw.get("l1_active"))) if "l1_active" in raw else None,
        "aec_layer2_aec_active": int(bool(raw.get("l2_aec_active"))) if "l2_aec_active" in raw else None,
        "aec_layer3_active": int(bool(raw.get("l3_active"))) if "l3_active" in raw else None,
        "apm_delay_ms_p50": raw.get("apm_delay_ms_p50"),
        "dtln_latency_ms_p95": raw.get("dtln_latency_ms_p95"),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_aec_state_file.py -v
```

Expected: 3 pass.

- [ ] **Step 5: Wire the reader into the agent's log_turn path**

In `src/voice-agent/jarvis_agent.py`, find the `log_turn(...)` call site (the one updated in the confab work, ~line 5540-5571). BEFORE it, ADD:

```python
        # 2026-05-19 — read the voice-client's AEC state (cross-process)
        # and thread it into the turn row. Stale/missing → NULLs.
        try:
            from audio.aec_state import read_aec_state
            _aec = read_aec_state(max_age_s=60)
        except Exception:
            _aec = {}
```

Then add to the `log_turn(...)` call kwargs:

```python
            aec_layer1_active=_aec.get("aec_layer1_active"),
            aec_layer2_aec_active=_aec.get("aec_layer2_aec_active"),
            aec_layer3_active=_aec.get("aec_layer3_active"),
            output_profile=_aec.get("output_profile"),
            apm_delay_ms_p50=_aec.get("apm_delay_ms_p50"),
            dtln_latency_ms_p95=_aec.get("dtln_latency_ms_p95"),
```

- [ ] **Step 6: Run telemetry + state tests + import sanity**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_aec_state_file.py tests/test_aec_telemetry.py -v
.venv/bin/python -c "import jarvis_agent" 2>&1 | tail -2
```

Expected: tests pass; jarvis_agent imports cleanly.

- [ ] **Step 7: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/audio/aec_state.py \
        src/voice-agent/tests/test_aec_state_file.py \
        src/voice-agent/jarvis_agent.py
git commit -m "feat(audio): cross-process AEC state bridge

write_aec_state (voice-client, atomic) + read_aec_state (agent,
stale-guarded → NULLs if >60s old / missing / malformed). Maps JSON
keys to the turns-table column names. Wired into the agent's
log_turn call site. Closes the voice-client→agent process boundary
for AEC telemetry. Per spec 2026-05-19 §5.5."
```

---

## Task 6: PipeWire L1 tuning + reload helper

**Files:**
- Modify: `install.sh`
- Create: `bin/jarvis-aec-reload`

- [ ] **Step 1: Inspect the existing module-echo-cancel load in install.sh**

```bash
grep -nE "module-echo-cancel|echo.cancel|pw-cli load|pactl load-module" /home/ulrich/Documents/Projects/jarvis/install.sh
```

Capture the existing load command (if any). If none exists (echo-cancel was loaded manually/once), the new `bin/jarvis-aec-reload` becomes the canonical loader and install.sh calls it.

- [ ] **Step 2: Create bin/jarvis-aec-reload**

Create `bin/jarvis-aec-reload`:

```bash
#!/usr/bin/env bash
# Idempotent (re)load of PipeWire module-echo-cancel with JARVIS's
# tuned AEC3 args. Unloads any existing instance first.
#
# Tuning rationale (spec 2026-05-19 §5.1):
#   webrtc.extended_filter=true   — better long-tail echo paths
#   filter_size_ms=200            — laptop speaker echo path >50ms
#   monitor.mode=true             — real reference when other apps play
#   NS/HPF/AGC OFF at L1          — owned by the APM layer (no double-DSP)
#
# Disable entirely with JARVIS_PIPEWIRE_AEC=0 (skips the load).
set -euo pipefail

if [[ "${JARVIS_PIPEWIRE_AEC:-1}" != "1" ]]; then
    echo "[aec-reload] JARVIS_PIPEWIRE_AEC=0 — skipping module-echo-cancel load"
    exit 0
fi

# Unload existing echo-cancel modules (idempotent).
for id in $(pactl list short modules 2>/dev/null | awk '/module-echo-cancel/{print $1}'); do
    pactl unload-module "$id" 2>/dev/null || true
done

AEC_ARGS='webrtc.extended_filter=true webrtc.high_pass_filter=false webrtc.noise_suppression=false webrtc.gain_control=false filter_size_ms=200 monitor.mode=true'

pactl load-module module-echo-cancel \
    aec_method=webrtc \
    source_name=echo-cancel-source \
    sink_name=echo-cancel-sink \
    aec_args="$AEC_ARGS" >/dev/null

echo "[aec-reload] module-echo-cancel loaded (args: $AEC_ARGS)"

# Verify the source/sink appeared.
if pactl list short sources 2>/dev/null | grep -q echo-cancel-source; then
    echo "[aec-reload] ✓ echo-cancel-source present"
else
    echo "[aec-reload] ✗ echo-cancel-source NOT present — check pactl/pipewire" >&2
    exit 1
fi
```

- [ ] **Step 3: Make executable + smoke test**

```bash
chmod +x /home/ulrich/Documents/Projects/jarvis/bin/jarvis-aec-reload
/home/ulrich/Documents/Projects/jarvis/bin/jarvis-aec-reload
pactl list short sources | grep echo-cancel-source
```

Expected: reload succeeds, echo-cancel-source listed. (If the box's PipeWire uses `pw-cli load-module libpipewire-module-echo-cancel` with `aec.args` instead of the pactl form, ADAPT the script to that form — verify with `pactl list short modules` what shape this system accepts.)

- [ ] **Step 4: Wire into install.sh**

In `install.sh`, find where audio/PipeWire is set up (or the end of the setup section). ADD a call to the reload helper:

```bash
# 2026-05-19 — load tuned echo-cancel (AEC3, filter_size_ms=200).
# Idempotent; honors JARVIS_PIPEWIRE_AEC=0. Spec 2026-05-19 §5.1.
if [ -x "$INSTALL_DIR/bin/jarvis-aec-reload" ]; then
    "$INSTALL_DIR/bin/jarvis-aec-reload" || warn "echo-cancel load failed (non-fatal)"
fi
```

Use whatever `$INSTALL_DIR` / `warn` conventions install.sh already uses (check the file head).

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add bin/jarvis-aec-reload install.sh
git commit -m "feat(aec): L1 — tuned PipeWire module-echo-cancel + reload helper

bin/jarvis-aec-reload idempotently (re)loads module-echo-cancel
with AEC3 tuning: extended_filter=true, filter_size_ms=200,
monitor.mode=true; NS/HPF/AGC OFF at L1 (owned by APM, no double-
DSP). Honors JARVIS_PIPEWIRE_AEC=0. install.sh calls it at setup.
Per spec 2026-05-19 §5.1."
```

---

## Task 7: Wire L2 reverse-stream + remove mic-drop (integration)

**Files:**
- Modify: `src/voice-agent/jarvis_voice_client.py`
- Test: `src/voice-agent/tests/test_mic_path_layers.py` (new)

- [ ] **Step 1: Write the failing integration test**

Create `src/voice-agent/tests/test_mic_path_layers.py`:

```python
"""Integration — mic path layer gating + barge-in (no mic-drop on speakers)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_mic_frames_flow_during_speak_on_speakers(monkeypatch):
    """The core barge-in fix: while state.speaking=True on speakers,
    mic frames must STILL be captured (not dropped)."""
    import jarvis_voice_client as vc

    captured = []
    # Build the mic callback in isolation via the helper the refactor exposes.
    decide = vc._should_publish_during_speak  # new helper added in Step 3
    # On speakers with AEC active, publish during speak:
    assert decide(profile="speakers", apm_aec=False, neural_aec=True) is True
    # On headphones, also publish (no echo path):
    assert decide(profile="headphones", apm_aec=False, neural_aec=False) is True
    # All AEC off + speakers → fall back to mic-drop (don't publish):
    assert decide(profile="speakers", apm_aec=False, neural_aec=False) is False
```

(Note: the test exercises the decision helper rather than the full PortAudio loop, which can't run headless. The helper isolates the publish-during-speak logic for testability.)

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_mic_path_layers.py -v
```

Expected: FAIL (`_should_publish_during_speak` missing).

- [ ] **Step 3: Add the decision helper + wire reverse-stream**

In `src/voice-agent/jarvis_voice_client.py`, ADD a module-level helper near the APM block (~line 135):

```python
def _should_publish_during_speak(*, profile: str, apm_aec: bool, neural_aec: bool) -> bool:
    """2026-05-19 — replaces the blanket 'drop mic while speaking'.
    Publish mic frames during TTS playback when SOME echo defense is
    active (so barge-in works); fall back to mic-drop only when all
    AEC is off on a speaker (the legacy safety net).

      - headphones: always publish (no echo path)
      - speakers + (APM AEC OR neural AEC active): publish (AEC handles echo)
      - speakers + no AEC at all: don't publish (legacy mic-drop)
    Spec 2026-05-19 §4.2 degradation ladder."""
    if profile == "headphones":
        return True
    if apm_aec or neural_aec:
        return True
    return False
```

REPLACE the existing mic-drop branch in `_mic_cb` (the `if state.speaking and os.environ.get("JARVIS_MIC_DURING_SPEAK"...) : return` at ~line 714) with:

```python
        if state.speaking:
            from audio.output_profile import classify_output_device
            _profile = classify_output_device()
            if not _should_publish_during_speak(
                profile=_profile, apm_aec=_APM_AEC,
                neural_aec=(os.environ.get("JARVIS_NEURAL_AEC", "1") == "1"),
            ):
                return
```

In `play_subscribed_track` (the OutputStream owner, ~line 348-413), after the `out = sd.OutputStream(...)` and inside the frame-render loop where each playback frame is written, ADD the reverse-stream feed:

```python
            # 2026-05-19 — feed APM the playback reference + stash for
            # DTLN. Without this, APM AEC has no reference (was why
            # _APM_AEC defaulted off). Spec §5.2 / §6.2.
            try:
                if _apm is not None:
                    _apm.process_reverse_stream(frame)
                _reverse_estimator.note_output(time.monotonic())
                _reverse_ringbuf.write(
                    np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0,
                    dac_ts=time.monotonic(),
                )
            except Exception as e:
                log.debug(f"[playback] reverse-stream feed failed: {e}")
```

ADD module-level singletons near the APM block:

```python
from audio.apm_reverse_stream import APMDelayEstimator, ReverseRefRingBuffer
_reverse_estimator = APMDelayEstimator()
_reverse_ringbuf = ReverseRefRingBuffer(capacity_frames=64)
```

In `_mic_cb`, before `_apm.process_stream(frame)`, ADD the delay estimate:

```python
        if _apm is not None:
            try:
                _reverse_estimator.note_input(time.monotonic())
                _apm.set_stream_delay_ms(_reverse_estimator.current_delay_ms())
            except Exception:
                pass
```

- [ ] **Step 4: Write the AEC state file + start the profile watcher**

In `jarvis_voice_client.py` startup (where the mic/output streams are set up), ADD periodic state-file writes + the profile watcher. Near the stream setup:

```python
    from audio.output_profile import classify_output_device, watch_for_changes
    from audio.aec_state import write_aec_state

    def _write_aec_state_snapshot() -> None:
        prof = classify_output_device()
        write_aec_state(
            output_profile=prof,
            l1_active=(os.environ.get("JARVIS_PIPEWIRE_AEC", "1") == "1"),
            l2_aec_active=_APM_AEC,
            l3_active=(os.environ.get("JARVIS_NEURAL_AEC", "1") == "1" and prof == "speakers"),
            apm_delay_ms_p50=_reverse_estimator.current_delay_ms(),
            dtln_latency_ms_p95=None,   # filled in Phase 2
        )

    _write_aec_state_snapshot()
    watch_for_changes(lambda _profile: _write_aec_state_snapshot())
```

Also call `_write_aec_state_snapshot()` periodically (e.g., piggyback on an existing status-poll loop, or a 10s timer). Adapt to the existing loop structure.

- [ ] **Step 5: Run test + import sanity + vite-equivalent (none — Python)**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_mic_path_layers.py -v
.venv/bin/python -c "import jarvis_voice_client" 2>&1 | tail -2
```

Expected: test passes; module imports cleanly.

- [ ] **Step 6: Run the full voice-agent suite**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/ --timeout=60 -q \
  --deselect tests/test_browser_subagent.py::test_browser_spec_loads_all_ext_tools \
  --deselect tests/test_evolution_batch_miner.py::test_mine_returns_proposals_from_telemetry 2>&1 | tail -5
```

Expected: all pass except the two pre-existing deselects.

- [ ] **Step 7: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/jarvis_voice_client.py src/voice-agent/tests/test_mic_path_layers.py
git commit -m "feat(aec): L2 — reverse-stream wiring + remove mic-drop (barge-in)

play_subscribed_track now feeds every playback frame through
apm.process_reverse_stream + the reference ring buffer + delay
estimator. _mic_cb sets stream-delay before process_stream. The
blanket 'drop mic while speaking' is replaced by
_should_publish_during_speak: publish during TTS when any echo
defense is active (barge-in works), mic-drop fallback only when all
AEC is off on speakers. Voice-client writes ~/.jarvis/aec-state.json
+ starts the pw-mon profile watcher. Per spec 2026-05-19 §5.2/§6.2."
```

---

## Task 8: bin/jarvis-aec-soak observability

**Files:**
- Create: `bin/jarvis-aec-soak`

- [ ] **Step 1: Create the script**

Create `bin/jarvis-aec-soak`:

```bash
#!/usr/bin/env bash
# Echo-cancellation cascade soak validation.
# Rolls up AEC layer activation + the echo-loop detector (STT
# transcripts produced WHILE the agent was speaking → should be 0).
#
# Usage: jarvis-aec-soak [hours]   (default 2)
# Spec: docs/superpowers/specs/2026-05-19-echo-cancellation-cascade-design.md §5.5
set -euo pipefail

HOURS="${1:-2}"
DB="$HOME/.local/share/jarvis/turn_telemetry.db"
[[ -f "$DB" ]] || { echo "missing telemetry db: $DB" >&2; exit 1; }

# Self-heal: add columns if the live DB predates the migration.
for col in aec_layer1_active aec_layer2_aec_active aec_layer3_active \
           output_profile apm_delay_ms_p50 dtln_latency_ms_p95; do
    if ! sqlite3 "$DB" "SELECT $col FROM turns LIMIT 0;" >/dev/null 2>&1; then
        decl="INTEGER"; [[ "$col" == "output_profile" ]] && decl="TEXT"
        [[ "$col" == "dtln_latency_ms_p95" ]] && decl="REAL"
        sqlite3 "$DB" "ALTER TABLE turns ADD COLUMN $col $decl;" 2>/dev/null || true
    fi
done

CUT="datetime('now', '-${HOURS} hours')"

echo "=== AEC cascade soak — last ${HOURS}h ==="
echo
echo "── output_profile distribution ──"
sqlite3 -header -column "$DB" "
SELECT COALESCE(output_profile,'(null)') AS profile, COUNT(*) AS turns
FROM turns WHERE ts_utc >= ${CUT} GROUP BY output_profile ORDER BY turns DESC;"
echo
echo "── layer activation rates ──"
sqlite3 -header -column "$DB" "
SELECT
  SUM(COALESCE(aec_layer1_active,0)) AS l1_on,
  SUM(COALESCE(aec_layer2_aec_active,0)) AS l2_aec_on,
  SUM(COALESCE(aec_layer3_active,0)) AS l3_on,
  COUNT(*) AS total
FROM turns WHERE ts_utc >= ${CUT};"
echo
echo "── APM delay + DTLN latency ──"
sqlite3 -header -column "$DB" "
SELECT
  ROUND(AVG(apm_delay_ms_p50),1) AS avg_apm_delay_ms,
  MAX(apm_delay_ms_p50) AS max_apm_delay_ms,
  ROUND(AVG(dtln_latency_ms_p95),2) AS avg_dtln_ms,
  MAX(dtln_latency_ms_p95) AS max_dtln_ms
FROM turns WHERE ts_utc >= ${CUT};"
echo
echo "── ECHO-LOOP DETECTOR (transcripts during agent speech → should be 0) ──"
# A turn whose user_text closely echoes the PRIOR turn's jarvis_text is a
# likely self-transcription. Heuristic: user_text is a substring of the
# previous jarvis_text (>=12 chars overlap).
ECHO=$(sqlite3 "$DB" "
WITH seq AS (
  SELECT ts_utc, user_text,
         LAG(jarvis_text) OVER (ORDER BY ts_utc) AS prev_jarvis
  FROM turns WHERE ts_utc >= ${CUT}
)
SELECT COUNT(*) FROM seq
WHERE prev_jarvis IS NOT NULL AND length(user_text) >= 12
  AND instr(lower(prev_jarvis), lower(user_text)) > 0;" 2>/dev/null || echo 0)

if [[ "$ECHO" -gt 0 ]]; then
    echo "  ✗ $ECHO turns look like self-transcription (echo leaked):"
    sqlite3 -separator ' | ' "$DB" "
    WITH seq AS (
      SELECT ts_utc, user_text, COALESCE(output_profile,'?') AS prof,
             LAG(jarvis_text) OVER (ORDER BY ts_utc) AS prev_jarvis
      FROM turns WHERE ts_utc >= ${CUT}
    )
    SELECT substr(ts_utc,12,8), prof, substr(user_text,1,50)
    FROM seq WHERE prev_jarvis IS NOT NULL AND length(user_text)>=12
      AND instr(lower(prev_jarvis), lower(user_text))>0
    ORDER BY ts_utc DESC LIMIT 10;"
    echo "  Investigate: was AEC active (check layer columns above)?"
    exit 2
fi
echo "  ✓ no self-transcription detected"
echo
echo "── interpretation guide ──"
cat <<'EOF'
  Target (post-fix, speaker hardware):
    output_profile=speakers turns: l3_on should ≈ total (DTLN running)
    echo-loop detector: 0 (the headline metric)
    max_dtln_ms < JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS (default 8)
  HARD-FAIL (exit 2): any self-transcription detected.
EOF
```

- [ ] **Step 2: Make executable + smoke test**

```bash
chmod +x /home/ulrich/Documents/Projects/jarvis/bin/jarvis-aec-soak
/home/ulrich/Documents/Projects/jarvis/bin/jarvis-aec-soak 24 || true
```

Expected: runs; shows current distribution. May exit 2 if pre-fix echo-loop turns exist in the window (expected on historical data).

- [ ] **Step 3: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add bin/jarvis-aec-soak
git commit -m "feat(bin): jarvis-aec-soak — cascade soak + echo-loop detector

Rolls up output_profile distribution, layer activation rates,
APM delay + DTLN latency, and the headline echo-loop detector
(self-transcription heuristic: user_text substring of prior
jarvis_text → echo leaked). Exits 2 on any detection for systemd-
timer alerting. Self-heals the columns pre-restart. Per spec
2026-05-19 §5.5 + §8 A3."
```

---

## PHASE 2 — L3 neural residual (A/B-gated)

## Task 9: DTLN model download + hash verify

**Files:**
- Modify: `install.sh`
- Create: `src/voice-agent/models/.gitignore` (ignore the .onnx blob)

- [ ] **Step 1: Add the download + verify block to install.sh**

In `install.sh`, after the PipeWire AEC setup (Task 6), ADD:

```bash
# 2026-05-19 — DTLN-aec neural residual model (Phase 2, L3).
# ~2MB ONNX. Skip with JARVIS_NEURAL_AEC=0. Spec 2026-05-19 §5.3.
DTLN_DIR="$INSTALL_DIR/src/voice-agent/models"
DTLN_ONNX="$DTLN_DIR/dtln_aec_128.onnx"
DTLN_SHA256="REPLACE_WITH_PINNED_SHA256_AT_IMPLEMENTATION_TIME"
DTLN_URL="https://github.com/breizhn/DTLN-aec/raw/main/pretrained_models/dtln_aec_128.onnx"
if [ "${JARVIS_NEURAL_AEC:-1}" = "1" ]; then
    mkdir -p "$DTLN_DIR"
    if [ ! -f "$DTLN_ONNX" ] || ! echo "$DTLN_SHA256  $DTLN_ONNX" | sha256sum -c - >/dev/null 2>&1; then
        echo "[install] downloading DTLN-aec model…"
        if curl -fsSL "$DTLN_URL" -o "$DTLN_ONNX.tmp" 2>/dev/null; then
            mv "$DTLN_ONNX.tmp" "$DTLN_ONNX"
            echo "[install] DTLN model: $(sha256sum "$DTLN_ONNX" | cut -d' ' -f1)"
        else
            warn "DTLN model download failed — L3 neural AEC disabled until present"
            rm -f "$DTLN_ONNX.tmp"
        fi
    fi
fi
```

IMPORTANT during implementation: download the model once manually, compute its real SHA256, and replace `REPLACE_WITH_PINNED_SHA256_AT_IMPLEMENTATION_TIME`. If breizhn's repo only ships TFLite (not ONNX), convert TFLite→ONNX with `tf2onnx` once and host the result in JARVIS's own release assets; update `DTLN_URL` to that mirror. Document the provenance in the install.sh comment.

- [ ] **Step 2: Create the models .gitignore**

Create `src/voice-agent/models/.gitignore`:

```
# The DTLN ONNX blob is downloaded by install.sh, not committed.
*.onnx
*.tflite
```

- [ ] **Step 3: Verify download works (manual)**

```bash
cd /home/ulrich/Documents/Projects/jarvis
JARVIS_NEURAL_AEC=1 bash -c 'source install.sh 2>/dev/null; true' || true
ls -la src/voice-agent/models/dtln_aec_128.onnx 2>/dev/null && echo "model present" || echo "download path needs the SHA + URL finalized"
```

Expected: either the model downloads, OR you confirm the URL/SHA need finalizing (and you do so).

- [ ] **Step 4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add install.sh src/voice-agent/models/.gitignore
git commit -m "feat(aec): L3 — DTLN model download + hash verify (install.sh)

Downloads dtln_aec_128.onnx (~2MB) to src/voice-agent/models/,
SHA256-verified, skipped with JARVIS_NEURAL_AEC=0. .gitignore keeps
the blob out of the repo. Per spec 2026-05-19 §5.3."
```

---

## Task 10: DTLNResidualFilter

**Files:**
- Create: `src/voice-agent/audio/dtln_aec.py`
- Test: `src/voice-agent/tests/test_dtln_residual.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_dtln_residual.py`:

```python
"""DTLN-aec residual filter: ERLE on a synthetic fixture + latency
budget self-disable. Skips ERLE assertions if the model isn't present."""
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_MODEL = Path(__file__).parent.parent / "models" / "dtln_aec_128.onnx"


def _synth_echo_fixture():
    """near-end speech + far-end ref convolved into echo → contaminated mic."""
    rng = np.random.default_rng(0)
    near = (rng.standard_normal(160) * 0.1).astype(np.float32)
    far = (rng.standard_normal(160) * 0.5).astype(np.float32)
    # Simple 1-tap echo: mic = near + 0.6*far (delayed 0 for the test frame)
    mic = (near + 0.6 * far).astype(np.float32)
    return mic, far, near


def test_construct_without_model_disables_gracefully(tmp_path):
    from audio.dtln_aec import DTLNResidualFilter
    f = DTLNResidualFilter(model_path=tmp_path / "nope.onnx")
    assert f.enabled is False
    # process() passes the mic through unchanged when disabled.
    mic, far, _ = _synth_echo_fixture()
    out = f.process(mic, far)
    assert np.allclose(out, mic)


@pytest.mark.skipif(not _MODEL.exists(), reason="DTLN model not downloaded")
def test_erle_reduction_on_fixture():
    from audio.dtln_aec import DTLNResidualFilter
    f = DTLNResidualFilter(model_path=_MODEL)
    assert f.enabled is True
    mic, far, near = _synth_echo_fixture()
    out = f.process(mic, far)
    # ERLE: residual echo energy should drop vs the raw mic.
    raw_err = float(np.mean((mic - near) ** 2))
    out_err = float(np.mean((out - near) ** 2))
    erle_db = 10 * np.log10(raw_err / max(out_err, 1e-9))
    assert erle_db >= 3.0   # conservative floor on a 1-frame synthetic


@pytest.mark.skipif(not _MODEL.exists(), reason="DTLN model not downloaded")
def test_latency_under_budget(monkeypatch):
    monkeypatch.setenv("JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS", "8")
    from audio.dtln_aec import DTLNResidualFilter
    f = DTLNResidualFilter(model_path=_MODEL)
    mic, far, _ = _synth_echo_fixture()
    for _ in range(120):
        f.process(mic, far)
    assert f.last_p95_ms() < 8.0
    assert f.enabled is True   # didn't self-disable under budget


def test_self_disable_on_budget_breach(monkeypatch):
    """A slow model self-disables after the measurement window."""
    monkeypatch.setenv("JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS", "0.001")
    from audio.dtln_aec import DTLNResidualFilter
    f = DTLNResidualFilter(model_path=_MODEL if _MODEL.exists() else None,
                           _force_enabled_for_test=True)
    mic, far, _ = _synth_echo_fixture()
    for _ in range(120):
        f.process(mic, far)
    assert f.enabled is False   # exceeded 0.001ms budget → disabled
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_dtln_residual.py -v
```

Expected: FAIL (module missing). Model-gated tests skip if no model.

- [ ] **Step 3: Create the module**

Create `src/voice-agent/audio/dtln_aec.py`:

```python
"""DTLN-aec neural residual filter (L3).

Runs a pre-trained DTLN-aec ONNX model (16 kHz, 160-sample frames) as
a NON-LINEAR residual after the linear AEC layers (PipeWire L1 + APM
L2). Catches cheap-laptop-speaker distortion that linear AEC misses.

Self-disables for the rest of the session if p95 per-frame latency
exceeds JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS (default 8) over a 100-
frame window — the neural layer must never degrade the mic path.

Spec: docs/superpowers/specs/2026-05-19-echo-cancellation-cascade-design.md §5.3
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("jarvis.audio.dtln_aec")

_DEFAULT_MODEL = Path(__file__).parent.parent / "models" / "dtln_aec_128.onnx"


class DTLNResidualFilter:
    def __init__(
        self,
        model_path: Optional[Path] = None,
        *,
        _force_enabled_for_test: bool = False,
    ) -> None:
        self._lat: deque[float] = deque(maxlen=100)
        self._p95 = 0.0
        self.enabled = False
        self._session = None
        try:
            self._budget_ms = float(os.environ.get("JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS", "8"))
        except ValueError:
            self._budget_ms = 8.0

        path = model_path if model_path is not None else _DEFAULT_MODEL
        if _force_enabled_for_test:
            self.enabled = True
            return
        if path is None or not Path(path).exists():
            logger.warning(f"[dtln] model not found at {path}; L3 disabled")
            return
        try:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 1   # don't contend with LLM threads
            opts.inter_op_num_threads = 1
            self._session = ort.InferenceSession(
                str(path), sess_options=opts, providers=["CPUExecutionProvider"]
            )
            self.enabled = True
            logger.info(f"[dtln] loaded {path.name}; budget {self._budget_ms}ms")
        except Exception as e:
            logger.warning(f"[dtln] load failed ({e}); L3 disabled")

    def last_p95_ms(self) -> float:
        return self._p95

    def _record_latency(self, ms: float) -> None:
        self._lat.append(ms)
        if len(self._lat) >= 100:
            self._p95 = float(np.percentile(np.array(self._lat), 95))
            if self._p95 > self._budget_ms and self.enabled:
                self.enabled = False
                logger.warning(
                    f"[dtln] p95 {self._p95:.2f}ms > budget {self._budget_ms}ms "
                    f"— self-disabling L3 for this session"
                )

    def process(self, mic16k: np.ndarray, ref16k: np.ndarray) -> np.ndarray:
        """Residual-filter one 160-sample 16kHz frame. Returns the mic
        unchanged when disabled or on any error (fail-safe)."""
        if not self.enabled:
            return mic16k
        t0 = time.perf_counter()
        try:
            if self._session is None:
                # _force_enabled_for_test path: simulate work, no real model.
                out = mic16k
            else:
                # NOTE: the exact input/output node names + shapes depend
                # on the specific DTLN-aec ONNX export. At implementation
                # time, inspect with: [i.name for i in session.get_inputs()].
                # The canonical dtln_aec_128 takes mic + far-end (lpb) and
                # returns the cleaned near-end. Adapt the feed dict to the
                # actual node names discovered.
                feeds = {
                    self._session.get_inputs()[0].name: mic16k.reshape(1, -1),
                    self._session.get_inputs()[1].name: ref16k.reshape(1, -1),
                }
                out = self._session.run(None, feeds)[0].reshape(-1).astype(np.float32)
        except Exception as e:
            logger.debug(f"[dtln] process failed ({e}); passing mic through")
            out = mic16k
        finally:
            self._record_latency((time.perf_counter() - t0) * 1000.0)
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_dtln_residual.py -v
```

Expected: the no-model + self-disable tests pass; ERLE/latency tests pass IF the model is present (skip otherwise). At implementation time, download the model (Task 9) so the model-gated tests actually run, and adapt the ONNX feed-dict node names to the real export (see the NOTE in Step 3).

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/audio/dtln_aec.py src/voice-agent/tests/test_dtln_residual.py
git commit -m "feat(aec): L3 — DTLNResidualFilter (neural residual @ 16kHz)

onnxruntime CPU session (single-threaded, no LLM-thread contention),
160-sample 16kHz frames. Non-linear residual after L1+L2 linear AEC.
Self-disables for the session if p95 latency > budget (default 8ms)
over a 100-frame window — never degrades the mic path. Fail-safe:
passes mic through unchanged on missing model / any error. Per spec
2026-05-19 §5.3."
```

---

## Task 11: Wire L3 into the mic path + 16k publish (integration)

**Files:**
- Modify: `src/voice-agent/jarvis_voice_client.py`
- Test: extend `src/voice-agent/tests/test_mic_path_layers.py`

- [ ] **Step 1: Write the failing test extension**

Append to `src/voice-agent/tests/test_mic_path_layers.py`:

```python
def test_l3_gating_by_profile():
    """L3 (DTLN) runs only on speakers, only when JARVIS_NEURAL_AEC=1."""
    import jarvis_voice_client as vc
    assert vc._l3_should_run(profile="speakers", neural_aec=True) is True
    assert vc._l3_should_run(profile="headphones", neural_aec=True) is False
    assert vc._l3_should_run(profile="speakers", neural_aec=False) is False
    assert vc._l3_should_run(profile="unknown", neural_aec=True) is True  # conservative
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_mic_path_layers.py::test_l3_gating_by_profile -v
```

Expected: FAIL (`_l3_should_run` missing).

- [ ] **Step 3: Add the L3 gating helper + wire into _mic_cb**

In `jarvis_voice_client.py`, ADD the gating helper near `_should_publish_during_speak`:

```python
def _l3_should_run(*, profile: str, neural_aec: bool) -> bool:
    """L3 (DTLN residual) runs only when neural AEC is enabled AND the
    output is NOT headphones (headphones have no echo path → no residual
    to filter). 'unknown' is treated conservatively as speakers."""
    if not neural_aec:
        return False
    return profile != "headphones"
```

ADD the DTLN singleton near the APM block:

```python
from audio.dtln_aec import DTLNResidualFilter
_dtln = DTLNResidualFilter()
```

In `_mic_cb`, AFTER `_apm.process_stream(frame)` and BEFORE `source.capture_frame`, ADD the L3 stage. NOTE this is where the 48k→16k publish-format decision lands:

```python
        # 2026-05-19 L3 — neural residual @ 16kHz (speakers only).
        # Also the point where we switch the published format to 16kHz
        # (STT consumes 16kHz; Orpheus playback is separate + stays 48k).
        _neural = os.environ.get("JARVIS_NEURAL_AEC", "1") == "1"
        from audio.output_profile import classify_output_device
        _profile = classify_output_device()
        # Downsample mic 48k→16k for publish + DTLN.
        mic48 = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
        from scipy.signal import resample_poly
        mic16 = resample_poly(mic48, up=1, down=3).astype(np.float32)
        if _l3_should_run(profile=_profile, neural_aec=_neural) and _dtln.enabled:
            ref16 = _reverse_ringbuf.read_16k_aligned(time.monotonic())
            mic16 = _dtln.process(mic16, ref16)
        pub = (np.clip(mic16, -1.0, 1.0) * 32767.0).astype(np.int16)
        frame16 = rtc.AudioFrame(
            data=pub.tobytes(), sample_rate=16_000, num_channels=1,
            samples_per_channel=len(pub),
        )
```

Then change the `source.capture_frame(frame)` call to publish `frame16`, and ensure the `rtc.AudioSource` + track were created at 16 kHz (find the `AudioSource(SAMPLE_RATE, ...)` construction and add a publish-rate constant `PUBLISH_RATE = 16_000`; the SOURCE that feeds the SFU should use `PUBLISH_RATE`, while the mic InputStream still opens at 48 kHz to match the echo-cancel device).

Update `_write_aec_state_snapshot` (Task 7 Step 4) to fill `dtln_latency_ms_p95=_dtln.last_p95_ms()`.

- [ ] **Step 4: Run test + import sanity + full suite**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_mic_path_layers.py -v
.venv/bin/python -c "import jarvis_voice_client" 2>&1 | tail -2
.venv/bin/python -m pytest tests/ --timeout=60 -q \
  --deselect tests/test_browser_subagent.py::test_browser_spec_loads_all_ext_tools \
  --deselect tests/test_evolution_batch_miner.py::test_mine_returns_proposals_from_telemetry 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 5: Validate the 16k publish against the SFU + agent STT**

This is a runtime check, not a unit test. After restarting the voice-agent + voice-client, confirm in the logs that the agent's STT receives the 16 kHz track without sample-rate errors. If the SFU/agent rejects 16 kHz, fall back to option 2a (keep 48 kHz publish + inline 16k→48k resample after DTLN). Document which path was taken.

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/jarvis_voice_client.py src/voice-agent/tests/test_mic_path_layers.py
git commit -m "feat(aec): L3 — wire DTLN into mic path + 16k publish

_mic_cb runs the DTLN residual on speakers (gated by
_l3_should_run + _dtln.enabled), feeding the ring-buffer reference.
Mic publish format switches 48k→16k (STT consumes 16k; Orpheus
playback stays 48k on the separate output path). AEC state snapshot
now reports dtln_latency_ms_p95. Per spec 2026-05-19 §5.3/§6.1.

If the SFU rejects 16k publish, fall back to 48k + inline resample
(spec §7 / option 2a) — documented at runtime validation."
```

---

## Self-Review

### Spec coverage
| Spec requirement (§) | Task |
|---|---|
| §5.1 L1 PipeWire tuning + reload helper | Task 6 |
| §5.2 APM reverse-stream + delay estimator + ring buffer + remove mic-drop | Tasks 3, 4, 7 |
| §5.3 DTLN @ 16kHz + model download + 16k publish | Tasks 9, 10, 11 |
| §5.4 output-device auto-detect + watcher | Task 2 |
| §5.5 cross-process telemetry + columns + soak | Tasks 1, 5, 8 |
| §6.1 mic capture flow | Tasks 7, 11 |
| §6.2 playback reference feed | Task 7 |
| §6.3 barge-in | Task 7 (`_should_publish_during_speak`) |
| §6.4 hot-plug | Task 2 (`watch_for_changes`) |
| §7 error handling (model missing, latency breach, stale file, etc.) | Tasks 5, 10 (graceful paths) |
| §8 A1-A9 acceptance | distributed; A3 echo-loop = Task 8; A4 latency = Task 10 |

All spec sections mapped. No gaps.

### Placeholder scan
One INTENTIONAL placeholder: `DTLN_SHA256="REPLACE_WITH_PINNED_SHA256_AT_IMPLEMENTATION_TIME"` in Task 9 — flagged explicitly as a step-time action (download model, compute hash, pin it) because the hash can't be known until the model is fetched. This is a runtime artifact, not a plan-gap. The ONNX node-name adaptation in Task 10 is similarly flagged as an inspect-at-implementation step (the model's exact I/O names aren't knowable from the plan). All other steps have complete code.

### Type consistency
- `classify_output_device() -> Profile` ("headphones"/"speakers"/"unknown") — consistent across Tasks 2, 7, 11.
- `APMDelayEstimator.current_delay_ms() -> int`, `note_output/note_input(float)` — Tasks 3, 7.
- `ReverseRefRingBuffer.write(np.ndarray, dac_ts)`, `read_16k_aligned(input_ts) -> np.ndarray(160)` — Tasks 4, 7, 11.
- `write_aec_state(...)` / `read_aec_state(...) -> dict` with the 6 column keys — Tasks 5, 7; agent reader maps to the same column names as Task 1's migration.
- `DTLNResidualFilter.process(mic16k, ref16k) -> np.ndarray`, `.enabled`, `.last_p95_ms()` — Tasks 10, 11.
- Telemetry columns `aec_layer{1,2,3}_active` / `output_profile` / `apm_delay_ms_p50` / `dtln_latency_ms_p95` — identical names in Tasks 1, 5, 8.

All consistent.

### Final tally
- 11 tasks, 8 in Phase 1 (independently shippable — restores barge-in) + 3 in Phase 2 (neural, A/B-gated).
- ~6 new modules + 3 modified + 2 bin scripts + 7 test files.
- TDD throughout; one commit per task; kill-switches per layer; degradation ladder bottoms at today's behavior.

---

## Plan complete and saved to `docs/superpowers/plans/2026-05-19-echo-cancellation-cascade.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task + two-stage review. Best for the integration tasks (7, 11) where the voice-client wiring needs care.

2. **Inline Execution** — execute in this session via executing-plans, batch with checkpoints.

Which approach?

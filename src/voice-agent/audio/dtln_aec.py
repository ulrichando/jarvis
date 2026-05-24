"""DTLN-aec L3 neural echo residual filter (Phase B Task 8).

Wraps the two-stage DTLN-aec 128 TFLite models (Westhausen et al., 2020)
behind a streaming `process(mic_frame, ref_frame)` API the realtime
mic-path consumer can call with arbitrary-length frames.

Algorithm — ported faithfully from upstream `run_aec.py`:
- Two TFLite interpreters in cascade. Single-threaded for predictable
  per-frame latency.
- Streaming overlap-add at 512-sample analysis window / 128-sample hop
  @ 16 kHz (per upstream BLOCK_LEN / BLOCK_SHIFT). Mic + reference each
  maintain a 512-sample shift-register; the synthesis buffer accumulates
  the stage-2 time-domain outputs with overlap-add.
- Stage 1 (`dtln_aec_128_1.tflite`):
    in:  input_3 [1,1,257] mic STFT magnitude
         input_4 [1,1,257] reference STFT magnitude
         input_5 [1,2,128,2] LSTM state
    out: Identity [1,1,257] mask (applied to mic STFT, then iFFT)
         Identity_1 [1,2,128,2] next LSTM state
- Stage 2 (`dtln_aec_128_2.tflite`):
    in:  input_6 [1,1,512] estimated waveform from stage 1
         input_7 [1,1,512] reference waveform (time-domain, full window)
         input_8 [1,2,128,2] LSTM state
    out: Identity [1,1,512] cleaned waveform
         Identity_1 [1,2,128,2] next LSTM state

Output is delayed ~448 samples (28 ms @ 16 kHz) relative to input — that's
intrinsic to the 512/128 overlap-add and is handled by the consumer's
existing `apm_reverse_stream` delay estimator on the reference side.

Health / self-disable:
- Per-`process()` p95 latency tracked over the last 200 calls (deque).
  Once ≥50 samples, computed on `.healthy` / `.p95_ms` access.
- p95 > budget (`JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS`, default 8.0) →
  filter sets `_disabled = True`, logs ONCE, never re-enables that session.
- Any exception during inference → log WARNING (once), set `_disabled`,
  fall through to passthrough. NEVER raise into the caller's audio cb.

SHA-verification of both .tflite artifacts at construction (defense
against a swapped/corrupted model file).

Spec: docs/superpowers/specs/2026-05-20-aec-cascade-completion-design.md §5.2
Plan: docs/superpowers/plans/2026-05-20-aec-cascade-completion.md Task 8
Upstream: https://github.com/breizhn/DTLN-aec  (run_aec.py)
"""
from __future__ import annotations

import collections
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("jarvis.audio.dtln_aec")

# ─── upstream constants (do not change without retraining the model) ─────────
BLOCK_LEN: int = 512        # FFT / analysis window in samples
BLOCK_SHIFT: int = 128      # hop (also = synthesis frame produced per inference)
SAMPLE_RATE: int = 16000

# Filesystem layout: <repo>/src/voice-agent/audio/ + ../models/
MODELS_DIR: Path = Path(__file__).resolve().parent.parent / "models"

# Pinned artifact hashes — must match the .tflite files actually present.
EXPECTED_SHA: dict[str, str] = {
    "dtln_aec_128_1.tflite": "8d241b3a732af8ca140b2e30043e56a6c3c7800c46e22c26f4eea2f70974ad1e",
    "dtln_aec_128_2.tflite": "350bb01a1152ae3cabe09fe5e868ef2f7d8b988a9f22aae44f140195f6493126",
}

LATENCY_BUDGET_MS_DEFAULT: float = 8.0

# LSTM state shape, identical for both stages: [1, 2, 128, 2]
_STATE_SHAPE: tuple[int, int, int, int] = (1, 2, 128, 2)

# Latency-deque parameters.
_LAT_WINDOW: int = 200          # max samples retained
_LAT_MIN_FOR_P95: int = 50      # below this, `p95_ms` is None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class DTLNResidualFilter:
    """L3 neural echo residual cancellation (DTLN-aec 128 model).

    Stateful streaming filter. Consumer calls `process(mic_frame, ref_frame)`
    with same-length 16 kHz int16 OR float32 ndarrays (any chunk size —
    buffers internally to the model's 512/128 streaming convention) and
    receives a cleaned mic frame of the same length back.

    Self-disables if per-frame inference latency p95 exceeds the budget.
    """

    # Class-level type hint so static analyzers + the spec contract agree.
    p95_ms: Optional[float]  # populated lazily

    def __init__(
        self,
        *,
        model_dir: Path | str = MODELS_DIR,
        latency_budget_ms: Optional[float] = None,
        verify_sha: bool = True,
    ) -> None:
        self._model_dir: Path = Path(model_dir)
        # Budget resolution: explicit arg → env var → default.
        if latency_budget_ms is not None:
            self._budget_ms = float(latency_budget_ms)
        else:
            try:
                self._budget_ms = float(
                    os.environ.get(
                        "JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS",
                        str(LATENCY_BUDGET_MS_DEFAULT),
                    )
                )
            except ValueError:
                self._budget_ms = LATENCY_BUDGET_MS_DEFAULT

        # --- state -------------------------------------------------------
        self._disabled: bool = False
        self._frames_processed: int = 0
        self._lat_ns: collections.deque[int] = collections.deque(maxlen=_LAT_WINDOW)
        self._error_logged: bool = False
        self._latency_logged: bool = False
        # Lazily-computed cache for `p95_ms`; invalidated whenever a new
        # measurement is recorded. Keeps the property cheap on repeat reads.
        self._p95_cache: Optional[float] = None
        self._p95_dirty: bool = True
        # Public name for the property's backing — declared above; populated
        # via the property accessor, no separate attribute needed.

        # Streaming shift-registers (initially zero) + overlap-add buffer.
        self._mic_window: np.ndarray = np.zeros(BLOCK_LEN, dtype=np.float32)
        self._ref_window: np.ndarray = np.zeros(BLOCK_LEN, dtype=np.float32)
        self._synth_buffer: np.ndarray = np.zeros(BLOCK_LEN, dtype=np.float32)

        # Per-call input accumulators (caller may push !=128 samples).
        self._mic_in: collections.deque[np.ndarray] = collections.deque()
        self._mic_in_len: int = 0
        self._ref_in: collections.deque[np.ndarray] = collections.deque()
        self._ref_in_len: int = 0
        # Output queue. Pre-seeded with zeros so warm-up returns same-length
        # frames without errors; the algorithmic delay (~448 samples) is then
        # naturally reflected in the consumer's signal.
        self._out: collections.deque[np.ndarray] = collections.deque()
        self._out_len: int = 0

        # LSTM states for each interpreter.
        self._states_1: np.ndarray = np.zeros(_STATE_SHAPE, dtype=np.float32)
        self._states_2: np.ndarray = np.zeros(_STATE_SHAPE, dtype=np.float32)

        # --- model load --------------------------------------------------
        # Verify SHAs BEFORE the (slow) Interpreter load. Raises on mismatch.
        if verify_sha:
            self._verify_shas()

        self._stage1, self._s1_in, self._s1_out = self._load_interpreter("dtln_aec_128_1.tflite")
        self._stage2, self._s2_in, self._s2_out = self._load_interpreter("dtln_aec_128_2.tflite")

    # ─────────────────────────── public API ──────────────────────────────

    @property
    def healthy(self) -> bool:
        """True iff the filter is loaded + has not self-disabled. Cheap; the
        latency check fires inside `process()`, not on every read."""
        return not self._disabled

    @property
    def p95_ms(self) -> Optional[float]:  # type: ignore[override]
        """95th-percentile per-frame latency over the most recent
        `_LAT_WINDOW` `process()` calls. `None` until at least
        `_LAT_MIN_FOR_P95` samples are recorded. Recomputed only when new
        samples landed since the last read."""
        if len(self._lat_ns) < _LAT_MIN_FOR_P95:
            return None
        if self._p95_dirty:
            arr = np.fromiter(self._lat_ns, dtype=np.int64, count=len(self._lat_ns))
            self._p95_cache = float(np.percentile(arr, 95)) / 1e6
            self._p95_dirty = False
        return self._p95_cache

    @property
    def frames_processed(self) -> int:
        return self._frames_processed

    def reset_state(self) -> None:
        """Zero LSTM states + analysis/synthesis buffers + input accumulators.
        Call on session restart / device change. Latency stats are preserved
        (they're a property of the host, not the session)."""
        self._states_1.fill(0.0)
        self._states_2.fill(0.0)
        self._mic_window.fill(0.0)
        self._ref_window.fill(0.0)
        self._synth_buffer.fill(0.0)
        self._mic_in.clear(); self._mic_in_len = 0
        self._ref_in.clear(); self._ref_in_len = 0
        self._out.clear(); self._out_len = 0

    def process(self, mic_frame: np.ndarray, ref_frame: np.ndarray) -> np.ndarray:
        """Run one streaming step. Same dtype + length out as `mic_frame` in.

        Passthrough (returns `mic_frame` unchanged) whenever the filter is
        not healthy. NEVER raises into the caller — any inference exception
        self-disables the filter and falls through to passthrough.
        """
        if self._disabled:
            return mic_frame

        # Bookkeeping for caller — every call counts, even if it produces no
        # output yet (warm-up) or self-disables in this call.
        self._frames_processed += 1

        t0 = time.monotonic_ns()
        try:
            output = self._process_inner(mic_frame, ref_frame)
        except Exception as e:
            if not self._error_logged:
                logger.warning(
                    f"[dtln] inference failed ({type(e).__name__}: {e}); "
                    f"L3 self-disabled for the rest of this session"
                )
                self._error_logged = True
            self._disabled = True
            return mic_frame

        # Record latency (single int64 — cheap append).
        dt_ns = time.monotonic_ns() - t0
        self._lat_ns.append(dt_ns)
        self._p95_dirty = True

        # Latency self-disable check. Only kicks in once we have enough
        # samples to make `p95_ms` non-None; reading the property handles
        # that gate. We deliberately use the public property (which uses
        # the cache) — recomputing here would defeat the cache.
        p95 = self.p95_ms
        if p95 is not None and p95 > self._budget_ms:
            if not self._latency_logged:
                logger.warning(
                    f"[dtln] p95 {p95:.2f}ms > budget {self._budget_ms:.2f}ms "
                    f"after {len(self._lat_ns)} frames; L3 self-disabled "
                    f"for the rest of this session"
                )
                self._latency_logged = True
            self._disabled = True
            # The cleaned output for THIS frame is still valid — return it.
            # Subsequent calls will short-circuit to passthrough.

        return output

    # ─────────────────────────── internals ───────────────────────────────

    def _verify_shas(self) -> None:
        """Hash both .tflite files; raise RuntimeError on any mismatch."""
        for name, expected in EXPECTED_SHA.items():
            path = self._model_dir / name
            if not path.is_file():
                raise RuntimeError(
                    f"DTLN model file not found: {path}. "
                    f"Expected SHA-256 {expected}."
                )
            actual = _sha256(path)
            if actual != expected:
                raise RuntimeError(
                    f"DTLN model SHA-256 mismatch for {path.name}: "
                    f"expected {expected}, got {actual}. "
                    f"Refusing to load — a swapped model could produce silent "
                    f"audio quality regressions."
                )

    def _load_interpreter(self, fname: str):
        """Load a TFLite interpreter and return (interp, in_idx, out_idx).

        `in_idx` / `out_idx` are name→tensor-index dicts so we don't depend
        on the (interpreter-dependent) declaration order of `get_input_details()`.
        """
        from ai_edge_litert.interpreter import Interpreter

        path = self._model_dir / fname
        interp = Interpreter(model_path=str(path), num_threads=1)
        interp.allocate_tensors()
        in_idx = {d["name"]: d["index"] for d in interp.get_input_details()}
        out_idx = {d["name"]: d["index"] for d in interp.get_output_details()}
        return interp, in_idx, out_idx

    def _process_inner(self, mic_frame: np.ndarray, ref_frame: np.ndarray) -> np.ndarray:
        """Core streaming step. Buffers caller input by 128-sample hops,
        runs zero-or-more inferences, returns same-length cleaned audio."""
        if mic_frame.shape != ref_frame.shape:
            raise ValueError(
                f"mic_frame and ref_frame must be same shape; "
                f"got {mic_frame.shape} vs {ref_frame.shape}"
            )
        n = mic_frame.shape[0]
        if n == 0:
            return mic_frame  # nothing to do, preserve dtype

        # Remember original dtype so we can convert back on return.
        orig_dtype = mic_frame.dtype
        mic_f32, ref_f32 = self._to_float32(mic_frame), self._to_float32(ref_frame)

        # Push into input accumulators.
        self._mic_in.append(mic_f32); self._mic_in_len += n
        self._ref_in.append(ref_f32); self._ref_in_len += n

        # Drain as many 128-sample hops as we have on BOTH sides.
        while self._mic_in_len >= BLOCK_SHIFT and self._ref_in_len >= BLOCK_SHIFT:
            mic_hop = self._pop_samples(self._mic_in, BLOCK_SHIFT, is_mic=True)
            ref_hop = self._pop_samples(self._ref_in, BLOCK_SHIFT, is_mic=False)
            hop_out = self._run_one_hop(mic_hop, ref_hop)
            self._out.append(hop_out); self._out_len += BLOCK_SHIFT

        # Take `n` samples from the output queue; pad with zeros at the FRONT
        # while warming up (i.e. when output queue is short).
        out_f32 = self._take_output(n)

        return self._from_float32(out_f32, orig_dtype)

    @staticmethod
    def _to_float32(x: np.ndarray) -> np.ndarray:
        if x.dtype == np.float32:
            return x
        if x.dtype == np.int16:
            return (x.astype(np.float32) / 32768.0)
        # Other dtypes: convert (best-effort) without scaling.
        return x.astype(np.float32, copy=False)

    @staticmethod
    def _from_float32(x: np.ndarray, target_dtype: np.dtype) -> np.ndarray:
        if target_dtype == np.float32:
            return x
        if target_dtype == np.int16:
            return np.clip(x * 32768.0, -32768, 32767).astype(np.int16)
        return x.astype(target_dtype, copy=False)

    def _pop_samples(
        self, q: "collections.deque[np.ndarray]", k: int, *, is_mic: bool
    ) -> np.ndarray:
        """Pop exactly `k` samples from a deque of np.float32 chunks.
        Updates the matching length counter."""
        gathered: list[np.ndarray] = []
        remaining = k
        while remaining > 0:
            head = q[0]
            if head.shape[0] <= remaining:
                gathered.append(head)
                remaining -= head.shape[0]
                q.popleft()
            else:
                gathered.append(head[:remaining])
                # Replace head with the unread tail.
                q[0] = head[remaining:]
                remaining = 0
        out = np.concatenate(gathered) if len(gathered) > 1 else gathered[0]
        if is_mic:
            self._mic_in_len -= k
        else:
            self._ref_in_len -= k
        return out.astype(np.float32, copy=False)

    def _take_output(self, n: int) -> np.ndarray:
        """Pop exactly `n` samples from the output queue; pad with leading
        zeros while warming up. Updates `_out_len` accordingly."""
        if self._out_len >= n:
            return self._pop_output(n)
        # Warm-up path: produce (n - _out_len) zeros at the front + drain
        # whatever is in the queue at the back.
        missing = n - self._out_len
        head = np.zeros(missing, dtype=np.float32)
        if self._out_len == 0:
            return head
        tail = self._pop_output(self._out_len)
        return np.concatenate((head, tail))

    def _pop_output(self, k: int) -> np.ndarray:
        """Same as `_pop_samples(self._out, k, is_mic=False)` but bumps the
        right counter (we manage three deques + lengths, so a custom helper
        keeps the bookkeeping local)."""
        gathered: list[np.ndarray] = []
        remaining = k
        while remaining > 0:
            head = self._out[0]
            if head.shape[0] <= remaining:
                gathered.append(head)
                remaining -= head.shape[0]
                self._out.popleft()
            else:
                gathered.append(head[:remaining])
                self._out[0] = head[remaining:]
                remaining = 0
        self._out_len -= k
        return np.concatenate(gathered) if len(gathered) > 1 else gathered[0]

    def _run_one_hop(self, mic_hop_128: np.ndarray, ref_hop_128: np.ndarray) -> np.ndarray:
        """Run ONE 128-sample-hop inference. Updates the analysis windows,
        runs both interpreters, advances the synthesis buffer, returns the
        cleaned 128-sample slice for this hop. Carries LSTM state across calls.

        Faithful port of the per-block loop body in upstream `run_aec.py`.
        """
        # 1. Shift analysis windows by BLOCK_SHIFT, append the new 128 samples.
        self._mic_window[:-BLOCK_SHIFT] = self._mic_window[BLOCK_SHIFT:]
        self._mic_window[-BLOCK_SHIFT:] = mic_hop_128
        self._ref_window[:-BLOCK_SHIFT] = self._ref_window[BLOCK_SHIFT:]
        self._ref_window[-BLOCK_SHIFT:] = ref_hop_128

        # 2. Stage 1 — magnitude-domain LSTM mask.
        mic_fft = np.fft.rfft(self._mic_window).astype(np.complex64)
        mic_mag = np.abs(mic_fft).reshape(1, 1, -1).astype(np.float32)
        ref_fft = np.fft.rfft(self._ref_window).astype(np.complex64)
        ref_mag = np.abs(ref_fft).reshape(1, 1, -1).astype(np.float32)

        self._stage1.set_tensor(self._s1_in["input_3"], mic_mag)
        self._stage1.set_tensor(self._s1_in["input_4"], ref_mag)
        self._stage1.set_tensor(self._s1_in["input_5"], self._states_1)
        self._stage1.invoke()
        mask = self._stage1.get_tensor(self._s1_out["Identity"])
        self._states_1 = self._stage1.get_tensor(self._s1_out["Identity_1"])

        # Apply mask in STFT domain; iFFT back to time-domain estimate.
        estimated = np.fft.irfft(mic_fft * mask).astype(np.float32)
        estimated = estimated.reshape(1, 1, -1)
        ref_window_tensor = self._ref_window.reshape(1, 1, -1).astype(np.float32)

        # 3. Stage 2 — time-domain residual cleanup with full 512-sample ref.
        self._stage2.set_tensor(self._s2_in["input_6"], estimated)
        self._stage2.set_tensor(self._s2_in["input_7"], ref_window_tensor)
        self._stage2.set_tensor(self._s2_in["input_8"], self._states_2)
        self._stage2.invoke()
        cleaned = self._stage2.get_tensor(self._s2_out["Identity"])
        self._states_2 = self._stage2.get_tensor(self._s2_out["Identity_1"])

        # 4. Overlap-add into synthesis buffer; pop the front BLOCK_SHIFT.
        self._synth_buffer[:-BLOCK_SHIFT] = self._synth_buffer[BLOCK_SHIFT:]
        self._synth_buffer[-BLOCK_SHIFT:] = 0.0
        self._synth_buffer += cleaned.reshape(-1)
        # COPY — synth_buffer is mutated in place by the next call.
        return self._synth_buffer[:BLOCK_SHIFT].copy()


__all__ = [
    "DTLNResidualFilter",
    "MODELS_DIR",
    "EXPECTED_SHA",
    "BLOCK_LEN",
    "BLOCK_SHIFT",
    "SAMPLE_RATE",
    "LATENCY_BUDGET_MS_DEFAULT",
]

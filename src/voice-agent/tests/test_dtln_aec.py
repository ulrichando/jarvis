"""Tests for `audio/dtln_aec.py::DTLNResidualFilter` (Phase B Task 8).

Exercise: SHA verification, dtype/length contract, state persistence,
reset, latency self-disable, passthrough when disabled, AEC smoke,
and the p95 lifecycle. Tests run against the real models — they're
small (≈1 MB each), CPU-only, and tracked in the repo.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from audio import dtln_aec  # noqa: E402  (after sys.path mutation)
from audio.dtln_aec import (  # noqa: E402
    BLOCK_LEN,
    BLOCK_SHIFT,
    DTLNResidualFilter,
)


# ───────────────────────── 1. load ─────────────────────────────────────


def test_loads_successfully() -> None:
    """Default-arg instantiation succeeds; healthy + zero frames + no p95
    yet (latency window is empty)."""
    f = DTLNResidualFilter()
    assert f.healthy is True
    assert f.frames_processed == 0
    assert f.p95_ms is None


# ───────────────────────── 2. SHA mismatch ─────────────────────────────


def test_sha_mismatch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A swapped/tampered model file must refuse to load. Error message
    must surface BOTH expected and actual hashes for forensics."""
    bad_sha = "0" * 64
    monkeypatch.setitem(dtln_aec.EXPECTED_SHA, "dtln_aec_128_1.tflite", bad_sha)
    with pytest.raises(RuntimeError) as exc:
        DTLNResidualFilter()
    msg = str(exc.value)
    assert bad_sha in msg, f"expected hash absent from error: {msg!r}"
    # Real hash from the on-disk file (the "actual" the loader saw).
    assert "8d241b3a732af8ca140b2e30043e56a6c3c7800c46e22c26f4eea2f70974ad1e" in msg, (
        f"actual hash absent from error: {msg!r}"
    )


# ─────────────────── 3. dtype / length contract ────────────────────────


def test_process_same_dtype_and_length_float32() -> None:
    """160-sample float32 in → 160-sample float32 out (same shape, dtype)."""
    f = DTLNResidualFilter()
    inp = np.random.randn(160).astype(np.float32) * 0.1
    ref = np.random.randn(160).astype(np.float32) * 0.1
    out = f.process(inp, ref)
    assert out.shape == inp.shape
    assert out.dtype == np.float32


def test_process_same_dtype_and_length_int16() -> None:
    """160-sample int16 in → 160-sample int16 out (same shape, dtype)."""
    f = DTLNResidualFilter()
    inp = (np.random.randn(160) * 3000).astype(np.int16)
    ref = (np.random.randn(160) * 3000).astype(np.int16)
    out = f.process(inp, ref)
    assert out.shape == inp.shape
    assert out.dtype == np.int16


# ─────────────────── 4. state persistence ──────────────────────────────


def test_state_persists_across_frames() -> None:
    """LSTM state arrays diverge from the initial zero state after enough
    frames to trigger ≥1 inference hop. Both stages must update."""
    f = DTLNResidualFilter()
    initial_s1 = f._states_1.copy()
    initial_s2 = f._states_2.copy()
    # 10 × 160-sample frames = 1600 samples; with 128-hop, that's ≥12 hops,
    # so the state definitely updates.
    for _ in range(10):
        mic = np.random.randn(160).astype(np.float32) * 0.1
        ref = np.random.randn(160).astype(np.float32) * 0.1
        f.process(mic, ref)
    assert not np.array_equal(f._states_1, initial_s1), "stage1 LSTM state never updated"
    assert not np.array_equal(f._states_2, initial_s2), "stage2 LSTM state never updated"


# ─────────────────── 5. reset_state ────────────────────────────────────


def test_reset_state_zeros_lstm() -> None:
    """`reset_state()` zeros both LSTM states + analysis/synthesis buffers."""
    f = DTLNResidualFilter()
    for _ in range(10):
        mic = np.random.randn(160).astype(np.float32) * 0.1
        ref = np.random.randn(160).astype(np.float32) * 0.1
        f.process(mic, ref)
    # Pre-condition: states are non-zero.
    assert np.any(f._states_1 != 0.0)
    assert np.any(f._states_2 != 0.0)
    f.reset_state()
    assert np.array_equal(f._states_1, np.zeros_like(f._states_1))
    assert np.array_equal(f._states_2, np.zeros_like(f._states_2))
    assert np.array_equal(f._mic_window, np.zeros_like(f._mic_window))
    assert np.array_equal(f._ref_window, np.zeros_like(f._ref_window))
    assert np.array_equal(f._synth_buffer, np.zeros_like(f._synth_buffer))


# ─────────────────── 6. latency self-disable ───────────────────────────


def test_self_disables_on_latency_breach() -> None:
    """With an impossibly tight budget, the filter must self-disable once
    enough latency samples have been collected (≥50)."""
    f = DTLNResidualFilter(latency_budget_ms=0.001)
    assert f.healthy is True  # not breached yet
    # 60 × 160-sample frames is enough hops to fill the latency window past
    # the 50-sample minimum; the very next p95 check will fire the disable.
    for _ in range(60):
        mic = np.random.randn(160).astype(np.float32) * 0.1
        ref = np.random.randn(160).astype(np.float32) * 0.1
        f.process(mic, ref)
    assert f.healthy is False, (
        f"filter should be disabled after latency breach; "
        f"p95_ms={f.p95_ms!r}, budget=0.001"
    )
    # Subsequent calls must passthrough — byte-identical input/output.
    test_in = np.random.randn(160).astype(np.float32)
    test_ref = np.zeros(160, dtype=np.float32)
    test_out = f.process(test_in, test_ref)
    np.testing.assert_array_equal(test_out, test_in)


# ─────────────────── 7. passthrough when disabled ──────────────────────


def test_passthrough_when_disabled() -> None:
    """Manually disabling the filter makes `process()` an identity function
    — output is the same numpy object the caller passed in."""
    f = DTLNResidualFilter()
    f._disabled = True
    test_in = (np.random.randn(160) * 5000).astype(np.int16)
    test_ref = np.zeros(160, dtype=np.int16)
    out = f.process(test_in, test_ref)
    # Byte-identical: same shape, dtype, and values.
    np.testing.assert_array_equal(out, test_in)
    assert out.dtype == test_in.dtype


# ─────────────────── 8. AEC smoke (not byte-identical) ─────────────────


def test_reduces_simulated_echo() -> None:
    """Smoke test: model is actually computing AEC, not silently passing
    audio through. With a pure-tone reference and (tone + speech-like
    noise) mic, after warm-up the output should NOT equal the input
    (the algorithm subtracted SOMETHING) AND the energy attributable to
    the reference should be reduced.

    Tolerance is generous because this is a synthetic signal the model
    wasn't trained on — the real test is just "did anything happen".
    """
    rng = np.random.default_rng(42)
    fs = 16000
    n = fs  # 1 second
    t = np.arange(n) / fs
    ref = (0.3 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    noise = (0.05 * rng.standard_normal(n)).astype(np.float32)
    mic = (ref + noise).astype(np.float32)

    f = DTLNResidualFilter()
    out = np.zeros(n, dtype=np.float32)
    # Chunk by 160-sample (10 ms) frames — the realistic call shape.
    chunk = 160
    for i in range(0, n - chunk + 1, chunk):
        out[i:i + chunk] = f.process(mic[i:i + chunk], ref[i:i + chunk])

    # Skip the warm-up window (~512 samples) when measuring — that region is
    # filled with leading zeros by design.
    warmup = BLOCK_LEN
    mic_steady = mic[warmup:]
    out_steady = out[warmup:]

    # 1. Output must not be byte-identical to the input (algorithm did
    #    something).
    assert not np.allclose(out_steady, mic_steady, atol=1e-7), (
        "DTLN output is byte-identical to input — filter is no-op?"
    )

    # 2. RMS of (output - noise_target) should be smaller than RMS of
    #    (mic - noise_target). I.e. the output is closer to the
    #    noise-only signal than the raw mic was. Generous: factor-of-2
    #    improvement is plenty (the model isn't trained on sine + WGN).
    noise_steady = noise[warmup:]
    err_in = float(np.sqrt(np.mean((mic_steady - noise_steady) ** 2)))
    err_out = float(np.sqrt(np.mean((out_steady - noise_steady) ** 2)))
    assert err_out < err_in, (
        f"DTLN output is no closer to noise-target than the mic was: "
        f"err_in={err_in:.4f}, err_out={err_out:.4f}"
    )


# ─────────────────── 9. p95 lifecycle ──────────────────────────────────


def test_p95_computed_after_enough_samples() -> None:
    """`p95_ms` is None until ≥50 latency samples are collected, then
    becomes a positive float."""
    f = DTLNResidualFilter()
    # Push <50 hops' worth of input: 5 × 160 samples → 5 process() calls
    # → 5 latency samples. Property must remain None.
    for _ in range(5):
        mic = np.zeros(160, dtype=np.float32)
        ref = np.zeros(160, dtype=np.float32)
        f.process(mic, ref)
    assert f.p95_ms is None

    # Push enough more calls to cross the 50-sample threshold.
    for _ in range(60):
        mic = np.zeros(160, dtype=np.float32)
        ref = np.zeros(160, dtype=np.float32)
        f.process(mic, ref)

    p95 = f.p95_ms
    assert p95 is not None, "p95_ms must be a float once ≥50 samples are recorded"
    assert p95 > 0.0, f"p95_ms must be positive (real inference time); got {p95}"

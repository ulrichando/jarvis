"""Tests for the DTLN L3 wiring in jarvis_voice_client (Phase B Task 10).

Exercises the runtime-integration glue around `DTLNResidualFilter`:
  - `_get_dtln()` lazy singleton + operator ceiling + load-failure latch
  - `_apply_dtln_to_mic()` mic-frame wrapper around `dtln.process(...)`
  - `_write_aec_state_snapshot()` plumbing `dtln_latency_ms_p95` through
    the cross-process state file

The DTLN runtime itself (`audio/dtln_aec.py`) is tested in
`test_dtln_aec.py` — those tests must already be green.

Real model files are SHA-pinned in the repo (~1 MB each, CPU-only); they
ship + load in CI.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# jarvis_voice_client imports sounddevice, which raises OSError (not
# ImportError) at import time when the PortAudio C library is absent —
# pytest.importorskip can't catch that, so guard explicitly. CI installs
# libportaudio2 so these tests RUN there; the skip is for minimal envs.
try:
    import sounddevice  # noqa: F401
except (ImportError, OSError) as _e:
    pytest.skip(f"sounddevice/PortAudio unavailable: {_e}", allow_module_level=True)

import jarvis_voice_client as jvc  # noqa: E402  (after sys.path mutation)
from audio.apm_reverse_stream import ReverseRefRingBuffer  # noqa: E402
from audio.dtln_aec import DTLNResidualFilter  # noqa: E402


# ─── helper: reset the module-level DTLN singleton between tests ─────────


def _reset_dtln_singleton() -> None:
    """Wipe the lazy-load latches so each test sees a fresh `_get_dtln()`."""
    jvc._dtln = None
    jvc._dtln_load_attempted = False


@pytest.fixture(autouse=True)
def _isolate_dtln_singleton():
    """Every test starts with a clean singleton + no leaked sentinel."""
    _reset_dtln_singleton()
    yield
    _reset_dtln_singleton()


# ─── 1. _get_dtln returns a live filter in the default environment ────────


def test_get_dtln_returns_filter_in_normal_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default JARVIS_NEURAL_AEC unset (or =1) → a real `DTLNResidualFilter`
    instance comes back, `healthy` is True straight off the load."""
    monkeypatch.delenv("JARVIS_NEURAL_AEC", raising=False)
    f = jvc._get_dtln()
    assert f is not None
    assert isinstance(f, DTLNResidualFilter)
    assert f.healthy is True


# ─── 2. _get_dtln honors the operator ceiling ─────────────────────────────


def test_get_dtln_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`JARVIS_NEURAL_AEC=0` is an operator ceiling — must return None
    without attempting the load, so the layer can be rolled back without
    a code change."""
    monkeypatch.setenv("JARVIS_NEURAL_AEC", "0")
    assert jvc._get_dtln() is None
    # And the cached module-state must stay clean (no half-loaded model
    # waiting to bite us when the env flips back).
    assert jvc._dtln is None
    assert jvc._dtln_load_attempted is False


# ─── 3. _get_dtln is a cached singleton ───────────────────────────────────


def test_get_dtln_cached_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeat calls must return the same object — the realtime mic
    callback can't afford a fresh TFLite Interpreter per frame."""
    monkeypatch.delenv("JARVIS_NEURAL_AEC", raising=False)
    a = jvc._get_dtln()
    b = jvc._get_dtln()
    assert a is b
    assert a is not None  # both load paths produced the same instance


# ─── 4. _apply_dtln_to_mic substitutes cleaned audio on speakers ──────────


def _make_48k_int16_frame(samples: int = 480) -> bytes:
    """Synthetic 48 kHz int16 mic frame (10 ms = 480 samples)."""
    rng = np.random.default_rng(seed=1234)
    return (rng.standard_normal(samples) * 3000).astype(np.int16).tobytes()


def test_apply_dtln_to_mic_substitutes_cleaned_when_speakers_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With `profile=='speakers'`, a healthy DTLN, and a primed reference
    ring, the helper returns a NEW bytes object whose contents differ
    from the input (the model cleaned something) — not byte-identical
    passthrough."""
    monkeypatch.delenv("JARVIS_NEURAL_AEC", raising=False)
    dtln = jvc._get_dtln()
    assert dtln is not None and dtln.healthy

    # Prime the reverse ring with a synthetic 48 kHz reference frame so the
    # ring's downsample stores a non-zero 16 kHz ref aligned at adc_t=2.0.
    ring = ReverseRefRingBuffer(capacity_frames=16)
    ref_48 = (np.sin(2 * np.pi * 440.0 * np.arange(480) / 48000) * 0.3).astype(np.float32)
    ring.write(ref_48, dac_ts=1.0)

    # Push enough mic frames through to clear DTLN's ~448-sample warm-up
    # (≥4 frames @ 480 samples). The last frame is the one we assert on.
    mic_in = _make_48k_int16_frame()
    out_bytes = mic_in
    for _ in range(6):
        out_bytes = jvc._apply_dtln_to_mic(
            mic_in, adc_t=2.0, ring=ring, profile="speakers", dtln=dtln,
        )
    # Helper returned a new object (cleaning happened) — not the input
    # bytes identity.
    assert out_bytes is not mic_in
    assert len(out_bytes) == len(mic_in)  # same frame size preserved
    # And the contents diverged from the input (something was subtracted).
    assert out_bytes != mic_in


def test_apply_dtln_to_mic_passthrough_on_headphones_profile() -> None:
    """`profile=='headphones'` → return the input bytes unchanged
    (identity). Headphones have no echo path; DTLN would just burn CPU."""
    ring = ReverseRefRingBuffer(capacity_frames=16)
    mic_in = _make_48k_int16_frame()
    dtln = jvc._get_dtln()
    out = jvc._apply_dtln_to_mic(
        mic_in, adc_t=1.0, ring=ring, profile="headphones", dtln=dtln,
    )
    assert out is mic_in  # identity — explicit passthrough


def test_apply_dtln_to_mic_passthrough_when_dtln_none() -> None:
    """No DTLN instance → input returned unchanged (identity). The mic
    callback survives a missing model file (e.g., JARVIS_NEURAL_AEC=0)
    without touching the publish path."""
    ring = ReverseRefRingBuffer(capacity_frames=16)
    mic_in = _make_48k_int16_frame()
    out = jvc._apply_dtln_to_mic(
        mic_in, adc_t=1.0, ring=ring, profile="speakers", dtln=None,
    )
    assert out is mic_in


def test_apply_dtln_to_mic_passthrough_on_inference_exception() -> None:
    """If `dtln.process(...)` raises, the helper returns the input bytes
    (identity) — the realtime path must never raise upward."""
    class _ExplodingDTLN:
        healthy = True
        def process(self, mic, ref):  # noqa: D401 — stub
            raise RuntimeError("boom")
    ring = ReverseRefRingBuffer(capacity_frames=16)
    ring.write(np.zeros(480, dtype=np.float32), dac_ts=1.0)
    mic_in = _make_48k_int16_frame()
    out = jvc._apply_dtln_to_mic(
        mic_in, adc_t=2.0, ring=ring, profile="speakers", dtln=_ExplodingDTLN(),
    )
    assert out is mic_in  # passthrough on exception


# ─── 5. _write_aec_state_snapshot plumbs DTLN p95 cross-process ───────────


def test_aec_state_includes_dtln_p95_when_loaded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """After enough DTLN frames to compute p95, calling the snapshot writer
    must persist a non-None `dtln_latency_ms_p95` plus `l3_active=True`
    into the cross-process AEC state JSON."""
    monkeypatch.delenv("JARVIS_NEURAL_AEC", raising=False)
    dtln = jvc._get_dtln()
    assert dtln is not None

    # Process enough frames to fill the latency window past the p95 threshold
    # (≥50 samples, see dtln_aec._LAT_MIN_FOR_P95).
    for _ in range(60):
        mic = np.zeros(160, dtype=np.float32)
        ref = np.zeros(160, dtype=np.float32)
        dtln.process(mic, ref)
    assert dtln.healthy is True
    assert dtln.p95_ms is not None and dtln.p95_ms > 0.0

    # Drive the writer directly with the production args — verifies the
    # whole chain `_get_dtln().p95_ms → write_aec_state → read_aec_state`.
    from audio.aec_state import write_aec_state, read_aec_state
    out_file = tmp_path / "aec-state.json"
    write_aec_state(
        out_file,
        output_profile="speakers",
        l1_active=True,
        l2_aec_active=False,
        l3_active=bool(dtln.healthy),
        apm_delay_ms_p50=0,
        dtln_latency_ms_p95=dtln.p95_ms,
    )
    state = read_aec_state(out_file, max_age_s=60)
    assert state["aec_layer3_active"] == 1
    assert state["dtln_latency_ms_p95"] is not None
    assert state["dtln_latency_ms_p95"] > 0.0

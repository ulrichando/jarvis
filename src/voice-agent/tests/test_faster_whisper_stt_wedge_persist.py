"""GPU-wedge episode persistence across job-process churn (2026-07-18).

The 2026-07-16T01:19–01:33 UTC live burst: 462 "CUDA failed with error
unknown error" log lines across 32 job pids in ~14 minutes. livekit-agents
spawns a NEW job process (→ a fresh ``FasterWhisperSTT``) per room session,
so every per-instance defense — the wedge streak, the sticky-CPU switch,
the notified-once latch — died with each process: every fresh process
re-armed on the wedged GPU, re-paid the retry+model-reload lag, and would
re-fire the "switched to CPU" notification once per process.

The fix under test: a per-boot flag file (validated against the current
boot id + a live cuInit probe on every read) that makes the wedge EPISODE
outlive the process. One notification per episode — not per process — and
a fresh process on a wedged GPU arms straight on CPU with zero laggy clips.

Hermetic: the flag path is env-redirected to tmp, the cuInit probe is
stubbed, models are fakes — no GPU, no libcuda, no real ~/.jarvis writes.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from livekit import rtc
from livekit.agents import APIConnectOptions

import providers.faster_whisper_stt as fw
from providers.faster_whisper_stt import FasterWhisperSTT


# ── helpers (mirrors test_local_stt_gpu_retry.py's fake-model machinery) ─────

class _Seg:
    def __init__(self, text: str):
        self.text = text


class _Info:
    language = "en"


class _FakeModel:
    """transcribe() raises the queued exceptions first, then succeeds."""

    def __init__(self, failures: list[Exception]):
        self.failures = list(failures)
        self.calls = 0

    def transcribe(self, *a, **kw):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return [_Seg("hello world")], _Info()


def _buffer():
    return [rtc.AudioFrame.create(16000, 1, 160)]


def _recognize(inst):
    return asyncio.run(
        inst._recognize_impl(_buffer(), conn_options=APIConnectOptions())
    )


_CUDA_ERR = "CUDA failed with error unknown error"


@pytest.fixture
def wedge_env(monkeypatch, tmp_path):
    """Redirect the flag file to tmp + capture desktop notifications."""
    flag = tmp_path / "wedge.json"
    monkeypatch.setenv("JARVIS_LOCAL_STT_WEDGE_FLAG", str(flag))
    calls: list[tuple[str, str]] = []
    import pipeline.cron_delivery as cd
    monkeypatch.setattr(cd, "notify", lambda t, b: calls.append((t, b)))
    return flag, calls


def _probe(monkeypatch, result):
    monkeypatch.setattr(fw, "_cuda_healthy", lambda: result)


def _cuda_inst(monkeypatch, failures=()):
    """A cuda-device instance whose (fake) model + CPU model never load a
    real WhisperModel; retry backoff sleeps are zeroed."""
    real_sleep = asyncio.sleep
    monkeypatch.setattr(fw.asyncio, "sleep", lambda d: real_sleep(0), raising=True)
    inst = FasterWhisperSTT(model="large-v3-turbo", device="cuda",
                            compute_type="int8_float16", language="en")
    fake = _FakeModel(list(failures))
    inst._model = fake

    async def _fake_ensure():
        if inst._model is None:
            inst._model = fake
        return inst._model

    monkeypatch.setattr(inst, "_ensure_model", _fake_ensure)
    cpu_fake = _FakeModel([])
    inst._cpu_fake = cpu_fake
    monkeypatch.setattr(inst, "_ensure_cpu_model", lambda: cpu_fake)
    return inst, fake


# ── arm-time probe: wedge caught at construction, not mid-conversation ───────

def test_arm_probe_wedged_coerces_to_cpu_and_notifies_once(wedge_env, monkeypatch):
    flag, calls = wedge_env
    _probe(monkeypatch, False)
    inst = FasterWhisperSTT(device="cuda", compute_type="int8_float16")
    assert inst._device == "cpu"
    assert inst._compute_type == "int8"       # float16 is GPU-only
    assert flag.exists()                      # episode recorded for this boot
    data = json.loads(flag.read_text())
    assert data["notified"] is True
    assert data["boot_id"] == fw._boot_id()
    assert len(calls) == 1                    # the ONE per-episode heads-up
    assert "jarvis-cuda-recover" in calls[0][1]  # remedy in the body


def test_arm_probe_wedged_second_process_is_silent(wedge_env, monkeypatch):
    # THE H1 regression: process churn must not re-notify. A second fresh
    # instance (= a new job process on the same wedged boot) arms on CPU
    # silently — the episode already told the user.
    flag, calls = wedge_env
    _probe(monkeypatch, False)
    a = FasterWhisperSTT(device="cuda", compute_type="int8_float16")
    b = FasterWhisperSTT(device="cuda", compute_type="int8_float16")
    assert a._device == b._device == "cpu"
    assert len(calls) == 1                    # not 2 — the latch survived churn


def test_arm_probe_healthy_clears_stale_flag(wedge_env, monkeypatch):
    # jarvis-cuda-recover (or a transient wedge that healed) → probe healthy:
    # the recorded episode is over; the flag is retired and GPU re-arms.
    flag, calls = wedge_env
    flag.write_text(json.dumps(
        {"boot_id": fw._boot_id(), "ts": 0, "notified": True}
    ))
    _probe(monkeypatch, True)
    inst = FasterWhisperSTT(device="cuda", compute_type="int8_float16")
    assert inst._device == "cuda"             # GPU path restored
    assert not flag.exists()                  # zero residue
    assert calls == []


def test_flag_from_another_boot_is_discarded(wedge_env, monkeypatch):
    # A reboot resets the GPU — an old boot's episode must never mute the
    # notification for a NEW wedge on this boot.
    flag, calls = wedge_env
    flag.write_text(json.dumps(
        {"boot_id": "not-this-boot", "ts": 0, "notified": True}
    ))
    _probe(monkeypatch, False)
    inst = FasterWhisperSTT(device="cuda", compute_type="int8_float16")
    assert inst._device == "cpu"
    assert len(calls) == 1                    # new episode → user informed
    assert json.loads(flag.read_text())["boot_id"] == fw._boot_id()


def test_probe_unavailable_honors_recorded_episode(wedge_env, monkeypatch):
    # No libcuda to probe (None) but THIS boot recorded a wedge → trust it,
    # silently (already notified).
    flag, calls = wedge_env
    flag.write_text(json.dumps(
        {"boot_id": fw._boot_id(), "ts": 0, "notified": True}
    ))
    _probe(monkeypatch, None)
    inst = FasterWhisperSTT(device="cuda", compute_type="int8_float16")
    assert inst._device == "cpu"
    assert calls == []


def test_probe_unavailable_without_flag_leaves_gpu(wedge_env, monkeypatch):
    # Unknown GPU state + no recorded episode → never preempt; the runtime
    # retry/degrade path owns the decision.
    flag, calls = wedge_env
    _probe(monkeypatch, None)
    inst = FasterWhisperSTT(device="cuda", compute_type="int8_float16")
    assert inst._device == "cuda"
    assert not flag.exists()
    assert calls == []


def test_corrupt_flag_self_cleans(wedge_env, monkeypatch):
    flag, calls = wedge_env
    flag.write_text("{not json")
    _probe(monkeypatch, True)
    inst = FasterWhisperSTT(device="cuda", compute_type="int8_float16")
    assert inst._device == "cuda"
    assert not flag.exists()                  # corrupt flag deleted on sight


# ── runtime sticky switch persists the episode across "processes" ────────────

def test_runtime_sticky_writes_flag_and_next_process_arms_cpu_silently(
    wedge_env, monkeypatch,
):
    # End-to-end across a simulated restart: process A develops the wedge
    # mid-session (probe was healthy at ITS arm time), sticks to CPU after 3
    # wedged clips, notifies ONCE, records the episode. Process B (the churn
    # replacement) probes the now-wedged GPU at arm time → CPU, silent.
    # Total notifications across both processes: exactly 1.
    flag, calls = wedge_env
    monkeypatch.setenv("JARVIS_LOCAL_STT_GPU_STICKY_AFTER", "3")
    monkeypatch.setenv("JARVIS_LOCAL_STT_GPU_RETRIES", "2")
    _probe(monkeypatch, True)                 # healthy when A armed
    a, _fake = _cuda_inst(monkeypatch, [RuntimeError(_CUDA_ERR)] * 9)
    for _ in range(3):                        # 3 wedged clips → sticky
        ev = _recognize(a)
        assert ev.alternatives[0].text == "hello world"
    assert a._device == "cpu"
    assert len(calls) == 1
    assert flag.exists()                      # episode persisted
    _probe(monkeypatch, False)                # the GPU is wedged for B too
    b = FasterWhisperSTT(device="cuda", compute_type="int8_float16")
    assert b._device == "cpu"                 # no laggy wedged clips for B
    assert len(calls) == 1                    # STILL one — churn can't flood


def test_post_sticky_cpu_success_does_not_clear_flag(wedge_env, monkeypatch):
    # After the sticky switch, clips succeed on the CPU model — that success
    # must NOT retire the episode, or the next process would re-arm on the
    # still-dead GPU and re-notify.
    flag, calls = wedge_env
    monkeypatch.setenv("JARVIS_LOCAL_STT_GPU_STICKY_AFTER", "3")
    monkeypatch.setenv("JARVIS_LOCAL_STT_GPU_RETRIES", "2")
    _probe(monkeypatch, True)
    inst, _fake = _cuda_inst(monkeypatch, [RuntimeError(_CUDA_ERR)] * 9)
    for _ in range(3):
        _recognize(inst)
    assert inst._device == "cpu" and flag.exists()
    _recognize(inst)                          # a clean post-sticky CPU clip
    assert flag.exists()                      # episode still on record


def test_gpu_success_mid_streak_retires_episode(wedge_env, monkeypatch):
    # A clean GPU clip between wedges (wedge was transient after all) clears
    # the streak AND any recorded episode — the next process re-arms on GPU.
    flag, calls = wedge_env
    monkeypatch.setenv("JARVIS_LOCAL_STT_GPU_STICKY_AFTER", "3")
    monkeypatch.setenv("JARVIS_LOCAL_STT_GPU_RETRIES", "2")
    _probe(monkeypatch, True)
    inst, _fake = _cuda_inst(monkeypatch, [RuntimeError(_CUDA_ERR)] * 3)
    _recognize(inst)                          # clip 1: wedged → streak 1
    assert inst._gpu_wedge_streak == 1
    flag.write_text(json.dumps(               # e.g. a sibling process stuck
        {"boot_id": fw._boot_id(), "ts": 0, "notified": True}
    ))
    _recognize(inst)                          # clip 2: GPU succeeds
    assert inst._gpu_wedge_streak == 0
    assert not flag.exists()                  # GPU demonstrably works → retire


# ── kill-switch ───────────────────────────────────────────────────────────────

def test_kill_switch_restores_per_process_behavior(wedge_env, monkeypatch):
    # JARVIS_LOCAL_STT_WEDGE_PERSIST=0: no arm-time probe/coercion, no flag
    # I/O — byte-for-byte the pre-2026-07-18 per-process behavior.
    flag, calls = wedge_env
    monkeypatch.setenv("JARVIS_LOCAL_STT_WEDGE_PERSIST", "0")
    monkeypatch.setenv("JARVIS_LOCAL_STT_GPU_STICKY_AFTER", "3")
    monkeypatch.setenv("JARVIS_LOCAL_STT_GPU_RETRIES", "2")
    _probe(monkeypatch, False)                # would coerce if enabled
    inst, _fake = _cuda_inst(monkeypatch, [RuntimeError(_CUDA_ERR)] * 9)
    assert inst._device == "cuda"             # arm probe skipped
    for _ in range(3):
        _recognize(inst)
    assert inst._device == "cpu"              # sticky still works in-process
    assert len(calls) == 1                    # notify still fires
    assert not flag.exists()                  # but nothing persisted

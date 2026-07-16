"""Local faster-whisper GPU resilience + local-mode error attribution
(2026-07-10 live incident).

Two stacked bugs made a transient CUDA blip spam Ulrich with "can't reach
DeepSeek (network)" notifications in Local mode:

  1. A single transient ``parallel_for failed: cudaError...`` from
     ctranslate2 (qwen on ollama + large-v3 whisper on the same 6 GB card;
     2026-07-11 follow-up pinned the real cause as a genuine CUDA OOM, not
     compute contention) surfaced immediately as APIConnectionError; the
     framework's outer retries land in the same contention window, exhaust,
     and the whole AgentSession dies. Fix: bounded in-rung retries with
     backoff (+ a model reload before the final attempt to rebuild a wedged
     CUDA context) in ``FasterWhisperSTT._recognize_impl``, plus (2026-07-11)
     a one-shot per-clip CPU fallback when exhaustion hits an OOM.

  2. ``jarvis_agent._active_voice_model()`` read the RAW ``~/.jarvis/
     voice-model`` pin (stale ``deepseek-chat-v3``) even in voice-mode=local,
     so the error classifier attributed the STT failure to DeepSeek. Fix:
     apply the same ``_local_mode_speech_model`` override the real brain
     selection uses.

Hermetic: model loading, Ollama discovery, and the ~/.jarvis files are all
patched — no GPU, no subprocess, no real home-dir reads.
"""
from __future__ import annotations

import asyncio

import pytest

from livekit import rtc
from livekit.agents import APIConnectionError, APIConnectOptions

import providers.faster_whisper_stt as fw
from providers.faster_whisper_stt import FasterWhisperSTT


# ── helpers ──────────────────────────────────────────────────────────────────

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


@pytest.fixture
def stt_with(monkeypatch):
    """Build a cuda-device FasterWhisperSTT whose model is a fake; the
    reload path (_ensure_model after self._model=None) returns the SAME
    fake so no real WhisperModel ever loads."""

    def make(failures, retries="2"):
        monkeypatch.setenv("JARVIS_LOCAL_STT_GPU_RETRIES", retries)
        # Kill the retry backoff sleeps (module-level asyncio.sleep) so the
        # suite doesn't pay real wall-clock for the exhaust cases.
        real_sleep = asyncio.sleep
        monkeypatch.setattr(
            fw.asyncio, "sleep", lambda d: real_sleep(0), raising=True
        )
        inst = FasterWhisperSTT(model="large-v3-turbo", device="cuda",
                                compute_type="int8_float16", language="en")
        fake = _FakeModel(failures)
        inst._model = fake

        async def _fake_ensure():
            if inst._model is None:
                inst._model = fake       # "reload" hands back the fake
                fake.reloaded = True
            return inst._model

        monkeypatch.setattr(inst, "_ensure_model", _fake_ensure)
        # OOM CPU fallback path (2026-07-11): hand back a fake "cpu model"
        # too, so the fallback never loads a real WhisperModel in tests.
        cpu_fake = _FakeModel([])
        inst._cpu_fake = cpu_fake        # test handle
        monkeypatch.setattr(inst, "_ensure_cpu_model", lambda: cpu_fake)
        return inst, fake

    yield make
    # restore asyncio.sleep is handled by monkeypatch


_CUDA_ERR = "parallel_for failed: cudaErrorInvalidDevice: invalid device ordinal"


# ── 1. transient CUDA error → retried, NOT fatal ─────────────────────────────

def test_transient_cuda_error_is_retried_not_fatal(stt_with):
    inst, fake = stt_with([RuntimeError(_CUDA_ERR)])
    ev = _recognize(inst)
    assert ev.alternatives[0].text == "hello world"
    assert fake.calls == 2  # failed once, retried once, succeeded


def test_two_blips_still_recover_within_default_budget(stt_with):
    inst, fake = stt_with([RuntimeError(_CUDA_ERR), RuntimeError(_CUDA_ERR)])
    ev = _recognize(inst)
    assert ev.alternatives[0].text == "hello world"
    assert fake.calls == 3
    # the final attempt runs on a freshly (re)loaded model
    assert getattr(fake, "reloaded", False) is True


# ── a wedged GPU degrades to CPU (no longer drops the clip) ──────────────────

def test_persistent_non_oom_cuda_degrades_this_clip_to_cpu(stt_with):
    # A wedged GPU (invalid device ordinal — the CUDA context lost after a
    # laptop suspend) exhausts the bounded GPU retries, then transcribes THIS
    # clip on CPU so the utterance survives instead of being dropped/surfaced.
    inst, fake = stt_with([RuntimeError(_CUDA_ERR)] * 10)
    ev = _recognize(inst)
    assert ev.alternatives[0].text == "hello world"  # CPU fallback text
    assert fake.calls == 3            # 1 initial + 2 GPU retries, bounded
    assert inst._cpu_fake.calls == 1  # degraded to CPU
    assert inst._device == "cuda"     # a single clip < sticky threshold
    assert inst._gpu_wedge_streak == 1
    # The model MUST be dropped for a fresh CUDA context before the final GPU
    # attempt — else a wedged context outlives the reload path.
    assert getattr(fake, "reloaded", False) is True


def test_retries_one_reloads_on_first_retry(stt_with):
    # Boundary: retries=1 → the reload fires on the very first (and only) retry,
    # then the exhausted clip degrades to CPU.
    inst, fake = stt_with([RuntimeError(_CUDA_ERR)] * 10, retries="1")
    ev = _recognize(inst)
    assert ev.alternatives[0].text == "hello world"
    assert fake.calls == 2
    assert inst._cpu_fake.calls == 1
    assert getattr(fake, "reloaded", False) is True


def test_non_gpu_error_is_not_retried(stt_with):
    inst, fake = stt_with([RuntimeError("wav decode kaboom")])
    with pytest.raises(APIConnectionError):
        _recognize(inst)
    assert fake.calls == 1  # no retry for non-transient errors — unchanged path


def test_retry_knob_zero_disables_gpu_retries_then_cpu_fallback(stt_with):
    # retries=0 → no GPU re-tries; the first wedge exhausts immediately and the
    # clip degrades straight to CPU (utterance still survives).
    inst, fake = stt_with([RuntimeError(_CUDA_ERR)] * 10, retries="0")
    ev = _recognize(inst)
    assert ev.alternatives[0].text == "hello world"
    assert fake.calls == 1
    assert inst._cpu_fake.calls == 1


# ── GPU OOM → one-shot CPU degrade (2026-07-11 live incident) ────────────────
# The real failure was a genuine CUDA OOM (ollama holds ~3 GB of the 6 GB
# card; the whisper encode's activation spike exceeds the remainder). GPU
# retries re-allocate the same VRAM and OOM again, so exhaustion must fall
# back to a CPU transcription of THIS clip instead of killing the session.

_OOM_ERR = "CUDA failed with error out of memory"


def test_persistent_oom_falls_back_to_cpu_not_fatal(stt_with):
    # Exactly 3 OOMs = the full GPU budget (1 initial + 2 retries) so the
    # queue is empty for the follow-up clip below.
    inst, fake = stt_with([RuntimeError(_OOM_ERR)] * 3)
    ev = _recognize(inst)
    assert ev.alternatives[0].text == "hello world"
    assert fake.calls == 3            # GPU attempts stayed bounded
    assert inst._cpu_fake.calls == 1  # exactly one CPU transcription
    # NOT a permanent switch: device unchanged → the NEXT clip retries the
    # GPU (VRAM may have freed) and never touches the CPU model again.
    assert inst._device == "cuda"
    ev2 = _recognize(inst)
    assert ev2.alternatives[0].text == "hello world"
    assert fake.calls == 4
    assert inst._cpu_fake.calls == 1


# ── a persistently-wedged GPU sticks to CPU (the flood fix) ──────────────────

def test_non_oom_wedge_sticks_to_cpu_after_threshold(stt_with, monkeypatch):
    # The flood fix: a GPU that stays wedged clip after clip must stop being
    # retried. After JARVIS_LOCAL_STT_GPU_STICKY_AFTER wedged clips the session
    # switches to CPU permanently, so it stops re-hitting the dead GPU and
    # re-firing the "speech-to-text GPU error" notification every clip.
    monkeypatch.setenv("JARVIS_LOCAL_STT_GPU_STICKY_AFTER", "3")
    # 3 clips × (1 initial + 2 retries) = 9 GPU attempts, all wedged.
    inst, fake = stt_with([RuntimeError(_CUDA_ERR)] * 9)
    for _ in range(3):
        ev = _recognize(inst)
        assert ev.alternatives[0].text == "hello world"  # CPU fallback each clip
    assert inst._device == "cpu"          # stuck to CPU after the 3rd wedge
    assert inst._compute_type == "int8"   # float16 is GPU-only
    assert inst._cpu_fake.calls == 3      # one CPU transcription per wedged clip


def test_gpu_success_resets_the_wedge_streak(stt_with, monkeypatch):
    # A clean GPU clip between wedges clears the streak, so intermittent blips
    # never accumulate into a spurious sticky-CPU switch.
    monkeypatch.setenv("JARVIS_LOCAL_STT_GPU_STICKY_AFTER", "3")
    inst, fake = stt_with([RuntimeError(_CUDA_ERR)] * 3)  # clip 1: 3 wedges
    _recognize(inst)
    assert inst._gpu_wedge_streak == 1
    ev = _recognize(inst)                 # clip 2: queue empty → GPU succeeds
    assert ev.alternatives[0].text == "hello world"
    assert inst._gpu_wedge_streak == 0
    assert inst._device == "cuda"


def test_sticky_disabled_never_switches_device(stt_with, monkeypatch):
    # STICKY_AFTER=0 opts out: every wedged clip still degrades to CPU, but the
    # device stays cuda (for a box that wants the GPU retried indefinitely).
    monkeypatch.setenv("JARVIS_LOCAL_STT_GPU_STICKY_AFTER", "0")
    inst, fake = stt_with([RuntimeError(_CUDA_ERR)] * 30)
    for _ in range(5):
        _recognize(inst)
    assert inst._device == "cuda"
    assert inst._cpu_fake.calls == 5


def test_oom_on_cpu_device_surfaces_no_self_fallback(stt_with, monkeypatch):
    # Already on CPU → nothing to degrade to; surface honestly.
    inst, fake = stt_with([RuntimeError(_OOM_ERR)] * 10)
    inst._device = "cpu"
    with pytest.raises(APIConnectionError):
        _recognize(inst)
    assert inst._cpu_fake.calls == 0


# ── Part A: default local STT model is large-v3-turbo ────────────────────────

def test_default_local_stt_model_is_large_v3_turbo(monkeypatch):
    # 2026-07-11 OOM fix Part A: large-v3-turbo is the documented live model
    # (CLAUDE.md / .env / tray); the bigger large-v3 default OOM'd the 6 GB
    # card next to the local LLM. Construction is lazy — no model loads.
    monkeypatch.setenv("JARVIS_LOCAL_STT_ENABLED", "1")
    monkeypatch.delenv("JARVIS_LOCAL_STT_MODEL", raising=False)
    assert FasterWhisperSTT()._model_size == "large-v3-turbo"
    assert fw.build_local_stt()._model_size == "large-v3-turbo"


# ── 2. _active_voice_model is local-mode aware ───────────────────────────────

@pytest.fixture
def voice_files(tmp_path, monkeypatch):
    import jarvis_agent as ja
    import providers.llm as llm_mod
    from providers import local_model_picker

    jdir = tmp_path / ".jarvis"
    jdir.mkdir()

    class _Home:
        @staticmethod
        def home():
            return tmp_path

    monkeypatch.setattr(ja, "Path", _Home)
    monkeypatch.setattr(llm_mod, "VOICE_MODE_FILE", tmp_path / "voice-mode")
    # Also point the documented hermeticity env at the same file, so the
    # override can't be steered by a leaked JARVIS_VOICE_MODE_PATH from a
    # sibling test even if _local_mode_speech_model is refactored to re-read it.
    monkeypatch.setenv("JARVIS_VOICE_MODE_PATH", str(tmp_path / "voice-mode"))
    monkeypatch.delenv("JARVIS_LOCAL_LLM_MODEL", raising=False)
    # never shell out to real `ollama list`
    monkeypatch.setattr(
        local_model_picker, "resolve_installed_model_tag",
        lambda preferred="": None,
    )
    return ja, jdir, tmp_path / "voice-mode"


def test_cloud_mode_returns_raw_pin_unchanged(voice_files):
    """HARD constraint: outside local mode, byte-identical to the old read."""
    ja, jdir, vmode = voice_files
    (jdir / "voice-model").write_text("deepseek-v4-flash\n")
    vmode.write_text("cloud\n")
    assert ja._active_voice_model() == "deepseek-v4-flash"


def test_absent_mode_file_returns_raw_pin(voice_files):
    ja, jdir, _ = voice_files
    (jdir / "voice-model").write_text("deepseek-chat-v3\n")
    assert ja._active_voice_model() == "deepseek-chat-v3"


def test_local_mode_overrides_stale_cloud_pin(voice_files, monkeypatch):
    """THE mislabel: stale deepseek pin + voice-mode=local must yield the
    on-device ollama model, so errors are never attributed to DeepSeek."""
    ja, jdir, vmode = voice_files
    (jdir / "voice-model").write_text("deepseek-chat-v3\n")
    vmode.write_text("local\n")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "qwen2.5:7b")
    assert ja._active_voice_model() == "ollama/qwen2.5:7b"


def test_local_mode_speech_model_direct_bypasses_swallow(voice_files, monkeypatch):
    """Assert the override function DIRECTLY, not through _active_voice_model —
    the latter wraps it in `except Exception: pass` and would silently fall
    back to the stale pin, so a real bug in the override could pass as a
    'cloud-mode' result. This test fails loudly if the override raises."""
    import providers.llm as llm_mod
    _, jdir, vmode = voice_files
    vmode.write_text("local\n")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "qwen2.5:7b")
    assert llm_mod._local_mode_speech_model("deepseek-chat-v3") == "ollama/qwen2.5:7b"


def test_local_mode_keeps_explicit_ollama_pin(voice_files, monkeypatch):
    ja, jdir, vmode = voice_files
    (jdir / "voice-model").write_text("ollama/llama3.1:8b\n")
    vmode.write_text("local\n")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "qwen2.5:7b")
    assert ja._active_voice_model() == "ollama/llama3.1:8b"


def test_local_mode_nothing_resolvable_falls_back_to_pin(voice_files):
    """No local model resolvable → degrade to the pin (same as the brain
    selection: better a wrong-but-existing name than None)."""
    ja, jdir, vmode = voice_files
    (jdir / "voice-model").write_text("deepseek-v4-flash\n")
    vmode.write_text("local\n")
    assert ja._active_voice_model() == "deepseek-v4-flash"


def test_local_mode_error_attribution_end_to_end(voice_files, monkeypatch):
    """The full mislabel chain, cut: local mode + stale deepseek pin + an LLM
    connection error → the classifier names the LOCAL model's provider, and
    the spoken line never says DeepSeek."""
    ja, jdir, vmode = voice_files
    from pipeline.provider_errors import classify_provider_error

    (jdir / "voice-model").write_text("deepseek-chat-v3\n")
    vmode.write_text("local\n")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "qwen2.5:7b")

    c = classify_provider_error(
        ConnectionError("Connection refused"), model=ja._active_voice_model()
    )
    assert "DeepSeek" not in c.spoken
    assert "DeepSeek" not in c.notify_title
    assert c.provider == "Qwen"

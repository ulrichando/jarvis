"""Local faster-whisper STT — offline last-resort speech-to-text.

A custom livekit :class:`stt.STT` that runs OpenAI Whisper locally via
faster-whisper (ctranslate2). It is the FINAL rung of JARVIS's STT
FallbackAdapter chain (Deepgram → THIS) — and the sole/primary rung
under the live ``JARVIS_STT_LOCAL_ONLY=1`` config — activated only
when ``JARVIS_LOCAL_STT_ENABLED=1``.

Non-streaming (finals only) — the chain's ``StreamAdapter`` + Silero VAD
wraps it for streaming compatibility, as with any finals-only Whisper STT.
Runs CPU/int8 by default so it never contends with the local LLM for the
6 GB GPU and needs no cuDNN; override via ``JARVIS_LOCAL_STT_DEVICE`` /
``JARVIS_LOCAL_STT_COMPUTE`` on a bigger box.

The model loads lazily on first transcription (downloads from HF the
first time, then cached under ~/.cache/huggingface). Part of the local
offline fallback stack — see ``pipeline/config.py`` and the 2026-06-15
local-LLM design (~/.claude/plans/we-need-to-find-polymorphic-allen.md).
"""
from __future__ import annotations

import asyncio
import ctypes
import io
import json
import logging
import os
import re
import time
from pathlib import Path

from livekit import rtc
from livekit.agents import APIConnectionError, APIConnectOptions, stt
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer, is_given

logger = logging.getLogger("jarvis.stt.local")


# Transient GPU failure markers. ctranslate2 raises RuntimeError shapes like
# "CUDA failed with error out of memory" or "parallel_for failed:
# cudaErrorInvalidDevice: invalid device ordinal" when whisper shares the
# 6 GB card with the local LLM. Live incidents 2026-07-10/11: the FIRST
# error in every failure cycle is a genuine CUDA **OOM** (ollama's
# llama-server holds ~3 GB of 6 GB and the whisper encode's activation
# spike exceeds the remainder); the invalid-device error on the next
# attempt is the OOM's downstream corruption of the CUDA context — NOT an
# independent compute/launch-contention failure as earlier comments here
# claimed. Without in-rung retries a single blip cascades: the framework's
# outer recognize() retries land in the same window, exhaust, mark the
# session unrecoverable, and the watchdog tears the whole AgentSession down.
_TRANSIENT_GPU_ERR_RE = re.compile(
    r"parallel_for failed|\bcuda|cublas|cudnn", re.I
)

# OOM-specific subset of the above: when GPU retries exhaust on one of
# these, retrying on the GPU can't cure it (the VRAM is held by the local
# LLM) — degrade THIS clip to a CPU transcription instead of killing the
# session (see _recognize_impl).
_OOM_ERR_RE = re.compile(
    r"out of memory|cudaErrorMemoryAllocation|CUBLAS_STATUS_ALLOC_FAILED", re.I
)


def _gpu_retries() -> int:
    """Bounded in-rung retry count for transient GPU errors (default 2 —
    i.e. up to 3 attempts per recognize; the framework's outer retry loop
    multiplies this, so a genuinely dead GPU still surfaces in seconds,
    not forever). Env-tunable via JARVIS_LOCAL_STT_GPU_RETRIES."""
    try:
        return max(0, int(os.environ.get("JARVIS_LOCAL_STT_GPU_RETRIES", "2")))
    except ValueError:
        return 2


def _gpu_sticky_after() -> int:
    """Consecutive wedged clips before the session sticks to CPU (default 3).

    An OOM is VRAM contention that may free, so it keeps retrying the GPU. But
    a NON-OOM CUDA fault (``invalid device ordinal`` / cuDNN — typically the
    driver/uvm context wedged after a laptop suspend) does NOT self-heal: every
    subsequent clip re-hits the dead GPU, drops to CPU, and re-fires the error
    notification. After this many wedged clips in a row we stop fighting the
    GPU and run STT on CPU for the rest of the process — a `jarvis-cuda-recover`
    + agent restart re-arms the GPU. Env-tunable via
    JARVIS_LOCAL_STT_GPU_STICKY_AFTER (0 disables the sticky switch)."""
    try:
        return max(0, int(os.environ.get("JARVIS_LOCAL_STT_GPU_STICKY_AFTER", "3")))
    except ValueError:
        return 3


# ── GPU-wedge persistence across process churn (2026-07-18) ──────────────────
# All the wedge state below (`_gpu_wedge_streak`, the sticky CPU switch, the
# notified-once latch) is per-INSTANCE — and livekit-agents spawns a NEW job
# process (→ a fresh FasterWhisperSTT) per room session. Live burst
# 2026-07-16T01:19–01:33 UTC: 462 CUDA-failed log lines across 32 job pids in
# ~14 min — every fresh process re-armed on the wedged GPU, re-paid the full
# retry+model-reload lag on its first clips (372 model loads that hour), and
# would ALSO re-fire the "switched to CPU" notification once per process, so
# "notify exactly once" was really "once per restart". This tiny per-boot flag
# file makes the wedge EPISODE outlive the process: "this boot's GPU is wedged
# + the user has been told". It can never go stale — every read is validated
# against the CURRENT boot id (a reboot resets the GPU ⇒ old flags are
# discarded on sight) and honoring it at arm time is gated on a live cuInit
# probe (a recovered GPU ⇒ flag deleted, GPU path re-armed). bin/
# jarvis-cuda-recover also deletes it on a successful recovery, so the
# documented remedy restores the GPU path immediately instead of leaving STT
# silently stuck on CPU. Kill-switch: JARVIS_LOCAL_STT_WEDGE_PERSIST=0
# reverts to the old per-process-only behavior.


def _wedge_persist_enabled() -> bool:
    return os.environ.get("JARVIS_LOCAL_STT_WEDGE_PERSIST", "1") != "0"


def _wedge_flag_path() -> Path:
    """Flag location — env-overridable so tests never touch the real one.
    Keep the default in sync with bin/jarvis-cuda-recover, which clears it."""
    raw = os.environ.get("JARVIS_LOCAL_STT_WEDGE_FLAG", "").strip()
    return Path(raw) if raw else Path.home() / ".jarvis" / "stt-gpu-wedged.json"


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except Exception:
        return ""  # non-Linux / hardened proc — flags then only TTL by boot_id equality


def _cuda_healthy() -> bool | None:
    """Live cuInit probe — the first call ctranslate2 itself makes, and the
    same health check bin/jarvis-cuda-recover trusts. True = GPU usable,
    False = wedged (the cuInit-999 signature), None = can't probe (no
    libcuda on this box) — callers must treat None as "unknown", never as
    wedged, so a CPU-only box is unaffected."""
    try:
        lib = ctypes.CDLL("libcuda.so.1")
        return int(lib.cuInit(0)) == 0
    except Exception:
        return None


def _read_wedge_flag() -> dict | None:
    """The parsed flag for THIS boot, else None. Stale flags (older boot,
    corrupt json, wrong shape) are deleted on sight so they self-clean."""
    path = _wedge_flag_path()
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and data.get("boot_id") == _boot_id():
            return data
        path.unlink(missing_ok=True)  # another boot's episode — GPU was reset
    except FileNotFoundError:
        pass
    except Exception:
        try:
            path.unlink(missing_ok=True)  # corrupt — self-clean
        except Exception:
            pass
    return None


def _write_wedge_flag(*, notified: bool) -> None:
    path = _wedge_flag_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({
            "boot_id": _boot_id(),
            "ts": time.time(),
            "notified": bool(notified),
        }))
        tmp.replace(path)  # atomic vs sibling job processes reading it
    except Exception:
        pass  # persistence is best-effort — it must never break STT itself


def _clear_wedge_flag() -> None:
    try:
        _wedge_flag_path().unlink(missing_ok=True)
    except Exception:
        pass


class FasterWhisperSTT(stt.STT):
    """Local Whisper (faster-whisper) as a non-streaming livekit STT."""

    def __init__(
        self,
        *,
        model: str = "large-v3-turbo",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str | None = None,
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self._model_size = model
        self._device = device
        self._compute_type = compute_type
        self._language = language or None
        self._model = None  # lazy-loaded WhisperModel
        self._cpu_model = None  # lazy CPU model for the GPU-OOM fallback
        self._load_lock = asyncio.Lock()
        self._gpu_wedge_streak = 0  # consecutive non-OOM GPU-wedged clips
        # Arm-time wedge check (2026-07-18): a fresh process arming on a
        # wedged GPU used to pay 3 laggy wedged clips + one MORE notification
        # before its own per-process sticky switch re-engaged — once per room
        # session, for as long as the GPU stayed wedged. Probe cuInit once at
        # construction instead: still wedged → arm straight on CPU (silent if
        # this boot's episode already notified); healthy → clear any recorded
        # episode so the GPU path re-arms with zero residue.
        if self._device != "cpu" and _wedge_persist_enabled():
            self._apply_boot_wedge_state()

    @property
    def label(self) -> str:
        return f"local:faster-whisper/{self._model_size}"

    def _apply_boot_wedge_state(self) -> None:
        """Honor / retire this boot's persisted GPU-wedge episode at arm time.

        Only called when constructed for a GPU device. The flag alone never
        forces CPU — the live cuInit probe is authoritative: probe healthy →
        any flag is stale (jarvis-cuda-recover ran, or the wedge was
        transient) and is deleted; probe wedged → arm on CPU now instead of
        replaying the failure, recording/refreshing the episode. Probe
        unavailable (no libcuda) → trust a recorded episode, else leave the
        runtime retry path to decide."""
        flag = _read_wedge_flag()
        healthy = _cuda_healthy()
        if healthy:
            if flag is not None:
                _clear_wedge_flag()
                logger.info(
                    "[stt.local] GPU recovered — cleared this boot's wedge "
                    "flag; arming on %s as configured", self._device,
                )
            return
        if healthy is None and flag is None:
            return  # unknown GPU state, no recorded episode — don't preempt
        already_notified = bool(flag and flag.get("notified"))
        logger.error(
            "[stt.local] CUDA wedged at arm time (cuInit probe failed%s) — "
            "arming STT on CPU instead of the configured %s. Run "
            "bin/jarvis-cuda-recover to restore the GPU.",
            "" if healthy is False else "; probe unavailable, episode on record",
            self._device,
        )
        self._device = "cpu"
        self._compute_type = "int8"  # float16 is GPU-only
        _write_wedge_flag(notified=True)
        if not already_notified:
            # First signal of THIS episode (episode = one wedge per boot,
            # ended by recovery). Synthesize the canonical wedge error so the
            # stt_gpu classifier words it identically to the runtime path.
            self._notify_switched_to_cpu(RuntimeError(
                "CUDA failed with error unknown error "
                "(cuInit probe failed arming local STT)"
            ))

    async def _ensure_model(self):
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is None:
                def _load():
                    from faster_whisper import WhisperModel
                    return WhisperModel(
                        self._model_size,
                        device=self._device,
                        compute_type=self._compute_type,
                    )
                logger.info(
                    "[stt.local] loading faster-whisper model=%s device=%s compute=%s",
                    self._model_size, self._device, self._compute_type,
                )
                self._model = await asyncio.to_thread(_load)
        return self._model

    def _ensure_cpu_model(self):
        """Lazily-built, cached CPU model for the GPU-OOM per-clip fallback.

        Called from a worker thread (sync on purpose). ponytail: once the
        first OOM fallback fires this keeps a ~1.6 GB model resident in RAM
        for the process lifetime — acceptable as the rare last resort, the
        box has RAM to spare.
        """
        if self._cpu_model is None:
            from faster_whisper import WhisperModel
            logger.info(
                "[stt.local] loading CPU fallback model=%s (int8)",
                self._model_size,
            )
            self._cpu_model = WhisperModel(
                self._model_size, device="cpu", compute_type="int8"
            )
        return self._cpu_model

    def _notify_switched_to_cpu(self, err: Exception) -> None:
        """One desktop notification at the moment STT permanently drops to CPU.

        Fires exactly once per process (at the sticky switch), so it can't
        flood. Needed because, once we stop raising the wedge to the framework,
        the normal provider-error notify path (jarvis_agent._notify_error) is
        never reached — without this the user gets ZERO signal that speech
        recognition silently moved onto the slow CPU path. Reuses the stt_gpu
        classified message so the wording + 'run jarvis-cuda-recover' remedy
        stay consistent with the surfacing path."""
        try:
            from pipeline.provider_errors import classify_provider_error
            from pipeline.cron_delivery import notify
            c = classify_provider_error(err, component="stt")
            notify(c.notify_title, c.notify_body)
        except Exception:
            pass  # notify-send absent / headless — the log line is the signal

    def _transcribe_sync(self, model, wav: bytes, lang: str | None):
        # Anti-hallucination decode config. faster-whisper's DEFAULTS invent
        # fluent text from silence/room-tone (2026-07-19 incident: 43 phantom
        # "user" turns with no real audio — e.g. a 15-word sentence transcribed
        # verbatim 6× across turns). The signature fix is
        # condition_on_previous_text=False: the library default (True) feeds the
        # previously decoded text back in as the decoder prompt, so ONE silence
        # hallucination regurgitates forever. temperature=0.0 disables the
        # temperature-fallback ladder (each fallback rung invents more on a
        # low-confidence clip). vad_filter=True restores faster-whisper's own
        # Silero silence gate — the upstream chain VAD opens on breath/room-tone,
        # so re-gate here (the old "already gated" comment didn't hold in a live
        # room).
        segments, info = model.transcribe(
            io.BytesIO(wav),
            language=lang,
            beam_size=1,                       # fast; this is a last-resort rung
            temperature=0.0,                   # no temp-fallback ladder
            condition_on_previous_text=False,  # CRITICAL: no prior-text feed-forward
            vad_filter=True,                   # faster-whisper's own silence gate, ON
            vad_parameters={"min_silence_duration_ms": 500},
        )
        # Stop discarding the per-segment confidence metadata (previously thrown
        # away): drop any segment faster-whisper itself flags as near-certain
        # silence (high no_speech_prob) or a very low-confidence decode (very
        # negative avg_logprob). These signals catch novel multi-sentence
        # hallucinations the fixed-phrase stt_gate structurally cannot. Kept
        # conservative so genuine quiet speech (no_speech_prob≈0, avg_logprob>-1)
        # is never dropped.
        kept = [
            seg.text
            for seg in segments
            if getattr(seg, "no_speech_prob", 0.0) < 0.85
            and getattr(seg, "avg_logprob", 0.0) > -1.2
        ]
        text = "".join(kept).strip()
        return text, getattr(info, "language", None)

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        lang = (language if is_given(language) else self._language) or None
        # WAV bytes at the source sample rate; faster-whisper decodes +
        # resamples to 16k internally, so no manual resampling is needed.
        wav = rtc.combine_audio_frames(buffer).to_wav_bytes()
        # Transient-GPU retry loop (2026-07-10): on a transient CUDA error
        # (see _TRANSIENT_GPU_ERR_RE) retry the SAME audio a bounded number of
        # times with a small backoff instead of surfacing immediately — the
        # backoff lets the concurrent local-LLM burst clear the card. Before
        # the FINAL attempt the model is dropped + lazily reloaded, rebuilding
        # the ctranslate2 CUDA context (an OOM — or a rarer non-OOM blip —
        # can wedge the existing one, in which case in-context retries never
        # recover). When retries exhaust on a genuine OOM (2026-07-11: the
        # local LLM holds the VRAM, so GPU retries re-OOM forever) this clip
        # degrades to a one-shot CPU transcription instead of dying; the next
        # clip retries the GPU as normal. Non-GPU errors and exhausted
        # non-OOM retries surface exactly as before: an APIConnectionError
        # the chain/framework can cascade on.
        retries = _gpu_retries()
        attempt = 0
        while True:
            try:
                model = await self._ensure_model()
                text, detected = await asyncio.to_thread(
                    self._transcribe_sync, model, wav, lang
                )
                # A clean GPU success clears the streak — and retires any
                # persisted wedge episode (the wedge was transient after
                # all). Guard on device: post-sticky clips succeed on CPU
                # and must NOT clear the flag, or the next job process
                # would re-arm on the still-dead GPU and re-notify.
                if self._device != "cpu":
                    if self._gpu_wedge_streak and _wedge_persist_enabled():
                        _clear_wedge_flag()
                    self._gpu_wedge_streak = 0
                break
            except Exception as e:  # surface as a chain-cascadable error
                blob = f"{type(e).__name__} {e}"
                if attempt >= retries or not _TRANSIENT_GPU_ERR_RE.search(blob):
                    transient_gpu = (
                        bool(_TRANSIENT_GPU_ERR_RE.search(blob))
                        and self._device != "cpu"
                    )
                    if transient_gpu:
                        # A persistent GPU failure with retries exhausted —
                        # either OOM (VRAM held by the local LLM) or a non-OOM
                        # wedge (invalid device ordinal / cuDNN, e.g. the CUDA
                        # context lost after a laptop suspend). Degrade THIS clip
                        # to CPU so the utterance isn't dropped.
                        is_oom = bool(_OOM_ERR_RE.search(blob))
                        kind = "OOM" if is_oom else "wedge"
                        logger.warning(
                            "[stt.local] GPU %s persists — transcribing this "
                            "clip on CPU (slow) so the utterance isn't dropped",
                            kind,
                        )
                        try:
                            def _cpu_transcribe():
                                return self._transcribe_sync(
                                    self._ensure_cpu_model(), wav, lang
                                )
                            text, detected = await asyncio.to_thread(_cpu_transcribe)
                        except Exception as cpu_e:
                            raise APIConnectionError(
                                f"faster-whisper local STT failed (GPU {kind}; "
                                f"CPU fallback also failed): {cpu_e}"
                            ) from cpu_e
                        # OOM may free, so keep the GPU for the next clip. A
                        # non-OOM wedge does NOT self-heal — after enough clips
                        # in a row, stick the whole session to CPU so we stop
                        # re-hitting the dead GPU (and re-firing the notification
                        # every clip, which is what spammed the desktop). A
                        # jarvis-cuda-recover + agent restart re-arms the GPU.
                        if not is_oom:
                            self._gpu_wedge_streak += 1
                            sticky_after = _gpu_sticky_after()
                            if sticky_after and self._gpu_wedge_streak >= sticky_after:
                                logger.error(
                                    "[stt.local] GPU wedged %d clips running — "
                                    "switching this session to CPU. Run "
                                    "bin/jarvis-cuda-recover and restart the "
                                    "agent to use the GPU again.",
                                    self._gpu_wedge_streak,
                                )
                                self._device = "cpu"
                                self._compute_type = "int8"  # float16 is GPU-only
                                self._model = None  # rebuild on CPU next clip
                                # Signal the user ONCE — per EPISODE, not per
                                # process. After the switch we no longer raise
                                # the wedge, so the framework's notify path
                                # never fires — this is the only heads-up that
                                # STT dropped to CPU + how to restore the GPU.
                                # The flag file persists the episode across
                                # the job-process churn (a new process per
                                # room session), so a restart doesn't reset
                                # the latch and re-notify. (Trade-off: in a
                                # rare local-PRIMARY + cloud-fallback config
                                # this also means we stay on CPU instead of
                                # cascading to a cloud STT rung; acceptable —
                                # the live box is local-only, and self-healing
                                # beats a flood.)
                                already_notified = False
                                if _wedge_persist_enabled():
                                    prior = _read_wedge_flag()
                                    already_notified = bool(
                                        prior and prior.get("notified")
                                    )
                                    _write_wedge_flag(notified=True)
                                if not already_notified:
                                    self._notify_switched_to_cpu(e)
                        break
                    raise APIConnectionError(
                        f"faster-whisper local STT failed: {e}"
                    ) from e
                attempt += 1
                delay = 0.4 * (2 ** (attempt - 1))
                if attempt >= retries:
                    # Last chance: rebuild the model → fresh CUDA context.
                    logger.warning(
                        "[stt.local] transient GPU error persists — dropping the "
                        "model for a fresh CUDA context before the final retry"
                    )
                    self._model = None
                logger.warning(
                    "[stt.local] transient GPU error (attempt %d/%d), retrying "
                    "in %.1fs: %s",
                    attempt, retries, delay, str(e)[:200],
                )
                await asyncio.sleep(delay)

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(text=text, language=detected or lang or "en")],
        )


def build_local_stt() -> FasterWhisperSTT | None:
    """Construct the local STT rung from env, or None when disabled.

    Gated on ``JARVIS_LOCAL_STT_ENABLED=1``. Defaults: large-v3-turbo on
    CPU/int8 (robust, no GPU/cuDNN dependency, fine for a last-resort
    rung; turbo — not the bigger large-v3 — is the documented live model
    and its smaller VRAM footprint leaves headroom next to the local LLM
    on the 6 GB card). ``device=auto`` is coerced to ``cpu`` to avoid VRAM
    contention with the local LLM + cuDNN requirements; set
    ``JARVIS_LOCAL_STT_DEVICE=cuda`` explicitly on a box set up for it.
    """
    if os.environ.get("JARVIS_LOCAL_STT_ENABLED", "0") != "1":
        return None
    model = (
        os.environ.get("JARVIS_LOCAL_STT_MODEL", "large-v3-turbo").strip()
        or "large-v3-turbo"
    )
    device = os.environ.get("JARVIS_LOCAL_STT_DEVICE", "cpu").strip() or "cpu"
    compute = os.environ.get("JARVIS_LOCAL_STT_COMPUTE", "int8").strip() or "int8"
    if device == "auto":
        device = "cpu"
    # Language pin — mirrors stt.py::_stt_language (same env knob, same
    # default; duplicated 2 lines rather than imported, to avoid a
    # providers.stt <-> faster_whisper_stt cycle — keep them in sync).
    # JARVIS_LANG_AUTODETECT falsy -> pin 'en'; default/truthy -> auto.
    # Live 2026-07-01 (multilingual room): auto-detect transcribed the
    # user's ENGLISH as Portuguese ("Jarvis, contem de um para dez,
    # lentamente.", detect prob 0.21) and the supervisor refused it as
    # non-English input; .env now ships JARVIS_LANG_AUTODETECT=0.
    raw = os.environ.get("JARVIS_LANG_AUTODETECT", "1").strip().lower()
    lang = "en" if raw in ("0", "false", "off", "no", "") else None
    try:
        inst = FasterWhisperSTT(
            model=model, device=device, compute_type=compute, language=lang,
        )
        # Log the INSTANCE device, not the env one — the arm-time wedge probe
        # may have coerced a wedged-GPU config to CPU (see
        # _apply_boot_wedge_state); the armed line must tell the truth.
        logger.info(
            "[stt.local] faster-whisper rung armed: model=%s device=%s compute=%s lang=%s",
            model, inst._device, inst._compute_type, lang or "auto",
        )
        return inst
    except Exception as e:
        logger.warning("[stt.local] faster-whisper construction failed: %s", e)
        return None

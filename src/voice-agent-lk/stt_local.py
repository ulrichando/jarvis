"""CPU faster-whisper STT for the headless VPS voice agent.

Adapted from the desktop agent's providers/faster_whisper_stt.py, with
the GPU/CUDA retry machinery removed — this box is CPU-only by design
(4 vCPU Hetzner VPS). Defaults to whisper `base.en` int8, which
transcribes a short utterance in well under a second on 4 threads;
override with VOICE_STT_MODEL=small.en for better accuracy at ~2-3x
the latency.

Two operating modes, chosen in build_stt() via VOICE_STT_STREAMING:

1. Streaming (default, VOICE_STT_STREAMING=1): StreamingFasterWhisperSTT
   declares STTCapabilities(streaming=True, interim_results=True), so
   AgentSession uses its .stream() directly (no StreamAdapter). While
   the user speaks, a small interim model (VOICE_STT_INTERIM_MODEL,
   default tiny.en) re-decodes the accumulated utterance audio on a
   bounded cadence and emits INTERIM_TRANSCRIPT events — the live
   "type-as-you-speak" hypothesis, smoothed with LocalAgreement-2
   (interim_text.py) so committed words don't flicker. On end of speech
   the utterance gets ONE clean decode with the main model and the full
   anti-hallucination guards, exactly like the non-streaming path.

2. Finals-only fallback (VOICE_STT_STREAMING=0): the proven
   FasterWhisperSTT; AgentSession wraps it in a StreamAdapter with the
   session's Silero VAD automatically, same as the desktop agent.

The models load in the worker prewarm hook (and are pre-downloaded into
the docker image), so first-turn latency doesn't pay the download/load
cost.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from collections import deque

from livekit import rtc
from livekit.agents import APIConnectionError, APIConnectOptions, stt
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer, is_given
from livekit.agents.vad import VAD, VADEventType

from interim_text import LocalAgreement, compose, plan_trim

logger = logging.getLogger("voice-agent.stt")

DEFAULT_MODEL = "base.en"

# ── Streaming (interim) tuning knobs — env-overridable in build_stt() ──────
# Cadence: min seconds between interim re-decodes. The decode itself is
# awaited before the next tick (single-flight), so a slow decode self-
# throttles instead of piling up; this floor just keeps a FAST decode from
# spinning the CPU. 0.5s reads as "live" without pinning a shared 4-vCPU box.
DEFAULT_INTERIM_INTERVAL = 0.5
# Window: max seconds of utterance audio re-decoded per interim tick. Past
# this, old audio is trimmed at a whisper segment boundary and its text is
# frozen in the display (see interim_text.plan_trim) — this caps the
# per-tick decode cost no matter how long the user talks.
DEFAULT_INTERIM_WINDOW = 10.0
# After a trim, keep this much recent audio (< window, so trims happen in
# chunks every few seconds instead of on every tick).
INTERIM_KEEP_RECENT_FRACTION = 0.6
# Skip an interim tick if less than this much new audio arrived since the
# last decode (nothing meaningful to add).
INTERIM_MIN_NEW_AUDIO = 0.2


class FasterWhisperSTT(stt.STT):
    """Local Whisper (faster-whisper, CPU int8) as a non-streaming livekit STT."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        language: str | None = "en",
        cpu_threads: int = 0,
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self._model_size = model
        self._language = language or None
        self._cpu_threads = cpu_threads
        self._model = None  # lazy-loaded WhisperModel
        self._load_lock = asyncio.Lock()

    @property
    def label(self) -> str:
        return f"local:faster-whisper/{self._model_size}"

    def preload(self) -> None:
        """Synchronous model load — for the worker prewarm hook, which
        runs before the job process event loop exists."""
        if self._model is None:
            from faster_whisper import WhisperModel

            logger.info(
                "[stt] preloading faster-whisper model=%s (cpu/int8)",
                self._model_size,
            )
            self._model = WhisperModel(
                self._model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=self._cpu_threads,
            )
            logger.info("[stt] model preloaded")

    async def ensure_model(self):
        """Load (or return) the ctranslate2 model. Safe to call from prewarm."""
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is None:
                def _load():
                    from faster_whisper import WhisperModel

                    return WhisperModel(
                        self._model_size,
                        device="cpu",
                        compute_type="int8",
                        cpu_threads=self._cpu_threads,
                    )

                logger.info(
                    "[stt] loading faster-whisper model=%s (cpu/int8)",
                    self._model_size,
                )
                self._model = await asyncio.to_thread(_load)
                logger.info("[stt] model loaded")
        return self._model

    def _transcribe_sync(self, model, wav: bytes, lang: str | None):
        segments, info = model.transcribe(
            io.BytesIO(wav),
            language=lang,
            beam_size=1,  # greedy — latency matters more than the last WER point
            # Whisper hallucinates confident text on non-speech audio (a cough,
            # breathing, background TV/voices) — a hands-free open mic feeds it
            # plenty, and each hallucination lands as its own chat bubble. Defence
            # in depth on top of the session's Silero endpointing:
            #   - vad_filter: whisper's own VAD trims non-speech spans per clip
            #   - no_speech / log_prob / compression thresholds: whisper's built-in
            #     "this isn't speech" gates
            #   - condition_on_previous_text=False: each clip is independent, so a
            #     hallucination can't seed a loop ("Thank you. Thank you. …")
            vad_filter=True,
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
            condition_on_previous_text=False,
        )
        # Final guard: drop any segment whisper itself is unsure is speech. This
        # is what kills the ambient "Okay.", "you", "It's not beautiful" bubbles
        # that slip past the combined-threshold rule above. If everything is
        # dropped, text == "" and the agent persists no turn.
        kept = [seg.text for seg in segments if seg.no_speech_prob < 0.6]
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
        # WAV bytes at the source sample rate; faster-whisper resamples
        # to 16k internally.
        wav = rtc.combine_audio_frames(buffer).to_wav_bytes()
        try:
            model = await self.ensure_model()
            text, detected = await asyncio.to_thread(
                self._transcribe_sync, model, wav, lang
            )
        except Exception as e:
            # Surface as an APIConnectionError so the framework's retry
            # logic treats it like any provider hiccup.
            raise APIConnectionError(f"faster-whisper STT failed: {e}") from e

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                stt.SpeechData(text=text, language=detected or lang or "en")
            ],
        )


class StreamingFasterWhisperSTT(FasterWhisperSTT):
    """Natively-streaming local whisper: live interim transcripts while the
    user speaks, guarded finals on end of speech.

    Declaring streaming=True makes AgentSession call .stream() directly
    instead of wrapping this STT in a StreamAdapter (verified against
    livekit-agents 1.6.5, Agent.default.stt_node). The stream runs its own
    Silero VAD stream (a second lightweight stream off the SAME prewarmed
    VAD instance the session uses — silero VADStreams share one ONNX
    session, each with private state), mirroring StreamAdapterWrapper:

      VAD START_OF_SPEECH  -> emit START_OF_SPEECH, start interim ticking
      VAD INFERENCE_DONE   -> accumulate utterance audio (while speaking)
      (interim tick, bounded cadence) -> re-decode utterance window with the
          small interim model, emit INTERIM_TRANSCRIPT (LocalAgreement-2
          smoothed, self-revising)
      VAD END_OF_SPEECH    -> emit END_OF_SPEECH, then ONE clean decode of
          the VAD's complete utterance frames with the main model and the
          production anti-hallucination guards -> FINAL_TRANSCRIPT

    The FINAL path is byte-identical to the StreamAdapter path (same VAD
    end-of-speech frames, same _transcribe_sync guards), so final accuracy
    cannot regress. Interims are best-effort display text and are fully
    replaced by the final.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        language: str | None = "en",
        cpu_threads: int = 0,
        vad: VAD,
        interim_model: str = "tiny.en",
        interim_interval: float = DEFAULT_INTERIM_INTERVAL,
        interim_window: float = DEFAULT_INTERIM_WINDOW,
    ) -> None:
        super().__init__(model=model, language=language, cpu_threads=cpu_threads)
        # Re-declare capabilities: base class pinned streaming=False.
        self._capabilities = stt.STTCapabilities(streaming=True, interim_results=True)
        self._vad = vad
        self._interim_model_size = interim_model
        self._interim_interval = interim_interval
        self._interim_window = interim_window
        self._interim_model = None  # lazy-loaded WhisperModel (may alias main)
        self._interim_load_lock = asyncio.Lock()

    @property
    def label(self) -> str:
        return (
            f"local:faster-whisper/{self._model_size}"
            f"+interim:{self._interim_model_size}"
        )

    def preload(self) -> None:
        """Load BOTH models in the worker prewarm hook."""
        super().preload()
        if self._interim_model is None:
            if self._interim_model_size == self._model_size:
                self._interim_model = self._model
                return
            from faster_whisper import WhisperModel

            logger.info(
                "[stt] preloading interim faster-whisper model=%s (cpu/int8)",
                self._interim_model_size,
            )
            self._interim_model = WhisperModel(
                self._interim_model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=self._cpu_threads,
            )
            logger.info("[stt] interim model preloaded")

    async def ensure_interim_model(self):
        """Load (or return) the interim ctranslate2 model."""
        if self._interim_model is not None:
            return self._interim_model
        if self._interim_model_size == self._model_size:
            self._interim_model = await self.ensure_model()
            return self._interim_model
        async with self._interim_load_lock:
            if self._interim_model is None:
                def _load():
                    from faster_whisper import WhisperModel

                    return WhisperModel(
                        self._interim_model_size,
                        device="cpu",
                        compute_type="int8",
                        cpu_threads=self._cpu_threads,
                    )

                logger.info(
                    "[stt] loading interim faster-whisper model=%s (cpu/int8)",
                    self._interim_model_size,
                )
                self._interim_model = await asyncio.to_thread(_load)
                logger.info("[stt] interim model loaded")
        return self._interim_model

    def _transcribe_interim_sync(self, model, wav: bytes, lang: str | None):
        """Best-effort interim decode. Looser than _transcribe_sync (no
        whisper-internal vad_filter — the external Silero VAD already gates
        what reaches us, and skipping it saves CPU on every tick) but keeps
        the cheap threshold guards + segment no_speech filter so a breath
        doesn't flash hallucinated text on screen. Returns (text, segments,
        detected_language) where segments = [(start, end, text)] in
        window-relative seconds — used to pick trim boundaries."""
        segments, info = model.transcribe(
            io.BytesIO(wav),
            language=lang,
            beam_size=1,
            vad_filter=False,
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
            condition_on_previous_text=False,
        )
        kept: list[str] = []
        seg_meta: list[tuple[float, float, str]] = []
        for seg in segments:
            if seg.no_speech_prob >= 0.6:
                continue
            kept.append(seg.text)
            seg_meta.append((float(seg.start), float(seg.end), seg.text.strip()))
        text = "".join(kept).strip()
        return text, seg_meta, getattr(info, "language", None)

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.RecognizeStream:
        return _StreamingSpeechStream(
            self, language=language, conn_options=conn_options
        )


class _StreamingSpeechStream(stt.RecognizeStream):
    """The per-session speech stream: consumes pushed frames, runs VAD +
    interim/final decodes, emits SpeechEvents (see class doc above)."""

    def __init__(
        self,
        stt_inst: StreamingFasterWhisperSTT,
        *,
        language: NotGivenOr[str],
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(stt=stt_inst, conn_options=conn_options)
        self._whisper = stt_inst
        lang = language if is_given(language) else None
        self._language = lang or stt_inst._language
        self._interim_language = (
            "en"
            if stt_inst._interim_model_size.endswith(".en")
            else self._language
        )

    async def _run(self) -> None:
        vad_stream = self._whisper._vad.stream()

        # ── per-utterance state (reset on each VAD START_OF_SPEECH) ────────
        self._speech_flag = asyncio.Event()  # set while an utterance is live
        self._utt_gen = 0            # bumped on SOS/EOS; stale decodes drop out
        self._frames: deque[rtc.AudioFrame] = deque()  # buffered window audio
        self._utt_dur = 0.0          # abs utterance-time at buffer end
        self._buf_start = 0.0        # abs utterance-time at buffer head
        self._decoded_upto = 0.0     # abs utterance-time covered by last decode
        self._frozen = ""            # display text for audio trimmed out
        self._agreement = LocalAgreement()
        self._segments: list[tuple[float, float, str]] = []  # abs-time segs

        async def _forward_input() -> None:
            async for data in self._input_ch:
                if isinstance(data, self._FlushSentinel):
                    vad_stream.flush()
                    continue
                vad_stream.push_frame(data)
            vad_stream.end_input()

        forward_task = asyncio.create_task(_forward_input(), name="stt_forward_input")
        interim_task = asyncio.create_task(self._interim_loop(), name="stt_interim_loop")
        try:
            await self._vad_loop(vad_stream)
        finally:
            interim_task.cancel()
            forward_task.cancel()
            await asyncio.gather(interim_task, forward_task, return_exceptions=True)
            await vad_stream.aclose()

    # ── VAD event loop: utterance boundaries + the guarded FINAL ──────────
    async def _vad_loop(self, vad_stream) -> None:
        async for ev in vad_stream:
            if ev.type == VADEventType.START_OF_SPEECH:
                self._utt_gen += 1
                self._frames.clear()
                # ev.frames = Silero's speech buffer so far (prefix padding +
                # the ~min_speech_duration trigger). Subsequent INFERENCE_DONE
                # events carry strictly newer windows (verified against
                # livekit-plugins-silero 1.6.5).
                dur = 0.0
                for f in ev.frames:
                    self._frames.append(f)
                    dur += f.samples_per_channel / f.sample_rate
                self._utt_dur = dur
                self._buf_start = 0.0
                self._decoded_upto = 0.0
                self._frozen = ""
                self._agreement.reset()
                self._segments = []
                self._speech_flag.set()
                self._event_ch.send_nowait(
                    stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH)
                )

            elif ev.type == VADEventType.INFERENCE_DONE:
                if self._speech_flag.is_set():
                    for f in ev.frames:
                        self._frames.append(f)
                        self._utt_dur += f.samples_per_channel / f.sample_rate

            elif ev.type == VADEventType.END_OF_SPEECH:
                self._utt_gen += 1  # invalidate any in-flight interim decode
                self._speech_flag.clear()
                self._event_ch.send_nowait(
                    stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH)
                )
                # ONE clean final decode of the complete utterance, exactly
                # like the StreamAdapter path: the VAD's own end-of-speech
                # frames (padding + full speech), the MAIN model, and the
                # full anti-hallucination guards in _transcribe_sync.
                wav = rtc.combine_audio_frames(ev.frames).to_wav_bytes()
                audio_dur = sum(
                    f.samples_per_channel / f.sample_rate for f in ev.frames
                )
                try:
                    model = await self._whisper.ensure_model()
                    text, detected = await asyncio.to_thread(
                        self._whisper._transcribe_sync, model, wav, self._language
                    )
                except Exception as e:
                    raise APIConnectionError(
                        f"faster-whisper STT failed: {e}"
                    ) from e
                if text:
                    self._event_ch.send_nowait(
                        stt.SpeechEvent(
                            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                            alternatives=[
                                stt.SpeechData(
                                    text=text,
                                    language=detected or self._language or "en",
                                )
                            ],
                        )
                    )
                self._event_ch.send_nowait(
                    stt.SpeechEvent(
                        type=stt.SpeechEventType.RECOGNITION_USAGE,
                        recognition_usage=stt.RecognitionUsage(
                            audio_duration=audio_dur
                        ),
                    )
                )

    # ── Interim loop: bounded-cadence, single-flight re-decode ────────────
    async def _interim_loop(self) -> None:
        interval = self._whisper._interim_interval
        while True:
            await self._speech_flag.wait()
            gen = self._utt_gen
            while self._speech_flag.is_set() and gen == self._utt_gen:
                t0 = time.monotonic()
                try:
                    await self._interim_tick(gen)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    # Interims are best-effort: log and back off, never kill
                    # the stream (the final path has its own error handling).
                    logger.warning("[stt] interim decode failed: %s", e)
                    await asyncio.sleep(interval * 4)
                elapsed = time.monotonic() - t0
                await asyncio.sleep(max(interval - elapsed, 0.05))

    async def _interim_tick(self, gen: int) -> None:
        if self._utt_dur - self._decoded_upto < INTERIM_MIN_NEW_AUDIO:
            return  # nothing meaningful arrived since the last decode
        self._maybe_trim()
        frames = list(self._frames)
        if not frames:
            return
        snap_start = self._buf_start
        snap_end = self._utt_dur
        wav = rtc.combine_audio_frames(frames).to_wav_bytes()
        model = await self._whisper.ensure_interim_model()
        text, segs, detected = await asyncio.to_thread(
            self._whisper._transcribe_interim_sync,
            model,
            wav,
            self._interim_language,
        )
        if gen != self._utt_gen or not self._speech_flag.is_set():
            return  # utterance ended while we were decoding — stale, drop
        self._decoded_upto = snap_end
        # Segment times are window-relative; store absolute utterance-time.
        self._segments = [
            (snap_start + s, snap_start + e, t) for s, e, t in segs
        ]
        if not text:
            return
        display = compose(self._frozen, self._agreement.update(text.split()))
        if not display:
            return
        self._event_ch.send_nowait(
            stt.SpeechEvent(
                type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                alternatives=[
                    stt.SpeechData(
                        text=display,
                        language=detected or self._language or "en",
                    )
                ],
            )
        )

    def _maybe_trim(self) -> None:
        """Cap the interim decode window: cut old audio at a segment boundary,
        freeze its text into the display prefix, restart LocalAgreement over
        the remaining window. Bounds per-tick decode cost for long speech."""
        window = self._whisper._interim_window
        plan = plan_trim(
            self._segments,
            self._buf_start,
            self._utt_dur,
            window,
            window * INTERIM_KEEP_RECENT_FRACTION,
        )
        if plan is None:
            return
        cut, frozen_add = plan
        if frozen_add:
            self._frozen = compose(self._frozen, frozen_add)
        self._agreement.reset()
        while self._frames:
            f = self._frames[0]
            fd = f.samples_per_channel / f.sample_rate
            if self._buf_start + fd > cut:
                break
            self._frames.popleft()
            self._buf_start += fd
        self._segments = [s for s in self._segments if s[1] > cut]


def build_stt(*, vad: VAD | None = None) -> FasterWhisperSTT:
    """Construct the STT from env.

    Env:
      VOICE_STT_MODEL            main (finals) model, default base.en
      VOICE_STT_LANGUAGE         pinned language, default en ("auto" to detect)
      VOICE_STT_STREAMING        1 (default) = live interim transcripts;
                                 0 = proven finals-only StreamAdapter path
      VOICE_STT_INTERIM_MODEL    interim model, default tiny.en (tiny when
                                 the language isn't pinned to English)
      VOICE_STT_INTERIM_INTERVAL min seconds between interim decodes,
                                 default 0.5 (clamped to [0.25, 2.0])
      VOICE_STT_INTERIM_WINDOW   max seconds of audio per interim decode,
                                 default 10 (clamped to [4, 30])

    `vad` is the prewarmed Silero VAD instance (the streaming STT opens its
    own stream off it for utterance boundaries). Without it, streaming is
    unavailable and the finals-only STT is returned.
    """
    model = os.environ.get("VOICE_STT_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    # Pin English by default — whisper auto-detect on short clips is
    # unreliable (the desktop agent learned this live; JARVIS_LANG_AUTODETECT=0
    # is its shipped default). Set VOICE_STT_LANGUAGE=auto to re-enable.
    raw_lang = os.environ.get("VOICE_STT_LANGUAGE", "en").strip().lower()
    lang = None if raw_lang in ("auto", "") else raw_lang
    # .en models reject a language pin other than en; guard the mismatch.
    if model.endswith(".en"):
        lang = "en"

    streaming_raw = os.environ.get("VOICE_STT_STREAMING", "1").strip().lower()
    streaming = streaming_raw not in ("0", "false", "no", "off")
    if streaming and vad is None:
        logger.warning(
            "[stt] VOICE_STT_STREAMING is on but no VAD was provided; "
            "falling back to the finals-only STT"
        )
        streaming = False

    if not streaming:
        inst = FasterWhisperSTT(model=model, language=lang)
        logger.info("[stt] armed: model=%s lang=%s (finals only)", model, lang or "auto")
        return inst

    # English pinned -> tiny.en (fastest, better English WER than tiny);
    # anything else -> multilingual tiny.
    default_interim = "tiny.en" if lang == "en" else "tiny"
    interim_model = (
        os.environ.get("VOICE_STT_INTERIM_MODEL", "").strip() or default_interim
    )

    def _env_float(name: str, default: float, lo: float, hi: float) -> float:
        try:
            v = float(os.environ.get(name, "").strip() or default)
        except ValueError:
            v = default
        return min(max(v, lo), hi)

    interval = _env_float(
        "VOICE_STT_INTERIM_INTERVAL", DEFAULT_INTERIM_INTERVAL, 0.25, 2.0
    )
    window = _env_float("VOICE_STT_INTERIM_WINDOW", DEFAULT_INTERIM_WINDOW, 4.0, 30.0)

    assert vad is not None
    inst = StreamingFasterWhisperSTT(
        model=model,
        language=lang,
        vad=vad,
        interim_model=interim_model,
        interim_interval=interval,
        interim_window=window,
    )
    logger.info(
        "[stt] armed: model=%s lang=%s streaming interim=%s interval=%.2fs window=%.1fs",
        model, lang or "auto", interim_model, interval, window,
    )
    return inst

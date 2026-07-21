"""Smart Turn v3 raw-audio end-of-turn (EOU) detector — voice-setup todo #2.

Replaces fixed-silence endpointing with a LEARNED "did the user actually
finish, or just pause mid-thought?" signal, so JARVIS stops both (a) leaving
dead air after the user is clearly done and (b) cutting the user off on a
natural pause. Model: Pipecat's Smart Turn v3 (Whisper-Tiny encoder + linear
head, ~8.7 MB int8 ONNX, ~12 ms CPU, BSD-2). It answers "is the turn
complete?" — NOT "was this addressed to me?" (that's the directedness gate,
todo #3). Complements VAD; does not replace it.

**The seam.** LiveKit 1.5.17's `AgentSession(turn_detection=<obj>)` calls
`predict_end_of_turn(chat_ctx)` at exactly the right moment (after VAD silence,
once an STT transcript exists) and, if the returned probability is below
`unlikely_threshold`, extends the endpointing wait from `min_delay` to
`max_delay` (i.e. waits for the user to continue). The catch: that seam passes
only the TEXT chat context, never audio. So this detector IGNORES chat_ctx and
reads a JARVIS-side rolling PCM buffer (`EOUAudioBuffer`) fed from the existing
stt_node tee — the same audio path the partial-barge-in tap rides. That
sidesteps monkey-patching LiveKit's endpointing internals.

**Fail-safe.** LiveKit only extends the wait when the returned probability is
< threshold. An EXCEPTION inside predict_end_of_turn leaves LiveKit at
min_delay (unchanged VAD behaviour). So on ANY internal error/timeout/empty
buffer we return 1.0 ("finished now") → min_delay → identical to today.

**Gating.** Opt-in: `JARVIS_SMART_TURN=1`. Default (unset) → build returns None
→ `turn_detection=None` → LiveKit behaves exactly as before. Knobs:
  - JARVIS_SMART_TURN_MODEL      path to the .onnx (default ~/.jarvis/models/smart-turn-v3.2-cpu.onnx)
  - JARVIS_SMART_TURN_THRESHOLD  P(complete) below which we wait longer (default 0.5)
  - JARVIS_SMART_TURN_TIMEOUT_S  inference deadline; exceeded → fail-safe finalize (default 0.3)
"""
from __future__ import annotations

import logging
import math
import os
from collections import deque

import numpy as np

logger = logging.getLogger("jarvis.smart_turn")

_SR = 16000
_MAX_SAMPLES = _SR * 8  # 128000 — Smart Turn's 8 s context window
_MIN_SAMPLES = int(_SR * 0.2)  # <200 ms of audio: nothing worth judging


def _model_path() -> str:
    return os.path.expanduser(
        os.environ.get(
            "JARVIS_SMART_TURN_MODEL", "~/.jarvis/models/smart-turn-v3.2-cpu.onnx"
        )
    )


def enabled() -> bool:
    return os.environ.get("JARVIS_SMART_TURN", "0") == "1"


def _threshold() -> float:
    try:
        return float(os.environ.get("JARVIS_SMART_TURN_THRESHOLD", "0.5"))
    except (TypeError, ValueError):
        return 0.5


def _timeout_s() -> float:
    try:
        return float(os.environ.get("JARVIS_SMART_TURN_TIMEOUT_S", "0.3"))
    except (TypeError, ValueError):
        return 0.3


class EOUAudioBuffer:
    """Rolling ~8 s ring of the current user's 16 kHz mono PCM, fed one
    rtc.AudioFrame at a time from the stt_node tee. Runs entirely on the agent
    event loop (feed + snapshot), so no lock is needed; the ONNX inference reads
    a detached float32 copy off-thread.

    ponytail: no per-turn reset — the ring self-bounds to the last 8 s and no
    frames flow during silence (VAD-closed / mic-dropped), so it naturally holds
    the current turn's tail. Add a reset() call in on_user_turn_completed if a
    short follow-up after a long prior turn ever mis-judges."""

    def __init__(self) -> None:
        self._chunks: deque[np.ndarray] = deque()
        self._n = 0

    def feed_frame(self, frame) -> None:
        try:
            if getattr(frame, "sample_rate", _SR) != _SR:
                return  # non-16k frame: skip (STT chain is 16k; guard anyway)
            pcm = np.frombuffer(frame.data, dtype=np.int16)
            if pcm.size == 0:
                return
            self._chunks.append(pcm)
            self._n += pcm.size
            while self._n > _MAX_SAMPLES and len(self._chunks) > 1:
                self._n -= self._chunks.popleft().size
        except Exception:
            pass  # never let buffering break the STT path

    def snapshot(self) -> np.ndarray:
        """Last ≤8 s as float32 in [-1, 1]; empty array if nothing buffered."""
        if not self._chunks:
            return np.zeros(0, dtype=np.float32)
        pcm = np.concatenate(self._chunks)[-_MAX_SAMPLES:]
        return pcm.astype(np.float32) / 32768.0

    def reset(self) -> None:
        self._chunks.clear()
        self._n = 0


class SmartTurnEOU:
    """A LiveKit `_TurnDetector` whose end-of-turn decision comes from raw audio
    (Smart Turn v3) rather than the transcript. See module docstring."""

    def __init__(self, buffer: EOUAudioBuffer, onnx_session) -> None:
        self.buffer = buffer
        self._sess = onnx_session
        self.last_prob: float | None = None

    # --- _TurnDetector protocol -------------------------------------------
    @property
    def model(self) -> str:
        return "smart-turn-v3.2-cpu"

    @property
    def provider(self) -> str:
        return "pipecat"

    async def supports_language(self, language) -> bool:
        return True  # Smart Turn v3 is multilingual (23 langs)

    async def unlikely_threshold(self, language) -> float | None:
        # Below this P(complete), LiveKit waits (min_delay -> max_delay).
        return _threshold()

    async def predict_end_of_turn(self, chat_ctx, *, timeout: float | None = None) -> float:
        """Return P(user finished). chat_ctx is deliberately ignored — the
        signal is audio. On any failure return 1.0 so LiveKit stays at
        min_delay (unchanged VAD behaviour)."""
        import asyncio

        try:
            audio = self.buffer.snapshot()
            if audio.size < _MIN_SAMPLES:
                return 1.0  # ~nothing captured — don't hold the turn open
            deadline = timeout or _timeout_s()
            prob = await asyncio.wait_for(asyncio.to_thread(self._run, audio), timeout=deadline)
            self.last_prob = prob
            logger.info(
                "[smart-turn] p(complete)=%.3f thr=%.2f -> %s",
                prob, _threshold(), "end" if prob >= _threshold() else "wait",
            )
            return prob
        except Exception as e:  # timeout, onnx error, etc. — CancelledError propagates
            logger.debug("[smart-turn] fail-safe finalize: %s", e)
            return 1.0

    def _run(self, audio: np.ndarray) -> float:
        from pipeline._whisper_features import compute_whisper_log_mel_features

        audio = audio[-_MAX_SAMPLES:]  # last 8 s (the pause is at the tail)
        logmel = compute_whisper_log_mel_features(audio, do_normalize=True)  # (80, 800)
        x = np.expand_dims(logmel, 0).astype(np.float32)  # (1, 80, 800)
        logit = float(self._sess.run(["logits"], {"input_features": x})[0].reshape(-1)[0])
        return 1.0 / (1.0 + math.exp(-logit))  # ONNX emits a raw logit


def build_smart_turn_detector() -> SmartTurnEOU | None:
    """Factory. Returns None (→ unchanged behaviour) when disabled, the model
    file is missing, or onnxruntime init fails — never raises."""
    if not enabled():
        return None
    try:
        import onnxruntime as ort

        path = _model_path()
        if not os.path.exists(path):
            logger.warning(
                "[smart-turn] JARVIS_SMART_TURN=1 but model missing at %s — "
                "disabled (download smart-turn-v3.2-cpu.onnx there)", path,
            )
            return None
        so = ort.SessionOptions()
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.inter_op_num_threads = 1
        so.intra_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess = ort.InferenceSession(path, sess_options=so, providers=["CPUExecutionProvider"])
        det = SmartTurnEOU(buffer=EOUAudioBuffer(), onnx_session=sess)
        logger.info("[smart-turn] EOU detector armed (model=%s thr=%.2f)", path, _threshold())
        return det
    except Exception as e:
        logger.warning("[smart-turn] init failed, disabled: %s", e)
        return None

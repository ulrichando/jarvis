"""JARVIS Ambient Listening — always-on, server-side speech detection.

The browser streams raw audio to the server via WebSocket.
The server detects speech boundaries using energy + zero-crossing rate (ZCR),
then transcribes only the speech segments with Whisper.

No mic button. No wake word. No browser Speech API.
JARVIS just knows when you're talking — like a human in the room.

Innovation: This runs entirely server-side, so it works in ANY browser,
ANY webview (Tauri, Electron, mobile), even over SSH tunnels.

Barge-in mode: when JARVIS is speaking, ambient listening stays active but
uses a higher energy + ZCR gate so background noise and JARVIS's own TTS echo
won't trigger false interruptions, while a real human voice speaking over JARVIS
still gets detected quickly.
"""

import numpy as np
import time
from collections import deque
from src.speech.stt import transcribe_audio, _is_hallucination, _has_speech_energy


def _compute_zcr(chunk: np.ndarray) -> float:
    """Zero-Crossing Rate — fraction of samples that cross zero.

    Pure noise and fans have high ZCR (random sign flips).
    Human voice sits in 0.02 – 0.45.
    Keyboard clicks have very short duration + extreme ZCR spikes.
    """
    if len(chunk) < 2:
        return 0.0
    crossings = np.sum(np.diff(np.signbit(chunk.astype(np.float32))))
    return float(crossings) / (len(chunk) - 1)


# ZCR band that corresponds to human speech (heuristic, works well at 16 kHz)
_ZCR_VOICE_LOW  = 0.015
_ZCR_VOICE_HIGH = 0.50


class AmbientListener:
    """Server-side always-on speech detection and transcription.

    Receives audio chunks from the browser, detects speech boundaries,
    and triggers transcription when a complete utterance is captured.

    Two operating modes controlled by ``jarvis_speaking``:
      - Normal mode  : standard threshold + ZCR gate
      - Barge-in mode: raised threshold (3× noise floor) + stricter ZCR gate
                       so JARVIS's TTS audio leaking into the mic doesn't
                       trigger false barge-ins, but a real human voice will.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        energy_threshold: float = 0.035,      # Minimum — calibration may raise further
        silence_duration: float = 1.5,         # Seconds of silence = end of utterance
        min_speech_duration: float = 0.8,      # Min seconds to be valid speech
        max_speech_duration: float = 12.0,     # Max seconds before forced cutoff
        pre_speech_buffer: float = 0.3,        # Seconds of audio to keep before speech
        barge_in_energy_factor: float = 5.0,   # How much louder barge-in must be vs noise
        min_barge_in_duration: float = 0.4,    # Min speech seconds to count as barge-in
    ):
        self.sample_rate = sample_rate
        self.energy_threshold = energy_threshold
        self.silence_duration = silence_duration
        self.barge_in_energy_factor = barge_in_energy_factor
        self.min_barge_in_duration = min_barge_in_duration

        # Adaptive noise floor
        self._noise_floor = 0.0
        self._noise_samples = 0
        self._calibrated = False
        # Post-TTS cooldown: ignore audio for this many seconds after TTS stops
        # to let the sound card drain and prevent JARVIS hearing his own voice.
        self._cooldown_until: float = 0.0

        self.min_speech_duration = min_speech_duration
        self.max_speech_duration = max_speech_duration

        # State
        self.is_speaking = False
        self.speech_buffer: list[np.ndarray] = []
        self.silence_start: float = 0
        self.speech_start: float = 0

        # Pre-speech ring buffer (keeps last N seconds of audio before speech onset)
        pre_samples = int(pre_speech_buffer * sample_rate)
        self._pre_buffer_target_samples = pre_samples
        self._pre_buffer: deque[np.ndarray] = deque(maxlen=20)

        # Barge-in state
        self.jarvis_speaking = False
        self._barge_in_start: float = 0.0
        self._barge_in_consecutive: int = 0   # Consecutive energetic frames during TTS

        # Callback for when a complete utterance is detected (async)
        self.on_utterance = None  # async callable(audio: np.ndarray) -> None

        # Barge-in callback — called immediately when barge-in is confirmed
        # (before the full utterance is available).  Useful for instant TTS kill.
        self.on_barge_in = None  # callable() -> None  (sync, called from feed())

    # ── Public interface ────────────────────────────────────────────────────

    def feed(self, audio_chunk: np.ndarray) -> str | None:
        """Feed an audio chunk. Returns transcription if utterance complete, else None.

        In barge-in mode (jarvis_speaking=True), calls ``on_barge_in()`` as soon as
        voice energy is confirmed above the higher threshold, without waiting for the
        full utterance.  The full utterance is still accumulated and transcribed so
        the server knows what the user said.
        """
        # ── Resize pre-buffer on first chunk ──────────────────────────────
        if self._noise_samples == 0 and len(audio_chunk) > 0:
            chunks_needed = max(1, self._pre_buffer_target_samples // len(audio_chunk))
            self._pre_buffer = deque(maxlen=chunks_needed)

        # ── Energy + ZCR ──────────────────────────────────────────────────
        float_chunk = audio_chunk.astype(np.float64)
        rms = float(np.sqrt(np.mean(float_chunk ** 2)))
        zcr = _compute_zcr(audio_chunk)
        now = time.time()

        # ── Adaptive calibration: first ~2 seconds of silence ──────────────
        if not self._calibrated:
            self._noise_floor = (
                self._noise_floor * self._noise_samples + rms
            ) / (self._noise_samples + 1)
            self._noise_samples += 1
            cal_frames = int(2.0 * self.sample_rate / max(1, len(audio_chunk)))
            if self._noise_samples > cal_frames:
                self.energy_threshold = max(0.035, self._noise_floor * 6.0)
                self._calibrated = True
                print(
                    f"[JARVIS] Ambient calibrated: "
                    f"noise_floor={self._noise_floor:.6f}, "
                    f"threshold={self.energy_threshold:.6f}"
                )
            return None

        # ── Post-TTS cooldown: drain sound card, ignore leaking TTS audio ──
        if now < self._cooldown_until:
            self._pre_buffer.append(audio_chunk)
            return None

        # ── ZCR gate — reject non-voice sounds ────────────────────────────
        voice_zcr = _ZCR_VOICE_LOW <= zcr <= _ZCR_VOICE_HIGH

        # ── Barge-in mode: JARVIS is currently speaking via TTS ───────────
        if self.jarvis_speaking:
            barge_threshold = max(self.energy_threshold, self._noise_floor * self.barge_in_energy_factor)
            if rms > barge_threshold and voice_zcr:
                self._barge_in_consecutive += 1
                if self._barge_in_start == 0:
                    self._barge_in_start = now
                # Accumulate for later transcription
                if not self.is_speaking:
                    self.is_speaking = True
                    self.speech_start = now
                    self.speech_buffer = list(self._pre_buffer)
                self.speech_buffer.append(audio_chunk)

                # Confirmed barge-in once we have enough consecutive energetic frames
                barge_in_confirmed = (
                    now - self._barge_in_start >= self.min_barge_in_duration
                    and self._barge_in_consecutive >= 3
                )
                if barge_in_confirmed and callable(self.on_barge_in):
                    # Fire once — reset flag so it doesn't fire again for this barge-in
                    cb = self.on_barge_in
                    self.on_barge_in = None
                    cb()
            else:
                # During TTS: adapt noise floor quickly to track speaker output level.
                # This raises the barge-in threshold to match what the mic hears from
                # the speakers, so JARVIS's own voice doesn't trigger barge-in.
                self._noise_floor = self._noise_floor * 0.97 + rms * 0.03
                if rms <= self._noise_floor * 1.2:
                    self._barge_in_consecutive = 0
                    if self._barge_in_start and (now - self._barge_in_start > 0.5):
                        self._barge_in_start = 0.0
                self._pre_buffer.append(audio_chunk)
            return None

        # ── Normal mode ────────────────────────────────────────────────────
        # Reset barge-in counters now that JARVIS is silent
        self._barge_in_start = 0.0
        self._barge_in_consecutive = 0

        # Continuously adapt noise floor during silence (slow decay)
        if not self.is_speaking:
            self._noise_floor = self._noise_floor * 0.995 + rms * 0.005

        # Speech onset: energy above threshold AND voice ZCR band
        if rms > self.energy_threshold and voice_zcr:
            if not self.is_speaking:
                self.is_speaking = True
                self.speech_start = now
                self.silence_start = 0
                self.speech_buffer = list(self._pre_buffer)

            self.speech_buffer.append(audio_chunk)
            self.silence_start = 0

            if now - self.speech_start > self.max_speech_duration:
                return self._finalize()

        else:
            if self.is_speaking:
                self.speech_buffer.append(audio_chunk)
                if self.silence_start == 0:
                    self.silence_start = now
                elif now - self.silence_start > self.silence_duration:
                    return self._finalize()
            else:
                self._pre_buffer.append(audio_chunk)

        return None

    def _finalize(self) -> str | None:
        """Finalize the current utterance — transcribe it."""
        if not self.speech_buffer:
            self.is_speaking = False
            return None

        audio = np.concatenate(self.speech_buffer)

        self.speech_buffer.clear()
        self.is_speaking = False
        self.silence_start = 0
        self.speech_start = 0

        # Minimum duration check
        duration = len(audio) / self.sample_rate
        if duration < self.min_speech_duration:
            return None

        if not _has_speech_energy(audio, self.energy_threshold * 0.5):
            return None

        try:
            text = transcribe_audio(audio, self.sample_rate)
            if text and len(text.strip()) > 1 and not _is_hallucination(text):
                return text.strip()
        except Exception:
            pass

        return None

    def set_jarvis_speaking(self, speaking: bool):
        """Tell the listener when JARVIS is speaking.

        In speaking mode the listener stays active (barge-in), but uses a
        raised threshold and ZCR gate so JARVIS's own TTS doesn't self-trigger.
        """
        self.jarvis_speaking = speaking
        if not speaking:
            # TTS ended — discard all audio accumulated during playback.
            # That audio is JARVIS's own voice or TTS echo; transcribing it
            # produces hallucinations ("You're terrifying", etc.).  Start fresh
            # so the next real utterance is captured cleanly.
            self.is_speaking = False
            self.speech_buffer.clear()
            self._pre_buffer.clear()
            self.speech_start = 0
            self.silence_start = 0
            self._barge_in_start = 0.0
            self._barge_in_consecutive = 0
            # Cooldown: ignore all audio for 1.5s so the sound card drains
            # and any lingering TTS echo doesn't get transcribed as user speech.
            self._cooldown_until = time.time() + 1.5
            # Decay noise floor back toward a quiet-room level so the threshold
            # doesn't stay inflated after TTS ends.
            self._noise_floor = min(self._noise_floor, self.energy_threshold * 0.3)
        else:
            # TTS starting — clear any leftover speech from before TTS began
            if not self.is_speaking:
                self.speech_buffer.clear()
                self._pre_buffer.clear()

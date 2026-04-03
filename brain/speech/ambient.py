"""JARVIS Ambient Listening — always-on, server-side speech detection.

The browser streams raw audio to the server via WebSocket.
The server detects speech boundaries using energy + zero-crossing,
then transcribes only the speech segments with Whisper.

No mic button. No wake word. No browser Speech API.
JARVIS just knows when you're talking — like a human in the room.

Innovation: This runs entirely server-side, so it works in ANY browser,
ANY webview (Tauri, Electron, mobile), even over SSH tunnels.
"""

import numpy as np
import time
from collections import deque
from brain.speech.stt import transcribe_audio, _is_hallucination, _has_speech_energy


class AmbientListener:
    """Server-side always-on speech detection and transcription.

    Receives audio chunks from the browser, detects speech boundaries,
    and triggers transcription when a complete utterance is captured.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        energy_threshold: float = 0.005,     # Very low — adaptive calibration raises it
        silence_duration: float = 0.8,        # Seconds of silence = end of utterance (fast response)
        min_speech_duration: float = 0.3,     # Min seconds to be valid speech
        max_speech_duration: float = 30.0,    # Max seconds before forced cutoff
        pre_speech_buffer: float = 0.3,       # Seconds of audio to keep before speech starts
    ):
        self.sample_rate = sample_rate
        self.energy_threshold = energy_threshold
        self.silence_duration = silence_duration
        # Adaptive noise floor
        self._noise_floor = 0.0
        self._noise_samples = 0
        self._calibrated = False
        self.min_speech_duration = min_speech_duration
        self.max_speech_duration = max_speech_duration

        # State
        self.is_speaking = False
        self.speech_buffer: list[np.ndarray] = []
        self.silence_start: float = 0
        self.speech_start: float = 0

        # Pre-speech ring buffer (keeps last N seconds of audio)
        # Size is dynamic — calculated on first chunk received
        pre_samples = int(pre_speech_buffer * sample_rate)
        self._pre_buffer_target_samples = pre_samples
        self._pre_buffer: deque[np.ndarray] = deque(maxlen=20)  # default, resized on first chunk

        # JARVIS speaking flag — ignore audio while JARVIS talks
        self.jarvis_speaking = False

        # Callback for when a complete utterance is detected
        self.on_utterance = None  # async callable(audio: np.ndarray) -> None

    def feed(self, audio_chunk: np.ndarray) -> str | None:
        """Feed an audio chunk. Returns transcription if utterance complete, else None.

        This is called synchronously from the WebSocket handler for each chunk.
        For async usage, set self.on_utterance callback.
        """
        # Ignore audio while JARVIS is speaking (prevents echo)
        if self.jarvis_speaking:
            self._pre_buffer.clear()
            self.speech_buffer.clear()
            self.is_speaking = False
            return None

        # Resize pre-buffer on first chunk to match actual chunk size
        if self._noise_samples == 0 and len(audio_chunk) > 0:
            chunks_needed = max(1, self._pre_buffer_target_samples // len(audio_chunk))
            self._pre_buffer = deque(maxlen=chunks_needed)

        # Calculate energy
        rms = np.sqrt(np.mean(audio_chunk.astype(np.float64) ** 2))
        now = time.time()

        # Adaptive calibration: measure noise floor for first ~1 second (faster boot)
        if not self._calibrated:
            self._noise_floor = (self._noise_floor * self._noise_samples + rms) / (self._noise_samples + 1)
            self._noise_samples += 1
            if self._noise_samples > int(1.0 * self.sample_rate / max(1, len(audio_chunk))):
                # Threshold = 2x noise floor — needs clear speech above background
                self.energy_threshold = max(0.005, self._noise_floor * 2.0)
                self._calibrated = True
                print(f"[JARVIS] Ambient calibrated: noise_floor={self._noise_floor:.6f}, threshold={self.energy_threshold:.6f}")
            return None

        # Continuously adapt noise floor during silence (slow decay)
        if not self.is_speaking:
            self._noise_floor = self._noise_floor * 0.995 + rms * 0.005

        if rms > self.energy_threshold:
            # Speech detected
            if not self.is_speaking:
                # Speech just started — grab pre-buffer for context
                self.is_speaking = True
                self.speech_start = now
                self.silence_start = 0
                # Include pre-speech audio for natural start
                self.speech_buffer = list(self._pre_buffer)

            self.speech_buffer.append(audio_chunk)
            self.silence_start = 0  # Reset silence counter

            # Check max duration
            if now - self.speech_start > self.max_speech_duration:
                return self._finalize()

        else:
            # Silence
            if self.is_speaking:
                self.speech_buffer.append(audio_chunk)  # Keep buffering during silence gap

                if self.silence_start == 0:
                    self.silence_start = now
                elif now - self.silence_start > self.silence_duration:
                    # Enough silence — utterance complete
                    return self._finalize()
            else:
                # Not speaking — update pre-buffer
                self._pre_buffer.append(audio_chunk)

        return None

    def _finalize(self) -> str | None:
        """Finalize the current utterance — transcribe it."""
        if not self.speech_buffer:
            self.is_speaking = False
            return None

        # Combine all chunks
        audio = np.concatenate(self.speech_buffer)

        # Reset state
        self.speech_buffer.clear()
        self.is_speaking = False
        self.silence_start = 0
        self.speech_start = 0

        # Check minimum duration
        duration = len(audio) / self.sample_rate
        if duration < self.min_speech_duration:
            return None

        # Check if it has actual speech energy
        if not _has_speech_energy(audio, self.energy_threshold * 0.5):
            return None

        # Transcribe
        try:
            text = transcribe_audio(audio, self.sample_rate)
            if text and len(text.strip()) > 1 and not _is_hallucination(text):
                return text.strip()
        except Exception:
            pass

        return None

    def set_jarvis_speaking(self, speaking: bool):
        """Tell the listener when JARVIS is speaking so it ignores echo."""
        self.jarvis_speaking = speaking
        if speaking:
            # Clear any in-progress detection
            self.speech_buffer.clear()
            self.is_speaking = False

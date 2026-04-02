"""Wake Word Detection — "Hey Jarvis" without always-on STT.

Uses openWakeWord for efficient keyword spotting. The wake word detector
runs continuously on the microphone stream at <1ms per frame, consuming
negligible CPU. Only when the wake word is detected does the full STT
pipeline activate.

States:
  LISTENING → detect "Hey Jarvis" → ACTIVE → STT processes speech → LISTENING

If openWakeWord is not installed, falls back to a simple energy-based
detector that activates on any loud sound (less precise but works).
"""

from __future__ import annotations

import numpy as np
import time


class WakeWordDetector:
    """Detect "Hey Jarvis" (or custom wake word) from audio stream.

    Uses openWakeWord if available, falls back to energy-based detection.
    """

    def __init__(self, model_path: str | None = None, threshold: float = 0.5):
        self.threshold = threshold
        self._model = None
        self._backend = "none"
        self._last_detection = 0.0
        self._cooldown = 2.0  # Don't re-trigger for 2 seconds

        # Try openWakeWord
        try:
            from openwakeword.model import Model
            models = [model_path] if model_path else []
            self._model = Model(wakeword_models=models)
            self._backend = "openwakeword"
        except (ImportError, Exception):
            # Fallback: energy-based detector
            self._backend = "energy"
            self._energy_threshold = 0.05  # RMS threshold
            self._energy_history: list[float] = []

    def feed(self, audio_chunk: np.ndarray, sample_rate: int = 16000) -> bool:
        """Feed an audio chunk. Returns True if wake word detected.

        audio_chunk: float32 array, ~80ms of audio (1280 samples at 16kHz)
        """
        now = time.time()
        if now - self._last_detection < self._cooldown:
            return False

        if self._backend == "openwakeword":
            return self._detect_openwakeword(audio_chunk, now)
        elif self._backend == "energy":
            return self._detect_energy(audio_chunk, now)

        return False

    def _detect_openwakeword(self, audio: np.ndarray, now: float) -> bool:
        """Detect using openWakeWord model."""
        prediction = self._model.predict(audio)
        for name, score in prediction.items():
            if score > self.threshold:
                self._last_detection = now
                return True
        return False

    def _detect_energy(self, audio: np.ndarray, now: float) -> bool:
        """Fallback: detect based on audio energy spike.

        Not as precise as a real wake word, but works without the model.
        Detects when someone starts speaking after silence.
        """
        rms = float(np.sqrt(np.mean(audio ** 2)))
        self._energy_history.append(rms)

        # Keep last 50 frames (~4 seconds at 80ms per frame)
        if len(self._energy_history) > 50:
            self._energy_history.pop(0)

        # Calculate adaptive threshold from recent background noise
        if len(self._energy_history) >= 10:
            baseline = np.median(self._energy_history[-30:])
            threshold = max(baseline * 3.0, self._energy_threshold)
            if rms > threshold:
                self._last_detection = now
                return True

        return False

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def is_ready(self) -> bool:
        return self._backend != "none"

    def stats(self) -> dict:
        return {
            "backend": self._backend,
            "threshold": self.threshold,
            "last_detection": self._last_detection,
            "cooldown": self._cooldown,
        }

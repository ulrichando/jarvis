"""Stateful viseme engine — turns (text, audio RMS) into per-frame morph
weights. One instance shared by the transcription handler and the
playback loop. Pure CPU. See the design spec.
"""
from __future__ import annotations

from .phonemize import text_to_visemes
from .viseme_tables import resolve_pose

# Each viseme holds for this long before the cursor advances. Conversational
# speech is ~12 phonemes/sec, so ~80 ms/viseme tracks naturally; 'sil'
# closures are shorter.
_VISEME_DUR_S = 0.08
_SIL_DUR_S = 0.04
# RMS at/above this reads as full openness (matches the 0..~0.2 range the
# playback loop produces for /level).
_RMS_FULL = 0.18
# Amplitude-fallback jaw gain (mirrors the kiosk's JAW_GAIN=6.0 today).
_FALLBACK_JAW_GAIN = 6.0


class VisemeEngine:
    def __init__(self) -> None:
        self._pending_text: str = ""
        self._seq: list[str] = []          # active viseme sequence
        self._durs: list[float] = []       # cumulative end-time of each viseme
        self._t0: float | None = None      # utterance start (rising edge)
        self._was_speaking: bool = False

    def set_pending_text(self, text: str) -> None:
        """Stash the text of the utterance about to be voiced."""
        self._pending_text = (text or "").strip()

    def reset(self) -> None:
        self._seq = []
        self._durs = []
        self._t0 = None
        self._was_speaking = False

    def _start(self, now: float) -> None:
        vis = text_to_visemes(self._pending_text)
        self._seq = vis
        # cumulative end-times so we can walk the cursor by elapsed time
        self._durs = []
        t = 0.0
        for v in vis:
            t += _SIL_DUR_S if v == "sil" else _VISEME_DUR_S
            self._durs.append(t)
        self._t0 = now

    def frame(self, now: float, speaking: bool, rms: float) -> dict[str, float]:
        """Return {target_N: weight} for the current frame."""
        # falling edge -> reset, mouth closes (kiosk eases to 0)
        if not speaking:
            if self._was_speaking:
                self.reset()
            self._was_speaking = False
            return {}
        # rising edge -> build the sequence from the pending text
        if not self._was_speaking:
            self._start(now)
            self._was_speaking = True

        openness = max(0.0, min(1.0, rms / _RMS_FULL))

        # no usable sequence -> amplitude jaw fallback (never worse than today)
        if not self._seq:
            jaw = max(0.0, min(1.0, rms * _FALLBACK_JAW_GAIN))
            return {"target_24": round(jaw, 4)}

        elapsed = now - (self._t0 or now)
        # find the current viseme by cumulative end-time; hold the last one
        # if the audio outruns the sequence.
        idx = len(self._seq) - 1
        for i, end_t in enumerate(self._durs):
            if elapsed < end_t:
                idx = i
                break
        return resolve_pose(self._seq[idx], openness)

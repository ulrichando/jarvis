"""Stateful viseme engine — turns (text, audio RMS) into per-frame morph
weights. One instance shared by the transcription handler and the
playback loop. Pure CPU. See the design spec.

Timing model (validated live 2026-05-30): the agent's TTS transcript
arrives word-by-word on `lk.transcription`, roughly in sync with the
audio (~0.3 s/word, ~4 visemes/word ≈ the 80 ms/viseme cadence below).
So `set_pending_text` is called repeatedly during an utterance with the
growing text, and the engine builds the viseme sequence LAZILY the first
frame text is available (anchoring t0 there) and EXTENDS it as more words
arrive — never going back to amplitude jaw once it has words. Before any
text lands (or if none ever does) it falls back to amplitude jaw, so the
mouth is never worse than the old behavior.
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
        self._pending_text: str = ""       # latest text from the transcript
        self._built_text: str = ""         # text the current sequence is built from
        self._seq: list[str] = []          # active viseme sequence
        self._durs: list[float] = []       # cumulative end-time of each viseme
        self._t0: float | None = None      # anchored at first build of this utterance
        self._was_speaking: bool = False

    def set_pending_text(self, text: str) -> None:
        """Set the latest known text of the utterance being voiced. Safe to
        call repeatedly during an utterance with the growing transcript."""
        self._pending_text = (text or "").strip()

    def reset(self) -> None:
        # Clears all per-utterance state INCLUDING the pending text, so the
        # next utterance never replays a previous one's words — it uses only
        # text set since the last utterance ended (else amplitude fallback).
        self._seq = []
        self._durs = []
        self._t0 = None
        self._was_speaking = False
        self._pending_text = ""
        self._built_text = ""

    def _build(self, text: str, now: float) -> None:
        """(Re)build the viseme sequence from `text`. Anchors t0 on the FIRST
        build of an utterance; later extends keep the original t0 so the
        cursor timeline stays continuous as words stream in."""
        vis = text_to_visemes(text)
        self._seq = vis
        self._durs = []
        t = 0.0
        for v in vis:
            t += _SIL_DUR_S if v == "sil" else _VISEME_DUR_S
            self._durs.append(t)
        self._built_text = text
        if self._t0 is None:
            self._t0 = now

    def frame(self, now: float, speaking: bool, rms: float) -> dict[str, float]:
        """Return {target_N: weight} for the current frame."""
        # falling edge -> reset, mouth closes (kiosk eases to 0)
        if not speaking:
            if self._was_speaking:
                self.reset()
            self._was_speaking = False
            return {}
        self._was_speaking = True

        # (re)build lazily whenever the transcript has new text — this is what
        # makes visemes engage: text arrives slightly after audio start and
        # then grows word-by-word.
        if self._pending_text and self._pending_text != self._built_text:
            # Distinguish a GROWING transcript (extend, keep the timeline) from
            # a FRESH utterance (re-anchor t0). The audio falling-edge reset
            # does NOT fire between back-to-back TTS segments because
            # state.speaking has a 1.2s hold — so without this, every sentence
            # after the first in a multi-sentence reply would inherit the first
            # sentence's t0, run the cursor off the end, and freeze on one pose.
            # A fresh utterance's text does not extend the built text.
            if not self._pending_text.startswith(self._built_text):
                self._t0 = now
            self._build(self._pending_text, now)

        openness = max(0.0, min(1.0, rms / _RMS_FULL))

        # no usable sequence yet (no text has arrived) -> amplitude jaw
        # fallback (never worse than today)
        if not self._seq:
            jaw = max(0.0, min(1.0, rms * _FALLBACK_JAW_GAIN))
            return {"target_24": round(jaw, 4)}

        t0 = self._t0 if self._t0 is not None else now
        elapsed = now - t0
        # find the current viseme by cumulative end-time; hold the last one
        # if the audio outruns the sequence.
        idx = len(self._seq) - 1
        for i, end_t in enumerate(self._durs):
            if elapsed < end_t:
                idx = i
                break
        return resolve_pose(self._seq[idx], openness)

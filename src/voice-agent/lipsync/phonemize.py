"""Text -> Oculus-viseme sequence, fully offline.

Known words come from the CMU pronouncing dictionary (ARPAbet); unknown
words fall back to a crude letter->phoneme rule so the mouth still moves.
A 'sil' is inserted at word boundaries for a brief closure. Stress digits
are stripped from ARPAbet symbols before the viseme lookup.
"""
from __future__ import annotations

import re

import cmudict

from .viseme_tables import ARPABET_TO_VISEME

# Loaded once, eagerly, at import time (~0.4s, 126k entries). This is
# DELIBERATE: text_to_visemes() is called from the audio playback loop, so
# building the dict lazily on first call would stall the first utterance by
# ~0.4s. Paying it at voice-client startup (a background service already
# ~10s into boot) keeps every audio-loop call O(1). Do not lazy-load this.
_CMU = cmudict.dict()  # {word: [[phoneme, ...], ...]}
_WORD_RE = re.compile(r"[a-z']+")
_STRESS_RE = re.compile(r"\d")

# Crude single-letter ARPAbet fallback for out-of-vocabulary words.
_LETTER_PHONEME = {
    "a": "AE", "b": "B", "c": "K", "d": "D", "e": "EH", "f": "F",
    "g": "G", "h": "HH", "i": "IH", "j": "JH", "k": "K", "l": "L",
    "m": "M", "n": "N", "o": "OW", "p": "P", "q": "K", "r": "R",
    "s": "S", "t": "T", "u": "AH", "v": "V", "w": "W", "x": "K",
    "y": "Y", "z": "Z",
}


def _phonemes_for(word: str) -> list[str]:
    entry = _CMU.get(word)
    if entry:
        return [_STRESS_RE.sub("", p) for p in entry[0]]
    return [_LETTER_PHONEME[c] for c in word if c in _LETTER_PHONEME]


def text_to_visemes(text: str) -> list[str]:
    """Return a flat list of Oculus viseme codes for `text`, with 'sil'
    at word boundaries. Empty/whitespace -> []. Tokens with no alphabetic
    content (bare digits like "2024", pure apostrophes) contribute no
    visemes — number-to-words expansion is out of scope for v1."""
    words = _WORD_RE.findall(text.lower())
    out: list[str] = []
    for i, word in enumerate(words):
        if i > 0:
            out.append("sil")
        for ph in _phonemes_for(word):
            out.append(ARPABET_TO_VISEME.get(ph, "sil"))
    return out

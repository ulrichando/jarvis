"""Text -> facial-expression ARKit-morph weights for the kiosk face.

Drives the brows / eyes / cheeks / smile-frown that the viseme engine leaves
idle, from the reply's sentiment + punctuation. Pure CPU, offline (VADER is a
lexicon). One instance shared with the playback loop, mirroring VisemeEngine.
"""
from __future__ import annotations

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from .viseme_tables import ARKIT_TO_TARGET, EXPRESSION_PRESETS

_VADER = SentimentIntensityAnalyzer()   # pure-Python lexicon, loaded once


def _blend(active: list[tuple[str, float]]) -> dict[str, float]:
    """Max-blend (preset_name, intensity) pairs into {target_N: weight}."""
    acc: dict[str, float] = {}
    for preset, intensity in active:
        scale = max(0.0, min(1.0, intensity))
        for morph, w in EXPRESSION_PRESETS[preset].items():
            tgt = ARKIT_TO_TARGET[morph]
            acc[tgt] = max(acc.get(tgt, 0.0), round(w * scale, 4))
    return acc


def expression_for_text(text: str) -> dict[str, float]:
    """Map `text` to a blend of expression presets -> {target_N: 0..1}."""
    t = (text or "").strip()
    if not t:
        return {}
    active: list[tuple[str, float]] = []
    compound = _VADER.polarity_scores(t)["compound"]
    if compound > 0.25:
        active.append(("warm", min(1.0, compound * 1.5)))
    elif compound < -0.25:
        active.append(("serious", min(1.0, abs(compound) * 1.5)))
    if "?" in t:
        active.append(("inquisitive", 1.0))
    caps = sum(1 for w in t.split() if len(w) >= 2 and w.isupper())
    if "!" in t or caps >= 2:
        active.append(("emphatic", 1.0))
    return _blend(active)


class ExpressionEngine:
    """Holds the current utterance's expression; emits it each frame while the
    agent is speaking (the kiosk eases it in/out)."""

    def __init__(self) -> None:
        self._expr: dict[str, float] = {}

    def set_pending_text(self, text: str) -> None:
        self._expr = expression_for_text(text)

    def reset(self) -> None:
        self._expr = {}

    def frame(self, speaking: bool) -> dict[str, float]:
        return dict(self._expr) if speaking else {}

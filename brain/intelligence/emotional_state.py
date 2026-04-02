"""
Emotional State Model for Jarvis.

This module implements a dimensional affect model to produce more natural,
contextually appropriate responses. It does NOT claim the AI experiences
emotions -- it uses continuous affective dimensions to modulate tone,
verbosity, and interaction style.

Dimensions:
    valence  (-1.0 to 1.0): negative <-> positive mood
    arousal  ( 0.0 to 1.0): calm <-> excited/urgent

Derived:
    engagement (0.0 to 1.0): investment in the current conversation
    rapport    (0.0 to 1.0): relationship quality, builds over time
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* between *lo* and *hi*."""
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Event definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _EventEffect:
    """How a single event type shifts each dimension."""
    valence: float = 0.0
    arousal: float = 0.0
    engagement: float = 0.0
    rapport: float = 0.0


# Each effect value is a *direction* that gets scaled by the event intensity.
EVENT_EFFECTS: Dict[str, _EventEffect] = {
    "user_positive":    _EventEffect(valence=+0.3, arousal=+0.1, engagement=+0.15, rapport=+0.1),
    "user_negative":    _EventEffect(valence=-0.3, arousal=+0.15, engagement=+0.1,  rapport=-0.05),
    "user_frustrated":  _EventEffect(valence=-0.4, arousal=+0.25, engagement=+0.2,  rapport=-0.1),
    "user_correction":  _EventEffect(valence=-0.2, arousal=+0.1,  engagement=+0.15, rapport=-0.05),
    "correct_answer":   _EventEffect(valence=+0.3, arousal=+0.05, engagement=+0.1,  rapport=+0.15),
    "wrong_answer":     _EventEffect(valence=-0.3, arousal=+0.1,  engagement=+0.05, rapport=-0.1),
    "learned_new":      _EventEffect(valence=+0.25, arousal=+0.2, engagement=+0.2,  rapport=+0.05),
    "user_greeting":    _EventEffect(valence=+0.2, arousal=+0.15, engagement=+0.25, rapport=+0.1),
    "long_silence":     _EventEffect(valence=-0.05, arousal=-0.2, engagement=-0.3,  rapport=-0.02),
    "user_thanks":      _EventEffect(valence=+0.35, arousal=+0.05, engagement=+0.1, rapport=+0.2),
}


# ---------------------------------------------------------------------------
# EmotionalState
# ---------------------------------------------------------------------------

class EmotionalState:
    """Dimensional affect model that modulates Jarvis's response style.

    Parameters
    ----------
    valence : float
        Initial valence (-1..1). Default 0.1 (slightly positive).
    arousal : float
        Initial arousal (0..1). Default 0.2 (calm).
    engagement : float
        Initial engagement (0..1). Default 0.3.
    rapport : float
        Initial rapport (0..1). Default 0.3.
    """

    # Neutral resting points -- decay drifts toward these.
    _NEUTRAL_VALENCE: float = 0.05
    _NEUTRAL_AROUSAL: float = 0.15
    _NEUTRAL_ENGAGEMENT: float = 0.2
    # Rapport does NOT decay toward zero; it decays very slowly.
    _RAPPORT_DECAY_RATE: float = 0.005

    # Half-life in seconds for each dimension's decay.
    _VALENCE_HALF_LIFE: float = 300.0   # 5 minutes
    _AROUSAL_HALF_LIFE: float = 120.0   # 2 minutes
    _ENGAGEMENT_HALF_LIFE: float = 600.0  # 10 minutes

    def __init__(
        self,
        valence: float = 0.1,
        arousal: float = 0.2,
        engagement: float = 0.3,
        rapport: float = 0.3,
    ) -> None:
        self.valence: float = _clamp(valence, -1.0, 1.0)
        self.arousal: float = _clamp(arousal, 0.0, 1.0)
        self.engagement: float = _clamp(engagement, 0.0, 1.0)
        self.rapport: float = _clamp(rapport, 0.0, 1.0)
        self._last_update: float = time.time()
        self._event_log: list[dict] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self, event: str, intensity: float = 0.5) -> None:
        """Apply an event to the emotional state.

        Parameters
        ----------
        event : str
            One of the recognised event names (see ``EVENT_EFFECTS``).
        intensity : float
            Strength of the event, 0.0 to 1.0.

        Raises
        ------
        ValueError
            If *event* is not recognised.
        """
        if event not in EVENT_EFFECTS:
            raise ValueError(
                f"Unknown event {event!r}. "
                f"Valid events: {', '.join(sorted(EVENT_EFFECTS))}"
            )

        intensity = _clamp(intensity, 0.0, 1.0)
        effect = EVENT_EFFECTS[event]

        self.valence = _clamp(self.valence + effect.valence * intensity, -1.0, 1.0)
        self.arousal = _clamp(self.arousal + effect.arousal * intensity, 0.0, 1.0)
        self.engagement = _clamp(self.engagement + effect.engagement * intensity, 0.0, 1.0)
        self.rapport = _clamp(self.rapport + effect.rapport * intensity, 0.0, 1.0)
        self._last_update = time.time()

        self._event_log.append({
            "event": event,
            "intensity": intensity,
            "time": self._last_update,
        })

    def get_tone(self) -> str:
        """Return a single-word tone descriptor based on current state.

        Returns
        -------
        str
            One of ``"empathetic"``, ``"warm"``, ``"energetic"``,
            ``"focused"``, or ``"neutral"``.
        """
        if self.valence < -0.25:
            return "empathetic"
        if self.valence > 0.3 and self.arousal > 0.4:
            return "energetic"
        if self.valence > 0.2:
            return "warm"
        if self.arousal > 0.4:
            return "focused"
        return "neutral"

    def get_response_style(self) -> dict:
        """Derive response-style parameters from the current state.

        Returns
        -------
        dict
            Keys:
            - ``verbosity`` (float 0-1): how detailed responses should be.
            - ``formality`` (float 0-1): how formal the language should be.
            - ``empathy`` (float 0-1): how much empathetic language to use.
            - ``ask_questions`` (bool): whether to proactively ask questions.
            - ``tone`` (str): the tone descriptor from :meth:`get_tone`.
        """
        # High engagement -> more verbose; low -> brief.
        verbosity = _clamp(0.3 + self.engagement * 0.6, 0.0, 1.0)

        # Low rapport -> slightly more formal; high -> relaxed.
        formality = _clamp(0.7 - self.rapport * 0.4, 0.0, 1.0)

        # Low valence -> more empathy.
        empathy = _clamp(0.5 - self.valence * 0.5, 0.0, 1.0)

        # High arousal -> conciseness pressure (reduce verbosity).
        if self.arousal > 0.6:
            verbosity = _clamp(verbosity - (self.arousal - 0.6) * 0.5, 0.0, 1.0)

        # Comfortable asking questions when rapport is high enough.
        ask_questions = self.rapport > 0.45

        return {
            "verbosity": round(verbosity, 3),
            "formality": round(formality, 3),
            "empathy": round(empathy, 3),
            "ask_questions": ask_questions,
            "tone": self.get_tone(),
        }

    def decay(self, seconds: float) -> None:
        """Apply time-based exponential decay toward neutral resting points.

        Parameters
        ----------
        seconds : float
            Elapsed time in seconds since last decay.
        """
        if seconds <= 0:
            return

        def _decay_toward(current: float, target: float, half_life: float) -> float:
            factor = math.pow(0.5, seconds / half_life)
            return target + (current - target) * factor

        self.valence = _clamp(
            _decay_toward(self.valence, self._NEUTRAL_VALENCE, self._VALENCE_HALF_LIFE),
            -1.0, 1.0,
        )
        self.arousal = _clamp(
            _decay_toward(self.arousal, self._NEUTRAL_AROUSAL, self._AROUSAL_HALF_LIFE),
            0.0, 1.0,
        )
        self.engagement = _clamp(
            _decay_toward(self.engagement, self._NEUTRAL_ENGAGEMENT, self._ENGAGEMENT_HALF_LIFE),
            0.0, 1.0,
        )
        # Rapport decays very slowly -- it represents long-term relationship.
        self.rapport = _clamp(
            self.rapport - self._RAPPORT_DECAY_RATE * (seconds / 60.0),
            0.0, 1.0,
        )

    def stats(self) -> dict:
        """Return the full internal state for debugging.

        Returns
        -------
        dict
            Current values of all dimensions plus event count.
        """
        return {
            "valence": round(self.valence, 4),
            "arousal": round(self.arousal, 4),
            "engagement": round(self.engagement, 4),
            "rapport": round(self.rapport, 4),
            "tone": self.get_tone(),
            "events_processed": len(self._event_log),
        }

    def __repr__(self) -> str:
        return (
            f"EmotionalState(valence={self.valence:.3f}, arousal={self.arousal:.3f}, "
            f"engagement={self.engagement:.3f}, rapport={self.rapport:.3f})"
        )


# ---------------------------------------------------------------------------
# Quick simulation when run directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Jarvis Emotional State -- Conversation Simulation ===\n")

    es = EmotionalState()
    print(f"[init]            tone={es.get_tone():<12s}  {es.stats()}")

    events = [
        ("user_greeting",   0.5),
        ("correct_answer",  0.8),
        ("user_thanks",     0.7),
        ("learned_new",     0.6),
        ("user_frustrated", 0.9),
        ("user_correction", 0.5),
        ("correct_answer",  0.9),
        ("user_positive",   0.6),
        ("long_silence",    0.4),
    ]

    for event, intensity in events:
        es.update(event, intensity)
        print(f"[{event:<18s}] tone={es.get_tone():<12s}  {es.stats()}")

    print(f"\nResponse style: {es.get_response_style()}")

    # Demonstrate decay
    print("\n--- Applying 5 minutes of decay ---")
    es.decay(300.0)
    print(f"[after decay]     tone={es.get_tone():<12s}  {es.stats()}")
    print(f"Response style: {es.get_response_style()}")

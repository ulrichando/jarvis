"""Directedness (device-directed-speech) score — voice-setup todo #3.

The durable replacement for the ambient gate's binary time-window (the band-aid
in [[voice-addressing-gate-reenabled-tv-loop]] that over-drops the user's real
speech when the window is cold, and lets a TV warm the window forever). Instead
of "was there a recent interaction?", this asks "does this utterance LOOK like
it's aimed at JARVIS?" by fusing cheap, already-available signals:

  - command / question shape (a command or question is far more likely directed
    than an ambient fragment)
  - utterance length (one- or two-word fragments are usually backchannel/ambient)
  - recency (exponential decay — a smooth version of the old hard window)

Vocative / wake / bare-greeting remain a HARD ACCEPT (handled by the caller);
this score only decides the previously time-window-gated tail.

**Not a learned model.** JARVIS has no labelled directed-vs-ambient corpus, so
this is a rule-based weighted fusion (Apple/Amazon DVAD papers use trained
models on data we don't have). Every weight + threshold is env-tunable so the
operating point can be set from real telemetry, and the score can later be
swapped for a logistic regression once shadow-mode has logged enough turns.

**Deferred signals (see PR description):** STT confidence (avg_logprob /
no_speech_prob is computed in faster_whisper_stt.py but discarded before the
turn — the #285/#287 filter already drops the worst-confidence garbage
upstream) and webcam presence (on-demand only; head-orientation/face-ID are NOT
a passive per-turn signal). Add them as features here if shadow data shows
cmd+len+recency is insufficient.

**Rollout.** Shadow-first: the gate keeps using the regex/window decision while
this score is logged alongside it (component features + both decisions) so
FRR@fixed-FAR can be computed offline from the logs. Flip to acting with
JARVIS_DIR_GATE=1 once calibrated. JARVIS_DIR_GATE=0 (default) reverts.
"""
from __future__ import annotations

import math
import os
import re

# Self-contained command/question detection — deliberately NOT importing
# jarvis_agent._looks_like_question so this module is independent of the
# interrupt-followup change.
_QUESTION_LEAD_RE = re.compile(
    r"^\s*(who|what|when|where|why|how|which|whose|whom|"
    r"can|could|would|will|should|do|does|did|is|are|was|were|"
    r"have|has|had|may|might)\b",
    re.IGNORECASE,
)
_COMMAND_LEAD_RE = re.compile(
    r"^\s*(open|close|start|stop|play|pause|show|find|search|set|turn|make|send|"
    r"call|read|write|run|check|tell|give|create|delete|remove|add|mute|unmute|"
    r"go|come|look|take|put|move|switch|remind|schedule|cancel|wait|repeat)\b",
    re.IGNORECASE,
)


def _f(env: str, default: float) -> float:
    try:
        return float(os.environ.get(env, default))
    except (TypeError, ValueError):
        return default


def acting() -> bool:
    """True when the directedness score should DRIVE the gate decision. Default
    False = shadow mode (score logged, regex/window still decides)."""
    return os.environ.get("JARVIS_DIR_GATE", "0") == "1"


def threshold() -> float:
    """Score below which a (non-hard-accepted) turn is dropped as ambient."""
    return _f("JARVIS_DIR_THRESHOLD", 0.45)


def looks_like_command_or_question(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return (
        t.endswith("?")
        or bool(_QUESTION_LEAD_RE.match(t))
        or bool(_COMMAND_LEAD_RE.match(t))
    )


def score(text: str, recency_elapsed_s: float) -> tuple[float, dict]:
    """Directedness score in [0, 1] — higher = more likely aimed at JARVIS.

    Pure function of `text` + the elapsed-since-last-interaction seconds, so it's
    unit-testable. Assumes vocative/wake/greeting hard-accept is handled by the
    caller (those never reach here). Weights are relative — the result is
    normalised by their sum, so tuning one weight doesn't rescale the threshold.
    """
    words = len((text or "").split())
    f_cmd = 1.0 if looks_like_command_or_question(text) else 0.0
    f_len = min(words / _f("JARVIS_DIR_LEN_FULL", 6.0), 1.0)
    tau = _f("JARVIS_DIR_TAU", 120.0)
    f_recency = math.exp(-max(recency_elapsed_s, 0.0) / tau) if tau > 0 else 0.0

    w_cmd = _f("JARVIS_DIR_W_CMD", 0.40)
    w_len = _f("JARVIS_DIR_W_LEN", 0.25)
    w_rec = _f("JARVIS_DIR_W_RECENCY", 0.35)
    total = w_cmd + w_len + w_rec or 1.0
    s = (w_cmd * f_cmd + w_len * f_len + w_rec * f_recency) / total

    return s, {
        "score": round(s, 3),
        "cmd": f_cmd,
        "len": round(f_len, 2),
        "recency": round(f_recency, 2),
        "words": words,
    }

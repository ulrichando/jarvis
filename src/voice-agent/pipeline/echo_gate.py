"""Echo-aware barge-in core — tells the user's real speech apart from
JARVIS's own TTS echoing back into a hot mic.

Design: docs/superpowers/specs/2026-05-20-echo-aware-bargein-gate-design.md

On speakers, keeping the mic live during TTS (so the user can interrupt)
feeds JARVIS's own Orpheus output back into STT as echo. But JARVIS *knows
the words it is currently speaking*, so any transcript that merely repeats
those words is echo, not a real interruption. This module is the single,
pure, testable decision; two consumers feed it:
  - interrupt suppression    (echo -> don't fire session.interrupt())
  - phantom-turn suppression (echo -> drop the finalized user turn)

Kill-phrases ("stop"/"wait"/...) are NEVER echo — they always get through,
which bounds the downside of the (out-of-scope) loud-echo-masking case: the
user is never trapped unable to interrupt; worst case they say "stop".

Pure stdlib — no livekit / jarvis_agent import (jarvis_agent imports THIS,
so the dependency must not point back). KILL_PHRASES_RE is the single source
of truth; jarvis_agent's mid-speech handler should import it from here rather
than redefining the alternation (the wake_word.py drift lesson).
"""
from __future__ import annotations

import os
import re

__all__ = ["is_echo", "enabled", "KILL_PHRASES_RE", "content_words"]


# Deliberate-stop phrases (mirrors the historical _KILL_PHRASES in
# jarvis_agent.py). These ALWAYS interrupt — never classified as echo.
KILL_PHRASES_RE = re.compile(
    r"\b("
    r"stop|wait|hold on|shut up|hush|pause|quiet|enough|cancel|nevermind|never mind"
    r"|one sec|one second|give me a (sec|second|moment)|hold up|hang on"
    r")\b",
    re.IGNORECASE,
)

_WORD_RE = re.compile(r"[a-z0-9']+")

# Function words carry no discriminating signal — they appear in both echo
# and real speech, so they don't count toward "novel" content.
_STOPWORDS = frozenset(
    "a an the and or but to of in on at for is are was were be been being am "
    "i you he she it we they them me my your his her our their this that these "
    "those with as do does did doing not no yes so if then will would shall "
    "should can could may might must have has had what which who whom how when "
    "where why youre im its".split()
)

_DEFAULT_MIN_NOVEL = 2


def content_words(text: str) -> list[str]:
    """Lowercase content words (stopwords + single-char tokens dropped)."""
    return [
        w for w in _WORD_RE.findall((text or "").lower())
        if len(w) > 1 and w not in _STOPWORDS
    ]


def _min_novel() -> int:
    """Min novel content words to treat a transcript as real (not echo).
    Read at call time so JARVIS_ECHO_MIN_NOVEL can change across restarts."""
    try:
        return max(1, int(os.environ.get("JARVIS_ECHO_MIN_NOVEL", _DEFAULT_MIN_NOVEL)))
    except (ValueError, TypeError):
        return _DEFAULT_MIN_NOVEL


def is_echo(transcript: str, speaking_text: str) -> bool:
    """True if `transcript` should NOT be treated as a real user utterance.

    True  => echo / empty / noise => suppress (don't interrupt; drop turn)
    False => real speech          => act     (interrupt; keep turn)

    Rules, in order:
      1. Empty / whitespace transcript -> True (nothing to act on).
      2. Kill-phrase present           -> False (always honor deliberate stops).
      3. No speaking_text              -> False (JARVIS isn't talking -> real turn;
                                          also fail-open if speech capture missed).
      4. Otherwise echo iff fewer than MIN_NOVEL content words in `transcript`
         are absent from `speaking_text`.
    """
    t = (transcript or "").strip()
    if not t:
        return True
    if KILL_PHRASES_RE.search(t):
        return False
    if not (speaking_text or "").strip():
        return False
    spoken = set(content_words(speaking_text))
    novel = [w for w in content_words(transcript) if w not in spoken]
    return len(novel) < _min_novel()


def enabled() -> bool:
    """Master switch for echo-aware barge-in. Default ON; set
    JARVIS_ECHO_AWARE_BARGEIN=0 to revert to the safe mic-drop-during-speak
    baseline (no hot mic during TTS, no echo gating)."""
    return os.environ.get("JARVIS_ECHO_AWARE_BARGEIN", "1") != "0"

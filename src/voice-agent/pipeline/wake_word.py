"""Single source of truth for the JARVIS-name regex alternation.

Whisper transcribes "Jarvis" as many things depending on accent and
noise — verified across the conversation DB: jarvis, jervis, javis,
joris, yarvis, garvis, jalvis, etc. We match the common phonetic
variants. The pattern is permissive on purpose: false-positive vocative
just means JARVIS responds to a similar-sounding word; false-negative
means the user has to repeat themselves.

**Why this module exists.** Pre-2026-05-10 the alternation lived in
THREE places in jarvis_agent.py (`_JARVIS_NAME_RE`, `_BARE_VOCATIVE_RE`,
the inline strip inside `_is_command()`), kept in sync by hand-written
comments referencing line numbers. Drift was inevitable: spec review
caught it 2026-05-09 (6 Whisper variants added to one site only); a
similar drift produced the "Hey, Jalvis." → "Pardon?" regression
2026-05-10 because the 'l'-variant addition got incomplete coverage.

Now there is ONE place: `NAME_ALTERNATION` below. The three runtime
regexes are built from it. Adding a new STT variant means editing
NAME_ALTERNATION; the three call sites pick it up automatically.

Spec: docs/superpowers/specs/2026-05-09-jarvis-drop-butler-register-design.md
(historical context for the original 3-way sync invariant).
"""
from __future__ import annotations

import re


__all__ = ["NAME_ALTERNATION", "NAME_RE", "BARE_VOCATIVE_RE", "INLINE_STRIP_RE"]


# ── The one source of truth ──────────────────────────────────────────
# Add new Whisper-variant names HERE. The 3 compiled regexes below
# pick up the change automatically.
#
# 2026-05-13: broadened from `j[aeo][rl]?vis` to `j[aeiou]+[rl]*[aeiou]*vis`
# after live capture (turns 2093-2095) showed STT producing
# "Jauravis", "Jaurvis", "Jaervis", "Joaurvis" — all missed by the
# previous single-vowel-with-optional-r/l pattern. The new pattern
# allows: one or more vowels, then zero-or-more r/l, then zero-or-
# more vowels, then "vis". Catches:
#   javis, jarvis, jervis, jorvis, jalvis, julvis, jelvis (1-vowel)
#   jaurvis, jauravis, jaervis, joaurvis, jaurevis, jaeurevis (multi-vowel)
# Risk of false-positive: a few real English `j`-words ending in
# "vis" (rare — "Javis" / "Jevis" are uncommon names) might wake
# JARVIS spuriously. Acceptable cost.
NAME_ALTERNATION: str = (
    r"j[aeiou]+[rl]*[aeiou]*vis"
    r"|joris|jervis|jarvest|jaravis|jeris|jarves|jarvess"
    r"|y[aeiou]+[rl]*[aeiou]*vis|g[aeiou]+[rl]*[aeiou]*vis|h[aeiou]+[rl]*[aeiou]*vis"
    r"|jorvis|jarbis"
    r"|yaris|yeris|yoris|jarius|jarrus|jorius"
)


# ── Site 1: word-boundary search ─────────────────────────────────────
# Matches anywhere in a transcript. Used by the quiet-hours guard
# (`_in_quiet_hours()` skip-if-vocative-present), by the broader-net
# bypass in the short-input gate (any short input containing the name
# bypasses), and by tests asserting the regex matches a given variant.
NAME_RE: re.Pattern[str] = re.compile(
    rf"\b(?:{NAME_ALTERNATION})\b",
    re.IGNORECASE,
)


# ── Site 2: bare-vocative full-match ─────────────────────────────────
# Matches an entire short transcript that is JUST the name (with
# optional preamble fillers and trailing punctuation). Used by the
# bare-vocative fast-path in `on_user_turn_completed` to voice the
# canonical "Yes?" via TTS without an LLM round-trip — saves ~2-3 s
# of wake latency.
#
# Accepts:  "jarvis." / "hey jarvis" / "yo jarvis!" / "ok jarvis" /
#           "i said jarvis" / "Hello, Jarvis." (comma after preamble)
# Rejects:  "jarvis open browser" / "jarvis what time"
BARE_VOCATIVE_RE: re.Pattern[str] = re.compile(
    r"^\s*"
    # Optional preamble — common wake-fillers before the name. The
    # filler may be followed by either whitespace OR a comma+whitespace
    # ("Hello, Jarvis." is natural English; pre-2026-05-09 the comma
    # form fell into the short-input gate as a 2-word ambiguous and
    # got "Pardon?").
    r"(?:(?:hey|yo|hi|ok(?:ay)?|so|alright|hello|i\s+said|please)[,\s]+)*"
    # The name itself.
    rf"(?:{NAME_ALTERNATION})"
    # Optional trailing punctuation only — no follow-up content:
    r"\s*[?!.,]*\s*$",
    re.IGNORECASE,
)


# ── Site 3: inline strip inside _is_command() ────────────────────────
# Strips a leading vocative from a sentence so wake/mute pattern
# matching can run against the body. Distinct from NAME_RE because
# it (a) anchors at start, (b) accepts trailing punctuation/space
# as a delimiter (not a word boundary), and (c) consumes the
# delimiter so the body is clean for downstream regex matching.
INLINE_STRIP_RE: re.Pattern[str] = re.compile(
    rf"^(?:{NAME_ALTERNATION})[,.:!\s]+",
    re.IGNORECASE,
)

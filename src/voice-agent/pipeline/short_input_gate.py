"""Short-input ambiguity gate — INVERTED 2026-05-10.

Previously this gate used a broad "deflect-unless-pattern-matches"
strategy: any 1-2 word input that didn't match an allowlist /
interrogative-bypass / vocative-bypass / kill-phrase-bypass got a
hardcoded "Pardon?". The set of legitimate-but-short inputs turned
out to be unbounded (greetings, emotional fragments, Whisper
variants, foreign words, contractions, ...) and each fix exposed
new gaps:

  · 'Hello, Jervis.'      → Pardon?  (2026-05-09)
  · 'Hey, Jalvis.'        → Pardon?  (2026-05-10 06:57)
  · 'at Jarvis.'          → Pardon?  (2026-05-10 06:57)
  · 'What's EMI?'         → Pardon?  (2026-05-09 19:47)
  · 'All right.'          → Pardon?  (2026-05-10 02:25)
  · 'good morning.'       → Pardon?  (2026-05-10 23:53)
  · 'força'               → Pardon?  (2026-05-10 23:53, Portuguese)
  · "i'm free."           → Pardon?  (2026-05-10 11:40)
  · 'so done'             → Pardon?  (2026-05-10 11:36)

Three rounds of bypass-broadening (daaafb2, 5a01cea + this) didn't
close the gap because the gap is structural: pattern-matching
"contentless" is harder than pattern-matching "specifically the
half-dozen dismissive words that caused the original confab."

The inverted approach: a small explicit BLOCKLIST of utterances
that have been observed to confab. Everything else flows to the
supervisor LLM, where confab is now handled defense-in-depth by
the confab detector, the chat_ctx-pruning, the supervisor prompt's
"never bare Pardon?" / "do not confabulate" rules, and the denial
detector. The original threat profile (a single 6-turn window on
2026-05-08 where 6/6 short inputs confabulated topical content
from chat_ctx) is no longer the dominant failure mode.

Historical context: docs/superpowers/specs/2026-05-08-anti-gaslighting-memory-design.md
and the live capture 2026-05-08T13:11-13:50 logged in the original
implementation.
"""
from __future__ import annotations


__all__ = ["CONFAB_TRIGGERS", "is_ambiguous_short_input"]


# ── The blocklist ────────────────────────────────────────────────────
# Only utterances that have been LIVE-OBSERVED to cause the supervisor
# LLM to reach for chat_ctx topical content on contentless input.
# Compared lowercase with trailing punctuation stripped and internal
# whitespace collapsed (see `_normalize` below).
#
# Adding to this set: require a logged confab incident — don't add
# "feels like it might confab" hypotheticals. Hypothetical additions
# are how this file grew the over-broad allowlist that we just ripped
# out.
CONFAB_TRIGGERS: frozenset[str] = frozenset({
    # Live evidence 2026-05-08 13:11-13:50:
    "hush",            # → 19 s of Cameroon history
    "one second",      # → 18 s of English history
    "one sec",         # variant
    # Closely-related dismissive shapes that share the no-anchor
    # property. Kept narrow on purpose.
    "quiet",
    "give me a sec",
    "gimme a sec",
    "whatever",
    "maybe",
})


def _normalize(text: str) -> str:
    """Lowercase, strip surrounding whitespace, strip trailing
    sentence punctuation, collapse internal whitespace. Returns a
    canonical key for blocklist lookup.

    Examples:
      "Hush!"          → "hush"
      "  One Sec.  "   → "one sec"
      "GIVE ME A SEC?" → "give me a sec"
    """
    s = text.casefold().strip().rstrip(".,!?;:")
    return " ".join(s.split())


def is_ambiguous_short_input(text: str) -> bool:
    """True iff `text` matches a known confab-trigger utterance.

    Returns False for everything else — including greetings, ack
    fragments, vocatives, Whisper variants, interrogatives, foreign
    words, and emotional 2-grams. Confab protection on those falls
    to the supervisor prompt + confab detector + chat_ctx pruning,
    which have all been hardened since the gate's original
    pattern-matching approach.
    """
    if not text:
        return False
    return _normalize(text) in CONFAB_TRIGGERS

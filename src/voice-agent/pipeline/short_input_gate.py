"""Short-input ambiguity gate.

Live evidence 2026-05-08 13:11-13:50: 6/6 short-input + >5s-audio turns
were confabulations from chat_ctx. Worst case: "Hush!" → 19 s of
Cameroon history. The supervisor LLM lacks a content anchor on these
inputs and reaches for topical content from the chat_ctx window.

This gate routes short-and-contentless inputs to a deterministic
"Pardon?" without calling the LLM. Legit short inputs (yes/no/sure/
thanks/cool/right/fine/okay/etc.) keep flowing.

Bypass classes (these short inputs DO reach the LLM):
  - allowlist words (yes / sure / thanks / great / etc.)
  - bare vocatives (jarvis / hey jarvis / hello jarvis / Whisper variants)
  - any short input CONTAINING a Jarvis-name variant (broader net)
  - interrupt kill-phrases (stop / wait / cancel / etc.)
  - short interrogatives (≥2 words ending with "?", or WH-stems)
  - recall queries (force-routed via pipeline.turn_router.is_recall_query)

Note on "Hush!" specifically: `_MUTE_PATTERNS` includes `\\bhush\\b`
and so does `_KILL_PHRASES`, but `_is_command()` requires a vocative
("Jarvis, hush") for mute patterns — so "Hush!" without a vocative
passes through to the LLM. The kill-phrase path fires only when
agent_state == "speaking". If JARVIS wasn't speaking when the user
said "Hush!", both gates skip and the bare transcript reaches the
LLM. THIS gate catches that case.
"""
from __future__ import annotations

import re

from pipeline.vocative import NAME_RE, BARE_VOCATIVE_RE


# ── Bypass: allowlist of short legit replies ─────────────────────────
ALLOWLIST_RE: re.Pattern[str] = re.compile(
    r"^\s*"
    r"(?:"
    # Affirmations / acks — let these flow to the LLM for natural reply
    r"yes|yeah|yep|yup|sure|right|okay|ok|fine|cool|nice|"
    r"thanks|thank\s*you|nope|no|nah|"
    # Single-word polite responses. Both "alright" (one `l`, no space)
    # and "all right" (two `l`s, spaced) carry the same intent; the
    # spaced form was tripping the gate before 2026-05-09.
    r"alright|all\s+right|bye|goodbye|cheers|gotcha|"
    # Reaction words that benefit from LLM's emotional response
    r"wow|sweet|awesome|amazing|great|good|perfect"
    r")"
    r"[\s,.!?]*$",
    re.IGNORECASE,
)


# ── Bypass: short interrogatives ─────────────────────────────────────
# Short interrogatives carry semantic intent — questions ending with `?`
# (≥2 words) and bare WH-stems ("Why?", "What now?") must reach the
# supervisor LLM rather than getting "Pardon?". Live evidence
# 2026-05-09T22:10+: 33% of post-restart turns were "Pardon?", many of
# them legitimate questions like "What's EMI?" (telemetry id 1513).
#
# Conservative shape: 1-word non-WH inputs ending with `?` (e.g. "Hush?",
# "Hmm?") still get deflected — they're indistinguishable from the
# original confab triggers and the user typically wants silence on those.
INTERROGATIVE_BYPASS_RE: re.Pattern[str] = re.compile(
    r"^\s*"
    r"(?:"
    # 2+ tokens ending with `?` — clearly a content-bearing question
    r"\S+\s+\S+.*\?"
    r"|"
    # WH-stem (even bare, like "Why?" / "What?" / "How?"). Word boundary
    # after the stem prevents matching "Whatever" / "however" — those
    # remain in the gate.
    r"(?:what|who|where|when|why|how|which|whose)(?:'?(?:s|re|ll))?\b.*"
    r")"
    r"\s*$",
    re.IGNORECASE | re.DOTALL,
)


# ── Bypass: short interrupt kill-phrases ─────────────────────────────
# Mid-speech kill-phrase listener in jarvis_agent.py only fires when
# JARVIS is currently speaking; outside that window these phrases need
# a normal LLM reply rather than "Pardon?".
#
# Deliberately excludes "hush", "one second", "one sec", "give me a sec",
# "quiet" — those are the original confab triggers from 2026-05-08 and
# must remain inside the gate. `_KILL_PHRASES` inside entrypoint() is
# the superset; this is the safe-to-bypass subset.
KILL_PHRASE_BYPASS_RE: re.Pattern[str] = re.compile(
    r"^\s*"
    r"(?:"
    r"stop|wait|cancel|nevermind|never\s*mind|enough|pause|"
    r"hold\s*on|hold\s*up|hang\s*on|shut\s*up"
    r")"
    r"[\s,.!?]*$",
    re.IGNORECASE,
)


def is_ambiguous_short_input(text: str) -> bool:
    """True if the transcript is ≤2 words and not a known intent
    pattern, so the gate should respond with 'Pardon?' rather than
    routing to the supervisor LLM (which has been observed to reach for
    topical content from chat_ctx on these short, contentless inputs).

    Returns False for: legit affirmations, bare vocatives (incl. Whisper
    variants), interrupt kill-phrases, recall queries, short
    interrogatives, and anything ≥3 words.
    """
    if not text:
        return False
    text = text.strip()
    if not text:
        return False
    word_count = len(text.split())
    if word_count >= 3:
        return False
    # Allowlist: legit short replies that should flow to the LLM
    if ALLOWLIST_RE.match(text):
        return False
    # Bare vocatives (and Whisper mis-transcriptions) must reach the
    # bare-vocative fast-path so they get the canonical "Yes?".
    # Live failure 2026-05-09: 30+ "Pardon?" replies traced to
    # vocatives being deflected here before the fast-path could fire.
    if BARE_VOCATIVE_RE.match(text):
        return False
    # Broader-net (2026-05-10): any short input containing a Jarvis-
    # name variant ANYWHERE bypasses the gate. Catches Whisper
    # transcripts like "at Jarvis." (mis-rendered "Hi, Jarvis") or
    # "Hey Jalvis" where the preamble doesn't match BARE_VOCATIVE_RE
    # but the user clearly intended a wake. The gate's job is to
    # block contentless ambient noise — anything containing the
    # name is by definition not contentless.
    if NAME_RE.search(text):
        return False
    # Interrupt kill-phrases — let them flow to the LLM as conversational
    # input outside the mid-speech kill-phrase window.
    if KILL_PHRASE_BYPASS_RE.match(text):
        return False
    # Short interrogatives — "What's EMI?", "Why?", "Got it?" carry
    # semantic intent and must reach the LLM. Live regression
    # 2026-05-09: 33% of post-restart turns were "Pardon?", many of
    # them legitimate WH-questions or ?-terminated short interrogatives.
    if INTERROGATIVE_BYPASS_RE.match(text):
        return False
    # Recall queries are short but should hit the recall force-router,
    # not be deflected. Mostly >=3 words in practice but check anyway.
    try:
        from pipeline.turn_router import is_recall_query
        if is_recall_query(text):
            return False
    except Exception:
        pass
    return True

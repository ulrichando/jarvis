"""Shared wake/mute voice-command matcher — ONE source of truth.

Extracted from jarvis_agent.py 2026-06-18 so BOTH the supervisor agent
(reactive silent-mode gating in `on_user_turn_completed`) and the
voice-client's local wake-listener (which transcribes mic audio locally
while silenced, so no audio reaches the cloud) decide "is this a wake/mute
command addressed to JARVIS" the SAME way. Two copies would inevitably
drift — the agent and the local wake path would disagree on what wakes
JARVIS.

The vocative (name) regex still lives in `pipeline.wake_word` — this
module imports it so there remains a single name-alternation source.

Spec: docs/superpowers/specs/2026-06-18-silent-mode-token-leak-fix-design.md
"""
from __future__ import annotations

import re

from pipeline.wake_word import BARE_VOCATIVE_RE, INLINE_STRIP_RE, NAME_RE


__all__ = [
    "MUTE_PATTERNS",
    "MUTE_SELF_EVIDENT_PATTERNS",
    "WAKE_PATTERNS",
    "WAKE_STRICT_PATTERNS",
    "UNMUTE_PATTERNS",
    "MEDIA_OBJECT_RE",
    "SENTENCE_SPLIT_RE",
    "COMMAND_MAX_WORDS",
    "is_command",
    "is_wake",
    "is_mute",
]


# Phrases that toggle silent mode. Each pattern is a regex tested
# against the lowercased transcript with word-boundary anchors, so
# "mute" matches the bare imperative ("Jarvis, mute") but NOT
# "muted" / "commute" / "automute". Multi-word patterns also use
# \b on both ends so trailing punctuation like "Jarvis, mute."
# still hits.
MUTE_PATTERNS = tuple(re.compile(r"\b" + p + r"\b") for p in (
    # "mutes?" — Whisper renders "Jarvis, mute" as "Jarvis mutes." often
    # enough to show up in the conversation DB (2026-06-13). "muting"
    # deliberately NOT covered (no \b-adjacent "mute" in it).
    r"mutes?",
    r"go silent",
    r"go quiet",
    r"be quiet",
    r"quiet down",
    r"shut up",
    r"stop talking",
    r"go to sleep",
    r"silence yourself",
    r"silent mode",
    # Bare "quiet" — "Jarvis, quiet" is a natural way to ask for
    # silence and the prior pattern set missed it. Safe because the
    # COMMAND_MAX_WORDS=6 gate (below) restricts matches to short
    # imperative sentences; "I'd like some quiet please" is fine but
    # only triggers because it fits a quiet-request shape anyway.
    r"quiet",
))


# Subset of mute phrasings that are only ever said TO A MACHINE — safe
# to honor WITHOUT the "Jarvis" vocative when the caller has separate
# evidence the user is engaged with JARVIS (see jarvis_agent's
# `_mute_context_engaged`). The human-directed phrases (shut up / be
# quiet / quiet (down) / stop talking / go to sleep) stay vocative-
# gated: ambient speech to kids / calls / the TV collides with them —
# the 2026-04-26 false-positive class. Live motivation: bare
# "Go on mute (please)" is the user's single most common mute phrasing
# (7+ occurrences in conversations.db) and matched NOTHING
# deterministic before 2026-07-03.
MUTE_SELF_EVIDENT_PATTERNS = tuple(re.compile(r"\b" + p + r"\b") for p in (
    r"mutes?",
    r"go silent",
    r"silent mode",
    r"silence yourself",
))


WAKE_PATTERNS = tuple(re.compile(r"\b" + p + r"\b") for p in (
    r"wake up",
    r"come back",
    r"un[\s-]?mute",
    r"talk again",
    r"you can talk",
    r"are you there",
    r"are you back",
    r"you there",  # was "jarvis you there"; vocative is stripped before match
    # Natural recovery phrases — when the user notices JARVIS has
    # gone silent and tries to get a response. These are easy to
    # miss but they're THE signal that silent mode was a false
    # positive and the user wants out. Keep the patterns narrow
    # (anchored on "you" + a verb of attention) so they don't fire
    # on ambient chatter.
    r"are you listening",
    r"are you broken",
    r"why are(n't| not) you responding",
    r"why aren't you talking",
    r"respond to me",
    r"answer me",
    r"hello jarvis",
    r"hey jarvis",
))


# Wake patterns that are dangerous in noisy multi-person rooms —
# they collide with everyday speech ("answer me!" between people,
# "are you there?" on a phone call). For these, is_command requires
# the "Jarvis," vocative. The remaining wake patterns stay permissive
# (uniquely-commanding phrases like "wake up", or already-vocative
# phrases like "hey jarvis").
WAKE_STRICT_PATTERNS = tuple(re.compile(r"\b" + p + r"\b") for p in (
    r"are you there",
    r"are you back",
    r"you there",
    r"are you listening",
    r"are you broken",
    r"why are(n't| not) you responding",
    r"why aren't you talking",
    r"respond to me",
    r"answer me",
    r"talk again",
    r"you can talk",
    r"come back",  # common as "come back here, kid" — needs vocative
))


# Deliberate UN-MUTE phrases — the ONLY things that exit silent mode by
# voice (2026-07-09, mute-stickiness fix). Deliberately a SUBSET of
# WAKE_PATTERNS: a mute is intentional, so un-muting must be intentional
# too. Greeting/address phrases ("hey jarvis", "hello jarvis") and
# attention-checks ("are you there") are EXCLUDED — they used to un-mute,
# which is exactly why "go on mute" didn't stay muted (the user's normal
# "hey Jarvis…" silently woke him). The strict members (come back / talk
# again / you can talk) still require the "Jarvis," vocative via is_command's
# WAKE_STRICT_PATTERNS check; the unambiguous ones stay permissive.
UNMUTE_PATTERNS = tuple(re.compile(r"\b" + p + r"\b") for p in (
    r"wake up",
    r"un[\s-]?mute",
    r"start listening",
    r"resume listening",
    r"you can speak",
    r"you can talk( again)?",   # WAKE_STRICT "you can talk" → needs vocative
    r"talk again",              # WAKE_STRICT → needs vocative
    r"come back",               # WAKE_STRICT → needs vocative
    r"exit silent mode",
    r"stop being (quiet|silent)",
))


# "mute X" where X is a media noun is a media command (mute Spotify,
# mute the music) — should go to media_control, NOT enter silent
# mode. Skip those before treating "mute" as a JARVIS-silence trigger.
MEDIA_OBJECT_RE = re.compile(
    r"\b(mutes?|silence|shut up)\b\s+"
    r"(the\s+)?"
    r"(music|song|track|audio|video|spotify|chrome|chromium|"
    r"firefox|youtube|player|tab|tv|sound|volume)",
)


# Wake/mute commands are short imperatives ("wake up", "Jarvis,
# mute"). Substring matching alone false-positives on topical
# mentions ("you don't even have to wake up"). The fix:
#   - Split the utterance into sentences (split on . ! ? ;).
#   - Treat EACH sentence as a candidate command.
#   - A sentence is command-shaped if (after stripping a leading
#     "Jarvis," vocative) it has ≤ COMMAND_MAX_WORDS words AND
#     contains one of our patterns.
# This lets "We can eat together. We don't... Jarvis, mute." fire
# the mute branch (the last sentence "Jarvis, mute" is a 1-word
# command) while still rejecting "you don't even have to wake up
# you say you swear and you go into your coaching" (the wake-up
# phrase lives in a 9-word sentence — too long).
COMMAND_MAX_WORDS = 6
SENTENCE_SPLIT_RE = re.compile(r"[.!?;]+|\.{2,}")


def is_command(
    text: str,
    patterns: tuple[re.Pattern, ...],
    *,
    require_vocative: bool | None = None,
) -> bool:
    """True iff some sentence in `text` is a short imperative matching
    `patterns` and addressed to JARVIS where required (mute needs the
    vocative by default; strict-wake phrases need it; uniquely-commanding
    wake phrases stay permissive).

    `require_vocative` overrides the mute vocative rule ONLY (None =
    pattern-set default). Callers passing False must supply their own
    addressed-to-JARVIS evidence — see jarvis_agent's
    `_mute_context_engaged()`. It does not relax WAKE_STRICT_PATTERNS.

    Vocative detection (2026-07-03, mute-intermittency fix) accepts the
    name ANYWHERE in the sentence ("go on mute, jarvis") and carries a
    bare-vocative sentence into the next one ("Jarvis. Go on mute." —
    Whisper's punctuation is a lottery, so the name and the command
    often land in different sentences)."""
    is_mute_check = (
        patterns is MUTE_PATTERNS or patterns is MUTE_SELF_EVIDENT_PATTERNS
    )
    need_vocative = is_mute_check if require_vocative is None else require_vocative
    prev_bare_vocative = False
    for sentence in SENTENCE_SPLIT_RE.split(text or ""):
        raw_body = sentence.strip().lower()
        if not raw_body:
            continue
        # Vocative = the name anywhere in this sentence, or the previous
        # sentence being JUST the name ("Jarvis." / "Hey Jarvis."). The
        # regexes come from pipeline.wake_word — same alternation source
        # as the agent's other vocative sites, so they stay synchronized.
        had_vocative = prev_bare_vocative or bool(NAME_RE.search(raw_body))
        prev_bare_vocative = bool(BARE_VOCATIVE_RE.match(raw_body))
        # Strip a leading "(hey) jarvis," so the word-count bound and the
        # pattern match run against the command body, not the address.
        body = INLINE_STRIP_RE.sub("", raw_body)
        if len(body.split()) > COMMAND_MAX_WORDS:
            continue
        # If we're checking for a MUTE trigger and the user is actually
        # asking to mute media (mute Spotify / mute the music), let
        # media_control handle it instead.
        if is_mute_check and MEDIA_OBJECT_RE.search(body):
            continue
        # Mute commands MUST address JARVIS by name (unless the caller
        # explicitly relaxed it). False positive captured 2026-04-26:
        # "i'm leaving. go on mute." (user speaking to a third party)
        # silenced JARVIS for two hours. Wake commands stay permissive
        # on a per-pattern basis (see WAKE_STRICT_PATTERNS below) — the
        # loose phrases that collide with everyday speech ("are you
        # listening", "answer me", etc.) require the vocative;
        # uniquely-commanding ones ("wake up", "hey jarvis") stay
        # permissive.
        if is_mute_check and need_vocative and not had_vocative:
            continue
        if (not is_mute_check) and (not had_vocative) and any(
            p.search(body) for p in WAKE_STRICT_PATTERNS
        ):
            # The matched pattern is in the strict set → require vocative.
            # Skip this sentence entirely; another sentence in the same
            # transcript can still wake (e.g. "are you there. jarvis
            # wake up." — the second sentence has the vocative).
            continue
        if any(p.search(body) for p in patterns):
            return True
    return False


def is_wake(text: str) -> bool:
    """True iff `text` is a wake command (exit silent mode)."""
    return is_command(text, WAKE_PATTERNS)


def is_mute(text: str) -> bool:
    """True iff `text` is a mute command (enter silent mode)."""
    return is_command(text, MUTE_PATTERNS)

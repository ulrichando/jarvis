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

from pipeline.wake_word import INLINE_STRIP_RE


__all__ = [
    "MUTE_PATTERNS",
    "WAKE_PATTERNS",
    "WAKE_STRICT_PATTERNS",
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
    r"mute",
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


# "mute X" where X is a media noun is a media command (mute Spotify,
# mute the music) — should go to media_control, NOT enter silent
# mode. Skip those before treating "mute" as a JARVIS-silence trigger.
MEDIA_OBJECT_RE = re.compile(
    r"\b(mute|silence|shut up)\b\s+"
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


def is_command(text: str, patterns: tuple[re.Pattern, ...]) -> bool:
    """True iff some sentence in `text` is a short imperative matching
    `patterns` and addressed to JARVIS where required (mute always needs
    the vocative; strict-wake phrases need it; uniquely-commanding wake
    phrases stay permissive)."""
    is_mute_check = patterns is MUTE_PATTERNS
    for sentence in SENTENCE_SPLIT_RE.split(text or ""):
        body = sentence.strip().lower()
        if not body:
            continue
        # Strip a leading vocative ("jarvis" / "jervis" / "javis" / "joris" /
        # the 'l' variants / etc.), remembering whether one was actually
        # present. The regex comes from pipeline.wake_word — same alternation
        # source as NAME_RE and BARE_VOCATIVE_RE, so all 3 sites stay
        # synchronized automatically.
        stripped = INLINE_STRIP_RE.sub("", body)
        had_vocative = stripped != body
        body = stripped
        if len(body.split()) > COMMAND_MAX_WORDS:
            continue
        # If we're checking for a MUTE trigger and the user is actually
        # asking to mute media (mute Spotify / mute the music), let
        # media_control handle it instead.
        if is_mute_check and MEDIA_OBJECT_RE.search(body):
            continue
        # Mute commands MUST address JARVIS by name. False positive
        # captured 2026-04-26: "i'm leaving. go on mute." (user
        # speaking to a third party) silenced JARVIS for two hours.
        # Wake commands stay permissive on a per-pattern basis (see
        # WAKE_STRICT_PATTERNS below) — the loose phrases that collide
        # with everyday speech ("are you listening", "answer me", etc.)
        # require the vocative; uniquely-commanding ones ("wake up",
        # "hey jarvis") stay permissive.
        if is_mute_check and not had_vocative:
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

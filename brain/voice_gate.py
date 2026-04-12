"""
Voice gate — validates Whisper transcriptions before they reach the brain.
Rejects ambient noise, hallucinations, filler words, and wake-word-less phrases.
Zero model cost on rejection.
"""

import re

# Minimum real word count to be treated as a command
MIN_WORDS = 2

# Known noise phrases — exact matches (after strip + lower)
NOISE_PHRASES: frozenset[str] = frozenset({
    # Empty / whitespace
    "", " ", ".", "..", "...", "…",
    # Single filler words
    "you", "the", "a", "uh", "um", "hmm", "hm", "ah", "eh", "oh",
    "okay", "ok", "yeah", "yes", "no",
    # Polite closings that aren't commands
    "thank you", "thanks", "bye", "goodbye", "see you",
    # Same with trailing punctuation
    "you.", "the.", "thank you.", "thanks.", "bye.", "okay.", "ok.",
    # Whisper hallucinations on silence / music
    "subtitles by", "transcribed by", "translated by",
    "[music]", "[applause]", "[laughter]", "(music)", "(applause)",
    "♪", "♫",
})

# Wake word — every voice message MUST begin with this (case-insensitive).
# Set to None to disable the requirement.
WAKE_WORD: str | None = "jarvis"


def is_valid_voice_input(transcript: str) -> tuple[bool, str]:
    """
    Validate a Whisper transcript before sending to the brain.
    Returns (is_valid, reason_if_rejected).
    """
    cleaned = transcript.strip()
    lower   = cleaned.lower()

    # Reject empty
    if not cleaned:
        return False, "empty transcript"

    # Reject known noise phrases (with and without trailing punctuation)
    if lower in NOISE_PHRASES or lower.rstrip(".,!?") in NOISE_PHRASES:
        return False, f"noise phrase: '{lower}'"

    # Reject if no alphabetic content at all
    if not re.search(r"[a-zA-Z]", cleaned):
        return False, "no alphabetic content"

    # Wake word check — must come first so we measure words after stripping it
    if WAKE_WORD and not lower.startswith(WAKE_WORD):
        return False, f"missing wake word '{WAKE_WORD}'"

    # Count meaningful words after stripping the wake word
    without_wake = _strip_wake_word(cleaned)
    words = [w for w in without_wake.split() if re.search(r"[a-zA-Z]", w)]
    if len(words) < MIN_WORDS:
        return False, f"too short after wake word ({len(words)} word)"

    return True, "ok"


def strip_wake_word(transcript: str) -> str:
    """
    Remove the wake word prefix from a validated transcript before forwarding.
    Safe to call even if no wake word is configured.
    """
    return _strip_wake_word(transcript.strip())


def _strip_wake_word(text: str) -> str:
    if not WAKE_WORD:
        return text
    lower = text.lower()
    if lower.startswith(WAKE_WORD):
        text = text[len(WAKE_WORD):].strip(" ,.")
    return text

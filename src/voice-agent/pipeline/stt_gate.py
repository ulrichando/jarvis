"""STT-confidence gate (transcript-shape filter).

Conservative upstream filter for STT transcripts that are obviously
not real user speech. Cheaper + more reliable than the post-LLM
`drop_pure_hedge` filter it replaced (that one was eating legitimate
replies like "I'm here.").

Filters:
  - Empty / punctuation-only / single-char
  - Bare filler tokens ("uh", "hmm", "ah", etc.)
  - Repeated-word stutters ("uh uh uh", "la la la")
  - Whisper silence-hallucinations ("thank you", "subscribe",
    "music", etc.) — Whisper emits these when fed sub-speech audio
    (room tone, breath, soft utterance starts). Sourced from
    openai/whisper#928, faster-whisper FAQ, ggerganov/whisper.cpp#1189
    plus live 2026-05-04 captures.

Words that double as legitimate replies (yes/no/yeah/okay/right) are
NOT filtered — those are valid confirmations standing alone.

Hoisted from `jarvis_agent.py` 2026-05-10 (Step 9 of the audit —
test_stt_garbage_gate was reaching directly into jarvis_agent for a
private helper; now it has a proper public API home).
"""
from __future__ import annotations

import re


__all__ = ["FILLER_TOKENS", "WHISPER_HALLUCINATIONS", "is_garbage_transcript"]


# Pure non-content fillers that are 100% noise when alone. NOT in the
# set: "yes", "no", "yeah", "yep", "okay", "right" — those are valid
# confirmations / acknowledgements when standing alone in context.
FILLER_TOKENS: frozenset[str] = frozenset({
    "uh", "uhh", "uhm", "um", "umm",
    "hm", "hmm", "hmmm",
    "ah", "ahh", "oh", "ohh",
    "eh", "huh", "mhm", "mmhm",
})

# Whisper silence-hallucinations. When Whisper is fed sub-speech audio
# (room tone, breath, mic_aec residual, soft start of a real utterance
# that VAD opened on too early) it doesn't return empty — it emits
# phrases that dominate its training data. Those are then routed as
# real transcripts: 2026-05-04 the canonical " Thank you." landed in
# the BANTER fast-path → llama-3.1-8b-instant attempted a malformed
# tool call → Groq returned "Failed to call a function" → breaker
# opened → 30 s recovery cascade → user assumed JARVIS missed them
# and repeated, second attempt transcribed cleanly. Filtering these
# at the upstream gate is both cheaper and unambiguous: a user
# volunteering only "thanks for watching" to a voice assistant is
# not a real interaction.
WHISPER_HALLUCINATIONS: frozenset[str] = frozenset({
    "thank you",
    "thanks",
    "thank you for watching",
    "thanks for watching",
    "thanks for watching the video",
    "thank you for watching the video",
    "subscribe",
    "subscribe to my channel",
    "like and subscribe",
    "please subscribe",
    "music",
    "applause",
    "laughter",
    "you",
    "you you",
    "you you you",
    "bye bye",
    "okay bye",
    "see you",
    "see you next time",
})


def is_garbage_transcript(text: str) -> tuple[bool, str]:
    """Return (is_garbage, reason).

    Conservative upstream gate: only the most obvious noise patterns
    return True. Returns the rule that fired so the caller can log it
    for tuning.
    """
    if text is None:
        return True, "none"
    s = text.strip().lower()
    if not s:
        return True, "empty"

    # Pure punctuation / ellipsis / "..." — no alphanumeric content.
    # Use Unicode-aware alpha check so non-Latin scripts (Hanzi, Kana,
    # Cyrillic, Hangul) are NOT misclassified as punctuation-only and
    # can flow through to the non-latin-fragment check below.
    if not any(c.isalnum() for c in text):
        return True, "punctuation-only"

    # Single bare filler token alone — drop. (Punctuation stripped.)
    only_word = re.sub(r"[^a-z]", "", s)
    if only_word and only_word in FILLER_TOKENS:
        return True, f"filler:{only_word}"

    # Repeated-word stutter: "uh uh uh", "la la la", "yeah yeah" —
    # ≥2 words, all identical. Real speech rarely has this shape.
    words = s.split()
    if len(words) >= 2 and len(set(words)) == 1:
        return True, f"repeated:{words[0]}"

    # Single-character noise.
    if len(only_word) == 1:
        return True, "single-char"

    # Whisper silence-hallucination phrases. Normalise to alnum +
    # single spaces so " Thank you. " and "thank you!" both match
    # "thank you".
    norm = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()
    if norm in WHISPER_HALLUCINATIONS:
        return True, f"whisper-hallucination:{norm}"

    # Non-Latin script fragment. Whisper turbo, fed sub-speech audio
    # from a background TV in another language, transcribes in that
    # language's native script — Cyrillic ("Добрый день"), Kana
    # ("クリノイズアイマ"), Hanzi ("再見"), Hangul, etc. JARVIS is
    # English-only per CLAUDE.md, so any fragment that's mostly
    # non-Latin alphabetic AND short is almost certainly bleed-through.
    # 50% threshold so mixed-script real speech ("the iPhone is 漂亮")
    # passes; 12-char cap so legitimate short responses aren't trapped
    # if they happen to contain a non-Latin character.
    # Live evidence (2026-05-16 telemetry audit): 14+ recent turns had
    # foreign-script user_text, three triggered >700 s LLM stalls,
    # one (turn 160) produced a hallucinated Bosnian reply. Source:
    # docs/reviews/2026-05-16/jarvis-review-ai.md §P0-1.
    raw_text = text  # keep original case + Unicode for script check
    alpha = [c for c in raw_text if c.isalpha()]
    if alpha and len(raw_text) < 12:
        non_latin = sum(1 for c in alpha if not ("a" <= c.lower() <= "z"))
        if non_latin / len(alpha) > 0.5:
            return True, f"non-latin-fragment:{raw_text[:20]!r}"

    return False, ""

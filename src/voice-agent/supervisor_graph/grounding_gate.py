"""Grounding gate — validates supervisor draft text against the
blackboard. The structural cure for "JARVIS lies about completion."

Pipeline:
  draft text → extract_claims → for each claim: find evidence on
  blackboard.tools → if all matched, RELEASE. If any unmatched,
  REJECT with retry budget (max 3) → if exhausted, replace with
  fixed honest fallback.

This file currently exposes only the tokenizer. The node body is
added in Task 11.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("supervisor_graph.grounding_gate")


# Past-tense / completion-state markers. Each is a regex that matches
# the verb form. New verbs added to this list should also match a
# "subject-of-action" noun within ~6 words for keyword extraction.
#
# The list is intentionally NARROW. False negatives (a real claim slips
# through unflagged) cost the user nothing; false positives (an
# innocent statement gets rejected) cost the user a real reply. So we
# only flag verbs that strongly assert a discrete completed action.
_CLAIM_VERBS = (
    "opened",   # "I've opened the tab"
    "open",     # "Tab is open." / "Chrome is open."
    "closed",
    "saved",
    "sent",
    "posted",
    "done",
    "launched",
    "created",
    "deleted",
    "clicked",
    "typed",
    "navigated",
    "switched",
    "pressed",
    "submitted",
    "uploaded",
    "downloaded",
)

# A claim verb must NOT be preceded by these words within 3 tokens —
# they signal future / hypothetical / question, not past completion.
_NEGATING_PREFIXES = (
    "should", "could", "would", "may", "might", "can", "will",
    "shall", "let", "lets", "let's", "want", "wants", "ought",
)


@dataclass
class Claim:
    """One past-tense success claim extracted from supervisor text."""
    verb: str
    keywords: list[str] = field(default_factory=list)
    span: tuple[int, int] = (0, 0)  # (start_idx, end_idx) in original text


def _tokenize(text: str) -> list[tuple[str, int, int]]:
    """Split text into (lowercase_word, start, end) tuples preserving
    span info. Punctuation stripped."""
    out = []
    for m in re.finditer(r"[A-Za-z']+", text):
        out.append((m.group(0).lower(), m.start(), m.end()))
    return out


def extract_claims(text: str) -> list[Claim]:
    """Walk the text looking for claim verbs. For each, check the
    preceding 3 tokens for negating prefixes. If clear, collect the
    next 6 tokens (excluding stopwords) as object keywords."""
    if not text or not text.strip():
        return []
    tokens = _tokenize(text)
    if not tokens:
        return []

    stopwords = {
        "a", "an", "the", "i", "i've", "i'm", "to", "for", "on", "in",
        "and", "or", "but", "is", "are", "was", "were", "be", "been",
        "have", "has", "had", "of", "with", "at", "by", "from", "your",
        "my", "sir", "now", "just", "also", "also,", "yes", "no", "ok",
        "okay", "all", "that", "this", "it", "its", "as", "so",
    }

    claims: list[Claim] = []
    for i, (word, start, end) in enumerate(tokens):
        if word not in _CLAIM_VERBS:
            continue
        # Negation check: scan up to 3 preceding tokens.
        prev_window = [t[0] for t in tokens[max(0, i - 3):i]]
        if any(p in _NEGATING_PREFIXES for p in prev_window):
            continue
        # Object keyword extraction: next 6 tokens minus stopwords.
        next_window = tokens[i + 1:i + 7]
        keywords = [t[0] for t in next_window if t[0] not in stopwords]
        # Also include preceding noun-like tokens (1-2 back) as keywords
        # for shapes like "Tab is open" → keywords=[tab].
        prev_kw = [t[0] for t in tokens[max(0, i - 2):i]
                   if t[0] not in stopwords and t[0] not in _NEGATING_PREFIXES]
        keywords = prev_kw + keywords
        claims.append(Claim(verb=word, keywords=keywords, span=(start, end)))
    return claims

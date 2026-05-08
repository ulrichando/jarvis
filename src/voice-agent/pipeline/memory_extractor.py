# src/voice-agent/pipeline/memory_extractor.py
"""Auto-extraction of memorable facts from user turns.

Bypasses the supervisor LLM's tool-choice surface entirely — runs
a small fast LLM (llama-3.1-8b-instant) on each user transcript,
parses a structured output, writes directly to state.db.memories
via the existing _publish_event path.

Pattern from Mem0/Zep production deployments
(github.com/mem0ai/mem0/issues/3999) — function-tool registration
for memory is unreliable on Llama-class models; the maintainers
themselves recommend turn-boundary auto-injection instead.

Two-step design so unit tests can cover parsing without an LLM:
- parse_extractor_output(): pure string → ExtractedMemory|None
- extract_memory_from_turn(): async LLM call + parse + publish
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("jarvis.memory_extractor")

EXTRACTOR_SKIP = "SKIP"
_VALID_CATEGORIES = ("user", "feedback", "project", "reference")
_MAX_CONTENT_CHARS = 500


@dataclass(frozen=True)
class ExtractedMemory:
    category: str
    content: str


_LINE_RE = re.compile(r"^\s*([a-z]+)\s*:\s*(.+?)\s*$", re.DOTALL)
_QUOTE_STRIP = re.compile(r'^["\']|["\']$')


def parse_extractor_output(raw: str) -> ExtractedMemory | None:
    """Parse `<category>: <content>` lines from the extractor LLM.

    Returns None for SKIP, malformed output, invalid category, or
    over-length content. Defensive — if anything looks off, drop
    the candidate rather than write garbage.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if text.upper() == EXTRACTOR_SKIP:
        return None

    m = _LINE_RE.match(text)
    if not m:
        return None
    category = m.group(1).lower().strip()
    content = m.group(2).strip()

    # Strip surrounding quotes the LLM sometimes adds.
    while content and content[0] in ('"', "'") and content[-1] == content[0]:
        content = content[1:-1].strip()

    if category not in _VALID_CATEGORIES:
        return None
    if not content:
        return None
    if len(content) > _MAX_CONTENT_CHARS:
        return None
    return ExtractedMemory(category=category, content=content)

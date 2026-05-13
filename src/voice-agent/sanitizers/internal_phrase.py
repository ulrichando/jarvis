"""Drop framework-internal terminology from voiced assistant output.

Live failure 2026-05-11 16:42 UTC: a subagent bailed with the
internal status phrase "not a screen-share task". The supervisor
LLM saw that string in its tool_result context and echoed it
verbatim as voiced text — the user heard a literal bailout-shape
phrase that was never meant for them.

This sanitizer is the last line of defense. The subagents/agent.py
task_done method already masks bailout summaries before handing
back, but a careless prompt regression OR a subagent that improvises
its own bailout phrasing could still leak. The sanitizer blanks any
assistant output that:

  1. IS one of the bailout / internal phrases (matched tightly so
     normal speech isn't false-positived), OR
  2. Contains framework-internal terminology like "subagent",
     "supervisor", "task_done", "handing back" in user-facing voice.

When the entire reply is just an internal phrase, the chunk is
replaced with an empty string (silent turn). When the reply
contains an internal phrase wrapped in other content, only the
internal phrase is blanked, the rest is kept.

Designed to work with the existing handoff_text / pycall / dsml
sanitizer stack — patches the same `_parse_choice` extension
point. Idempotent install.
"""
from __future__ import annotations

import logging
import re


logger = logging.getLogger("jarvis.internal_phrase_sanitizer")


_INSTALLED = False


# Phrases that should NEVER appear in voiced output. Each is the
# canonical form of a framework-internal status signal. Matching is
# case-insensitive and word-bounded so "wrong subagent" matches but
# "wrong subassembly" doesn't.
_INTERNAL_PHRASES = [
    # Bailout-summary tokens from subagents/agent.py::_BAILOUT_SUMMARY_RE
    r"user\s+(?:changed|switched)\s+topic",
    r"not\s+(?:a\s+)?(?:desktop|browser|screen[-\s]?share|relevant|valid)\s+task",
    r"wrong\s+subagent",
    r"needs?\s+(?:the\s+)?(?:browser|desktop|planner|supervisor)\s+subagent",
    r"cannot\s+(?:accomplish|act\s+on|handle)",
    # "hand back" / "hand off" / "hand over" / "hand control" — verb-
    # forms of the framework-internal handoff. Live failure 2026-05-13:
    # user heard "handing back a task" (the original regex required a
    # specific suffix "to supervisor"). The broader pattern below
    # catches the verb form + ANY optional connective + ANY optional
    # internal noun, so "handing back a task" / "hand off to the
    # supervisor" / "handed control back" all blank cleanly.
    r"hand(?:ing|ed|s)?\s+(?:back|off|over|control)(?:\s+(?:to|a|the|control|over))*(?:\s+(?:task|tasks?|supervisor|subagent|operations?|control))?",
    # "to the supervisor" without the verb (in case the verb leaks
    # past the above pattern via word ordering).
    r"to\s+(?:the\s+)?supervisor",
    r"not\s+a\s+request\s+I\s+can\s+act\s+on",
    r"screen[-\s]share\s+(?:not\s+active|isn'?t\s+active|off)",
    r"no\s+video\s+frames(?:\s+received)?",
    # Other framework-internal nouns the supervisor might echo if
    # it sees them in chat_ctx. The trailing `\b` from the outer
    # wrapper handles bare `task_done` cleanly; the call form
    # `task_done(...)` ends with `(` which isn't a word char and
    # would defeat `\b`, so list just the bare token here and let
    # the surrounding chars get scrubbed too via the substitution
    # window.
    r"task_done",
    # Past-failure phrases that occasionally leak — keep tight
    # anchoring so we don't blank legitimate uses of the word
    # "subagent" / "supervisor" inside meta-conversation.
    r"transferring\s+(?:to|you)\s+(?:the\s+)?(?:screen[-\s]?share|desktop|browser)\s+subagent",
    r"transfer(?:ring|red)?\s+(?:to|back)\s+(?:the\s+)?supervisor",
    # "Sir" / "ma'am" honorifics. The user has a long-standing no-
    # honorifics rule (CLAUDE.md drop-butler-register overhaul). The
    # supervisor prompt explicitly bans them but the LLM occasionally
    # emits "sir" anyway, especially when chat_ctx contains older
    # turns where the rule wasn't in place. Live failure 2026-05-13:
    # user heard "Both Gmail and YouTube are now open in separate
    # tabs, sir." Blanking here is the last-line defense.
    r"sir",
    r"ma'?am",
]

# Pre-compile a single OR regex over all phrases.
_INTERNAL_RE = re.compile(
    r"\b(?:" + r"|".join(f"(?:{p})" for p in _INTERNAL_PHRASES) + r")\b",
    re.IGNORECASE,
)


def sanitize(text: str) -> str:
    """Return `text` with internal-only phrases blanked.

    - If `text` is JUST an internal phrase (after stripping
      whitespace and punctuation), return an empty string — the
      whole utterance was framework noise.
    - Otherwise, replace each matched internal phrase with a space
      and collapse adjacent whitespace. Dangling punctuation
      stranded by a blanked phrase ("tabs, ." after blanking ", sir")
      is tidied so TTS doesn't pronounce orphan commas.
    """
    if not text:
        return text
    stripped = text.strip(" \t\n.,!?'\"")
    if _INTERNAL_RE.fullmatch(stripped):
        return ""
    cleaned = _INTERNAL_RE.sub(" ", text)
    # Drop dangling commas / semicolons before terminal punctuation:
    # "tabs, ." → "tabs.", "Yes ." → "Yes." (cosmetic, voice-friendly).
    cleaned = re.sub(r"[,;]\s+([.!?])", r"\1", cleaned)
    cleaned = re.sub(r"\s+([.!?])", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def install() -> None:
    """Monkey-patch livekit.agents.inference.llm.LLMStream._parse_choice
    so internal phrases get blanked from `delta.content` before they
    reach TTS. Stacks safely on top of dsml/pycall/handoff_text
    sanitizers — same patch site, idempotent install."""
    global _INSTALLED
    if _INSTALLED:
        return

    try:
        from livekit.agents.inference import llm as inf_llm
    except ImportError:
        logger.warning(
            "[internal-phrase] inference.llm not available; sanitizer skipped"
        )
        _INSTALLED = True
        return

    if getattr(inf_llm.LLMStream, "_jarvis_internal_phrase_patched", False):
        _INSTALLED = True
        return

    orig_parse = inf_llm.LLMStream._parse_choice

    def patched(self, id, choice, thinking):
        chunk = orig_parse(self, id, choice, thinking)
        if chunk is None:
            return chunk
        try:
            delta = getattr(chunk, "delta", None)
            if delta is not None and getattr(delta, "content", None):
                cleaned = sanitize(delta.content)
                if cleaned != delta.content:
                    if not cleaned:
                        logger.info(
                            f"[internal-phrase] blanked whole reply "
                            f"(was: {delta.content[:80]!r})"
                        )
                    else:
                        logger.debug(
                            "[internal-phrase] scrubbed internal phrase from reply"
                        )
                    delta.content = cleaned
        except Exception as e:
            logger.debug(f"[internal-phrase] scrub failed (non-fatal): {e}")
        return chunk

    inf_llm.LLMStream._parse_choice = patched
    inf_llm.LLMStream._jarvis_internal_phrase_patched = True
    _INSTALLED = True
    logger.info(
        "[internal-phrase] installed (blanks framework-only phrases from TTS)"
    )

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
    # ── Meta-narration fragments (bare, parens stripped by streaming) ──
    # Live failure 2026-05-18→20: the weak 8b BANTER fast-path voiced
    # "(Ambient conversation — not directed at me.)" instead of staying
    # silent. _STAGE_DIRECTION_RE below removes the whole parenthetical
    # when it arrives in one chunk; these bare forms catch the streamed
    # remainder ("not directed at me.)" / "ambient conversation") whose
    # opening "(" landed in a prior chunk. "the user was/is …" is NOT
    # listed bare (too easy to false-positive on "the user manual is …")
    # — it only blanks inside the parenthetical regex.
    r"not\s+directed\s+at\s+(?:me|you)",
    r"ambient\s+conversation",
    # ── Recovery theater (prompt-banned confusion narration) ──
    # supervisor.md WHEN-INPUT-UNCLEAR rule bans "I'm catching pieces…"
    # / "Got fragments…" + narrating confusion. The trailing lookahead
    # requires the demonstrative to be clause-final (punctuation/dash/
    # end), so "tracking this bug" / "parsing the file" pass through.
    r"i'?m\s+catching\s+(?:fragments|pieces|bits|parts)(?:\s+here)?",
    r"i'?m\s+not\s+(?:quite\s+)?(?:tracking|parsing|following)\s+(?:this|that)(?:\s+clearly)?(?=\s*(?:[—–\-.,!?]|$))",
    r"i'?m\s+having\s+trouble\s+(?:tracking|parsing|following)\s+(?:this|that)(?=\s*(?:[—–\-.,!?]|$))",
]

# Pre-compile a single OR regex over all phrases.
_INTERNAL_RE = re.compile(
    r"\b(?:" + r"|".join(f"(?:{p})" for p in _INTERNAL_PHRASES) + r")\b",
    re.IGNORECASE,
)

# Parenthetical stage-directions: "(Ambient conversation — not directed
# at me.)". A voiced turn is never legitimately a parenthetical aside —
# the supervisor prompt says output ZERO characters for ambient/not-
# directed input, and class-(C) bans voicing the silence. The closing
# ")" is optional so a chunk that ends mid-stream ("(Ambient
# conversation —") still matches. Kept separate from _INTERNAL_RE
# because the leading "(" defeats that regex's \b word-boundary anchor.
_STAGE_DIRECTION_RE = re.compile(
    r"\(\s*[^)]*\b(?:"
    r"not\s+directed\s+at\s+(?:me|you|jarvis)"
    r"|not\s+(?:for|addressed\s+to|aimed\s+at)\s+me"
    r"|ambient\s+conversation"
    r"|background\s+(?:conversation|chatter|noise|speech)"
    r"|the\s+user\s+(?:was|is|seems|appears)"
    r"|(?:remaining|staying|being|keeping)\s+silent"
    r"|no\s+response\s+(?:needed|required|necessary)"
    r"|observing"
    r"|just\s+listening"
    r")\b[^)]*\)?",
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

    # Parenthetical stage-directions: remove the whole "(...)" aside.
    # If nothing meaningful survives, the entire turn was meta-narration
    # the user must never hear → silent turn.
    if _STAGE_DIRECTION_RE.search(text):
        text = _STAGE_DIRECTION_RE.sub(" ", text)
        if not text.strip(" \t\n.,!?'\"()—–-"):
            return ""

    # Streaming guard: a voiced turn that opens with "(" before its
    # closing ")" has arrived is the start of a stage-direction aside
    # (the meta keyword may be in a later chunk). Never legitimate.
    lead = text.lstrip(" \t\n'\"")
    if lead.startswith("(") and ")" not in lead:
        return ""

    stripped = text.strip(" \t\n.,!?'\"()")
    if not stripped:
        # Punctuation/whitespace-only CHUNK — a legitimate BPE streaming
        # delta (models emit "," / "." as standalone tokens). Blanking
        # these (the pre-2026-07-02 behavior) silently deleted every
        # standalone . , ! ? from every voiced reply and the conversation
        # DB across ALL providers since 2026-05-25 — "no heart no pulse
        # Just processing cycles". Em-dashes survived only because — is
        # not in the strip set above. Pass through verbatim; a whole
        # reply that is letterless is dropped downstream by
        # strip_emote_markup's unspeakable-reply guard.
        return text
    if _INTERNAL_RE.fullmatch(stripped):
        return ""
    # Fast path: no internal phrase in this chunk → return as-is so we
    # don't perturb its whitespace. Critical for streaming chunks: BPE
    # tokens carry their leading space (" well", " thanks") and any
    # .strip() / whitespace-collapse below would eat that leading
    # space, producing the "wellthanks." mash heard 2026-05-15 with
    # gpt-5.1 streaming output.
    if not _INTERNAL_RE.search(text):
        return text
    cleaned = _INTERNAL_RE.sub(" ", text)
    # Drop dangling commas / semicolons before terminal punctuation:
    # "tabs, ." → "tabs.", "Yes ." → "Yes." (cosmetic, voice-friendly).
    cleaned = re.sub(r"[,;]\s+([.!?])", r"\1", cleaned)
    cleaned = re.sub(r"\s+([.!?])", r"\1", cleaned)
    # Collapse runs of whitespace to a single space (the substitution
    # above may have introduced one), but do NOT strip leading/trailing
    # — for per-chunk streaming, that strip is what eats inter-word
    # spaces of BPE-tokenized streams.
    cleaned = re.sub(r"\s+", " ", cleaned)
    # If scrubbing stranded only punctuation / dashes (e.g. "I'm not
    # tracking this — " → " — "), there's nothing voiceable left.
    if not cleaned.strip(" \t\n.,!?'\"()—–-"):
        return ""
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

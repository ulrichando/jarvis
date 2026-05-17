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
import time
from dataclasses import dataclass

logger = logging.getLogger("jarvis.memory_extractor")


# Timestamp of the most recent successful extraction (epoch seconds).
# Read by `confab_detector._has_tool_evidence` to grant evidence credit
# for "saved/remembered" claims that ride the auto-extract path
# (which doesn't fire a tool call the supervisor's chat_ctx can see).
# Live capture 2026-05-08 13:18: confab dropped two consecutive
# "I've confirmed your wife's name, Lizzie, is saved" turns even
# though the extractor DID write the fact.
#
# Thread/concurrency note: this is a module-global mutable. Safe under
# the voice-agent's single-event-loop runtime (extract_memory_from_turn
# is dispatched via `asyncio.create_task` per turn, never threaded), and
# any interleave window between two simultaneous extractions is
# absorbed by the 30 s TTL — both refer to recent activity. If the
# voice-agent ever spawns the extractor on a separate thread or
# multiprocessing pool, wrap reads/writes with `asyncio.Lock`.
_LAST_EXTRACTION_SUCCESS_AT: float | None = None
# How long an extractor success stays "fresh" as evidence. Long enough
# to cover the supervisor's reply turn (typical: a few seconds), short
# enough that a stale 5-minute-old extraction doesn't grant evidence
# to an unrelated confab.
_EXTRACTION_EVIDENCE_TTL_SECONDS = 30.0


def last_extraction_success_at() -> float | None:
    """Return the epoch ts of the last successful extraction, or None."""
    return _LAST_EXTRACTION_SUCCESS_AT


def has_recent_extraction_evidence(now: float | None = None) -> bool:
    """True if a successful extraction landed within the last
    `_EXTRACTION_EVIDENCE_TTL_SECONDS`. Used by the confab detector
    to avoid false-positive drops on memory-write turns."""
    if _LAST_EXTRACTION_SUCCESS_AT is None:
        return False
    now = now if now is not None else time.time()
    return (now - _LAST_EXTRACTION_SUCCESS_AT) <= _EXTRACTION_EVIDENCE_TTL_SECONDS


def _mark_extraction_success() -> None:
    """Record that the extractor just produced a parsed ExtractedMemory.
    Test seam — also called by `extract_memory_from_turn` on success."""
    global _LAST_EXTRACTION_SUCCESS_AT
    _LAST_EXTRACTION_SUCCESS_AT = time.time()

EXTRACTOR_SKIP = "SKIP"
_VALID_CATEGORIES = ("user", "feedback", "project", "reference")
_MAX_CONTENT_CHARS = 500


@dataclass(frozen=True)
class ExtractedMemory:
    category: str
    content: str


_LINE_RE = re.compile(r"^\s*([a-z]+)\s*:\s*(.+?)\s*$", re.DOTALL)
_QUOTE_STRIP = re.compile(r'^["\']|["\']$')

# Meta-paraphrase reject filter (added 2026-05-08, fix D in the
# voice-channel audit). Live extractions captured this shape:
#   - "The user inquires about the history of England"
#   - "The user is expressing gratitude for the time spent"
#   - "The conversation has shifted to a casual topic about a bird"
#   - "It seems to be a mixed review of a product or service"
#   - "User appears to be requesting mute"
#   - "Coding Kiddos appears to involve a simulation or game"
#
# These are LLM-meta narration of what was *said*, not stable facts
# about the user / project / preferences. They pollute the memory
# store with junk and (worse) flow into chat_ctx via recall, training
# the supervisor LLM to mirror the meta-paraphrase shape on its own
# replies (= the "summarize-everything" failure mode from fix E).
#
# Anchored at start-of-content; case-insensitive. Mirrors phrasings
# the user/project type would never legitimately start with.
_META_PARAPHRASE_RE = re.compile(
    r"""(?ix)
    (?:
        # Subject-anchored at start: "The user is/has X-ing", "The
        # conversation has Y-ed", "It seems to be Z", "User asked
        # about W". These are LLM narration of the conversation.
        ^\s*the\s+(?:user|conversation|discussion|topic|exchange)
            \s+(?:is|was|has|appears|seems|seemed|inquires|expresses|expressed|
                 mentions|describes|asks|asked|seeks|sought|wants|wanted)
      | ^\s*(?:it|this|that)\s+(?:seems|appears|looks|sounds)\s+(?:to|like)\b
      | ^\s*user\s+(?:appears|seems|seemed|inquires|inquired|expresses|
               expressed|mentions|mentioned|describes|described|asks|asked|
               seeks|sought|wants|wanted)\b
      | ^\s*the\s+user\s+seeks
    )
    """
    # Narrowed 2026-05-08 (code review): the previous broad
    # "\b(?:appears|seems)\s+to\s+(?:be|involve|...)\b" alternation
    # rejected genuine hedged facts about user projects, e.g.
    # "Pretva appears to involve regulatory work in Cameroon."
    # Start-anchored narration-subject rules above still catch the
    # actual LLM-narration shapes ("The user appears to...", "It seems
    # to..."). A hedged-but-real project fact like "Coding Kiddos
    # appears to involve teaching" now passes through; the few-shot
    # extractor prompt's anti-examples already discourage that shape
    # at the LLM level.
)


def _is_meta_paraphrase(content: str) -> bool:
    """Return True for LLM-meta-narration outputs that should be
    dropped instead of stored as memory. See `_META_PARAPHRASE_RE`."""
    return bool(_META_PARAPHRASE_RE.search(content or ""))


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
    # Drop meta-paraphrase outputs (e.g. "The user is X-ing", "The
    # conversation has shifted to Y") — these are LLM narration, not
    # facts. Live captures: 2026-05-08 17:07/17:47/17:51/17:52.
    if _is_meta_paraphrase(content):
        logger.info(
            f"[extractor] meta-paraphrase rejected: {content[:80]!r}"
        )
        return None
    return ExtractedMemory(category=category, content=content)


# Few-shot examples — calibrated against the spec's "live failure"
# 2026-05-08 conversation. Order matters: positives first to bias
# the model toward extraction, then 2 SKIP examples to teach refusal.
_EXTRACTOR_PROMPT = """You read a single line of user speech and decide \
whether it contains a stable, memorable fact about the user or their \
ongoing work. If yes, output exactly one line in the form \
'<category>: <one-sentence summary>'. Categories: user, feedback, \
project, reference. If no memorable fact, output exactly: SKIP.

Examples — note the subjects are DIVERSE (different domains, different \
projects, different proper nouns). When extracting from new speech, \
copy the SHAPE, not any specific name or domain from the examples \
below. Background TV audio that Whisper hallucinates as English does \
NOT contain real user facts — output SKIP.

USER: "my wife's name is Lizzy"
OUTPUT: user: Ulrich's wife is named Lizzy.

USER: "i run pretva, a ride hailing service in cameroon"
OUTPUT: user: Ulrich runs Pretva, a ride-hailing service in Cameroon.

USER: "i have a background in medical psychology"
OUTPUT: user: Ulrich has a background in medical psychology.

USER: "we use proxmox for the home lab and run six vms on a single node"
OUTPUT: project: Home lab runs Proxmox on a single node, ~6 VMs.

USER: "jarvis runs on a dell latitude 7480 with an intel i7-7600u"
OUTPUT: reference: Primary JARVIS hardware: Dell Latitude 7480, Intel i7-7600U (2C/4T Kaby Lake).

USER: "every time i ask jarvis to remember he says he can't"
OUTPUT: feedback: User reports JARVIS denies its own memory capability when asked. Why: the supervisor LLM defaults to 'I'm a conversational AI without memory' from training data. How to apply: prefer the auto-extractor and denial-detector layers over relying on the supervisor LLM to call remember() proactively.

USER: "stop ending replies with 'is there anything else i can help with'"
OUTPUT: feedback: User dislikes trailing "anything else I can help with" / closer phrases. How to apply: cap the reply at substantive content; don't append follow-up offers unless the user asked.

USER: "i'm thirsty"
OUTPUT: SKIP

USER: "yeah okay"
OUTPUT: SKIP

USER: "thanks for that"
OUTPUT: SKIP

USER: "could you tell me about the history of england"
OUTPUT: SKIP

USER: "what's the weather like"
OUTPUT: SKIP

USER: "再見"
OUTPUT: SKIP

USER: "Thank you for watching"
OUTPUT: SKIP

ANTI-EXAMPLES (these shapes are FORBIDDEN — they are conversation \
narration, not facts; output SKIP instead):
  ✗ "The user is asking about X" — narration
  ✗ "The user appears to be X-ing" — narration
  ✗ "The conversation has shifted to X" — narration
  ✗ "It seems to be X" — narration
  ✗ "User wants to know about X" — narration

ALSO FORBIDDEN — copying example subjects when speech doesn't \
mention them (this is how the memory store ended up polluted with \
fictional "Coding Kiddos" narration on background TV audio):
  ✗ Outputting "Coding Kiddos ..." when the user didn't say it
  ✗ Outputting "Pretva ..." when the user didn't say it
  ✗ Outputting "Ulrich's ..." when the speech is generic
  ✗ Any proper noun not present in the user's actual line

A FACT looks like:
  ✓ "Ulrich's wife is named Lizzy."  (when user said Lizzy)
  ✓ "Home lab runs Proxmox on a single node."  (when user said Proxmox)
  ✓ "User prefers responses to start with content, not 'Sure thing'."

USER: "{transcript}"
OUTPUT:"""


async def _call_extractor_llm(transcript: str) -> str:
    """Call llama-3.1-8b-instant via Groq with the extractor prompt.
    Isolated function so tests can monkeypatch it without an API key."""
    import os
    import httpx

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.debug("[extractor] GROQ_API_KEY missing — skipping extraction")
        return EXTRACTOR_SKIP

    prompt = _EXTRACTOR_PROMPT.format(transcript=transcript.replace('"', "'"))

    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 160,
                    "temperature": 0.0,
                    "stop": ["\nUSER:", "\n\n"],
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"[extractor] LLM call failed: {type(e).__name__}: {e}")
            return EXTRACTOR_SKIP


async def extract_memory_from_turn(
    transcript: str,
) -> ExtractedMemory | None:
    """Top-level extractor entry point. Returns None if SKIP /
    parse-fail / LLM error. Caller handles the publish step.

    Wired into JarvisAgent.on_user_turn_completed in jarvis_agent.py.
    Runs in parallel with the supervisor LLM (asyncio.create_task)
    so it doesn't add latency on the critical path.
    """
    if not transcript or not transcript.strip():
        return None
    raw = await _call_extractor_llm(transcript.strip())
    parsed = parse_extractor_output(raw)
    if parsed is not None:
        logger.info(
            f"[extractor] {parsed.category}: {parsed.content[:80]!r}"
        )
        # Mark extractor-success evidence for the confab detector.
        _mark_extraction_success()
        # Tell the consolidator a successful extraction landed; on every
        # Nth call (default 10) it schedules consolidate_all_categories
        # via asyncio.create_task. Lazy import so a circular at module
        # load doesn't surface (consolidator imports _META_PARAPHRASE_RE
        # from this module).
        try:
            from pipeline.memory_consolidator import record_extraction
            record_extraction()
        except Exception as e:
            logger.warning(
                f"[extractor] record_extraction failed: {type(e).__name__}: {e}"
            )
    return parsed

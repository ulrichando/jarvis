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


# Few-shot examples — calibrated against the spec's "live failure"
# 2026-05-08 conversation. Order matters: positives first to bias
# the model toward extraction, then 2 SKIP examples to teach refusal.
_EXTRACTOR_PROMPT = """You read a single line of user speech and decide \
whether it contains a stable, memorable fact about the user or their \
ongoing work. If yes, output exactly one line in the form \
'<category>: <one-sentence summary>'. Categories: user, feedback, \
project, reference. If no memorable fact, output exactly: SKIP.

Examples:

USER: "we charge them six hundred dollars for six months"
OUTPUT: project: Coding Kiddos charges $600 for 6 months ($100/mo) per student.

USER: "my wife's name is Lizzy"
OUTPUT: user: Ulrich's wife is named Lizzy.

USER: "we teach python javascript and lua"
OUTPUT: project: Coding Kiddos curriculum covers Python, JavaScript, and Lua.

USER: "i run pretva, a ride hailing service in cameroon"
OUTPUT: user: Ulrich runs Pretva, a ride-hailing service in Cameroon.

USER: "every time i ask jarvis to remember he says he can't"
OUTPUT: feedback: User reports JARVIS denies its own memory capability when asked. Why: the supervisor LLM defaults to 'I'm a conversational AI without memory' from training data. How to apply: prefer the auto-extractor and denial-detector layers over relying on the supervisor LLM to call remember() proactively.

USER: "i'm thirsty"
OUTPUT: SKIP

USER: "yeah okay"
OUTPUT: SKIP

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
    return parsed

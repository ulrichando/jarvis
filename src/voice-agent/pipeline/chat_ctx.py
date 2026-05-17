"""Chat-context seeding + recall-time sanitization filters.

Two concerns wrapped together because they're tightly coupled at
runtime:

  1. Loading recent prior turns from `~/.jarvis/hub/state.db` and
     filtering ambient/household chatter (kids, TV, family talking
     past JARVIS) so seeding doesn't pollute context.

  2. Scrubbing those assistant turns through the same register /
     silence / tool-leak filters used in the live TTS chain — so
     historical bad replies don't poison the model via in-context-
     example weighting (industry-standard pattern, mirrors how
     OpenAI ChatGPT memories and Anthropic Claude.ai summaries
     filter past turns before re-injection).
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

from livekit.agents.llm import ChatContext, ChatMessage

logger = logging.getLogger("jarvis.chat_ctx")


# ── Config ───────────────────────────────────────────────────────────
# Default 12 turns. Realtime mode overrides via JARVIS_RECENT_TURNS=4
# (set in jarvis_agent.py before seed_chat_ctx() is called) because
# each prior turn costs 500-1000 tokens and OpenAI Realtime's 40k TPM
# means 12 prior turns + 6k instructions + tools burns the per-request
# budget. Read at CALL time (not import time) so the realtime override
# takes effect even when this module was imported earlier.
import os as _os
RECENT_TURNS_LIMIT: int = 12

def _current_recent_turns_limit() -> int:
    return int(_os.environ.get("JARVIS_RECENT_TURNS", str(RECENT_TURNS_LIMIT)))

# Cap recalled assistant turns at this many characters. Long historical
# replies (a 574-char essay, a 1099-char monologue on entropy) get
# truncated to the first sentence before re-injection — otherwise
# they prime Claude Haiku 4.5 to copy the same long shape on every
# subsequent similar question. Live failure 2026-05-11: user asked
# "What's in your mind?" twice in 9 minutes, got the exact same
# 574-char architecture essay both times because the first one was
# in chat_ctx via recall. The 30-word ceiling rule in supervisor.md
# loses to in-context examples; trimming the examples is the fix.
# 250 chars ≈ 5s of audio — generous enough to keep a real one-
# sentence answer intact, tight enough to kill essays.
RECALL_ASSISTANT_MAX_CHARS: int = 250


# ── Filter 1: leaked tool-call text ──────────────────────────────────
TOOL_LEAK_RE: re.Pattern[str] = re.compile(
    # XML attribute form: `<function=name>...</function>` (W-015)
    r"<function\s*=\s*[a-zA-Z_][a-zA-Z0-9_]*[^>]*>.*?</function>"
    # XML bare-tag form: `<function>name</function>` (W-016)
    r"|<function\s*>.*?</function>"
    # Orphaned `<arguments>...</arguments>` chunk (W-016)
    r"|<arguments\s*>.*?</arguments>"
    # Trailing close after content was suppressed (legacy heuristic)
    r"|[^<]{0,500}</function>"
    # Alternate tag format
    r"|<tool_call>.*?</tool_call>"
    # Pipe-bracket format
    r"|<\|tool_call\|>.*?<\|/tool_call\|>"
    # JSON array of tool-call objects (W-016)
    r"|\[\s*\{\s*\"(?:name|tool|function)\"\s*:.*?\]"
    # Python call form for known subagent-internal tools (W-015)
    r"|task_done\s*\([^)]*\)"
    r"|<\|end_header_id\|>"
    # W-019 (2026-05-05): prompt-label preambles.
    r"|^\s*(?:Bare-vocative call|Bare vocative call|"
    r"\[TASK mode\][^\n]*|"
    r"Recognized as[^\n]*|"
    r"Following the bare-vocative rule[^\n]*|"
    r"Classification:[^\n]*|"
    r"Mode:[^\n]*|"
    r"Category:[^\n]*)"
    r"[.:]?\s*\n+",
    re.DOTALL | re.MULTILINE,
)


def sanitize_leaked_tool_text(s: str) -> str:
    """Strip any text that looks like a leaked structured tool-call."""
    if not s:
        return ""
    return TOOL_LEAK_RE.sub("", s).strip()


# ── Filter 2: meta-silence ────────────────────────────────────────────
META_SILENCE_RE: re.Pattern[str] = re.compile(
    r"^\s*\[?\(?\s*"
    r"(?:silent|silence|silently|quiet|quietly|listening|just\s+listening|"
    r"observing|standing\s+by|noted|quietly\s+noted|"
    r"empty\s+output|no\s+reply|no\s+output|nothing\s+to\s+say|nothing|"
    r"\(\s*empty\s*\)|\(\s*silent\s*\)|\(\s*no\s+reply\s*\))"
    r"(?:[\s,—-]+sir)?[\s.,!?\]\)]*$",
    re.IGNORECASE,
)


# ── Filter 3: archaic / British-butler openers ───────────────────────
ARCHAIC_OPENER_RE: re.Pattern[str] = re.compile(
    r"^\s*"
    r"(?:indeed|quite(?:\s+well|\s+right|\s+so)?|splendid|naturally|"
    r"very\s+well|at\s+once|excellent|certainly|"
    r"a(?:n)?\s+(?:interesting|fine|fair)\s+(?:question|result|point)|"
    r"worth\s+(?:examining|considering)|i\s+see)"
    r"(?:[,.\s—-]+sir)?"
    r"[\s,.!?—-]+",
    re.IGNORECASE,
)


def _truncate_to_sentence(s: str, max_chars: int) -> str:
    """Truncate to <=max_chars at the nearest sentence boundary at or
    before the cap. Falls back to a hard char cap with an ellipsis if
    no boundary is found in the leading window."""
    if len(s) <= max_chars:
        return s
    window = s[:max_chars]
    # Look for end-of-sentence punctuation followed by space/end.
    # Search right-to-left so we keep as much as possible under the cap.
    for i in range(len(window) - 1, 0, -1):
        if window[i] in ".!?" and (i + 1 == len(window) or window[i + 1] in " \n\t"):
            return window[: i + 1].rstrip()
    # No sentence break found — hard truncate, no ellipsis (the recall
    # is in-context priming, not user-facing text; cleaner without the
    # "…" which the model might mistake for a deliberate trailing).
    return window.rstrip()


def scrub_recalled_assistant_text(text: str) -> str | None:
    """Apply the SAME register/silence/tool-leak filters used in the
    live TTS chain to assistant turns being re-injected into chat_ctx,
    then cap length at RECALL_ASSISTANT_MAX_CHARS so long historical
    replies don't prime the model to copy the long shape.

    Returns the cleaned text, or None if the whole reply should be
    dropped (e.g. it was just a meta-silence ack).
    """
    cleaned = sanitize_leaked_tool_text(text)
    if not cleaned:
        return None
    # Drop whole-reply meta-silence ("Silence." etc).
    if META_SILENCE_RE.match(cleaned):
        return None
    # Drop turns that mention the disabled screen_share subagent —
    # they prime Claude to call `transfer_to_screen_share`, which
    # doesn't exist anymore. Live failure 2026-05-11 15:51 UTC: chat
    # history from a session where the subagent was enabled leaked
    # into a session where it's disabled, Claude said "Let me transfer
    # to the screen subagent", then realized the tool was missing
    # ("I don't have that transfer tool available") — user confused.
    # Drop the recalled turn entirely so Claude has no precedent.
    if _DISABLED_SUBAGENT_RE.search(cleaned):
        return None
    # Trim leading archaic openers ("Quite.", "Indeed.", …).
    m = ARCHAIC_OPENER_RE.match(cleaned)
    if m:
        rest = cleaned[m.end():].lstrip()
        if not rest:
            return None  # whole reply was just the archaic opener
        cleaned = rest[0].upper() + rest[1:]
    # Length cap: stop long historical answers from priming Claude
    # to mimic on the next similar question. See module-level
    # RECALL_ASSISTANT_MAX_CHARS comment for the live failure.
    if len(cleaned) > RECALL_ASSISTANT_MAX_CHARS:
        cleaned = _truncate_to_sentence(cleaned, RECALL_ASSISTANT_MAX_CHARS)
    return cleaned


# Recalled-turn poison filter: drop assistant lines that mention the
# disabled screen_share subagent. Specifically the phrases that
# prime Claude to attempt `transfer_to_screen_share` — a tool that
# was registered in past sessions but is now gated off.
_DISABLED_SUBAGENT_RE = re.compile(
    r"(?:transfer_to_screen_share"
    r"|screen[-\s]share\s+subagent"
    r"|let\s+me\s+(?:transfer\s+(?:to|you)|switch\s+to)\s+(?:the\s+)?screen)",
    re.IGNORECASE,
)


# ── Loader: recent prior turns from state.db ─────────────────────────
def load_recent_turns(limit: int | None = None) -> list[tuple[str, str]]:
    """Return the most recent (role, text) pairs from state.db, OLDEST
    first. Empty list on any error or if the DB doesn't exist yet."""
    if limit is None:
        limit = _current_recent_turns_limit()
    state_db = Path.home() / ".jarvis" / "hub" / "state.db"
    if not state_db.exists():
        return []
    try:
        with sqlite3.connect(str(state_db), timeout=2.0) as conn:
            raw_ms = conn.execute(
                "SELECT ts, role, text FROM messages "
                "WHERE role IN ('user','assistant') "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (limit * 4,),
            ).fetchall()
    except Exception as e:
        logger.warning(f"recall load failed: {e}")
        return []
    raw = [(int(ts // 1000), role, text) for ts, role, text in raw_ms]
    raw.reverse()  # OLDEST first
    REPLY_GAP_S = 60
    kept: list[tuple[str, str]] = []
    for i, (ts, role, text) in enumerate(raw):
        if role == "assistant":
            kept.append((role, text))
            continue
        for j in range(i + 1, len(raw)):
            nts, nrole, _ = raw[j]
            if nts - ts > REPLY_GAP_S:
                break
            if nrole == "assistant":
                kept.append((role, text))
                break
    return kept[-limit:]


# ── Assembly: seed a fresh ChatContext ───────────────────────────────
def seed_chat_ctx() -> ChatContext:
    """Build a ChatContext pre-populated with recent prior turns,
    with assistant turns scrubbed."""
    items: list[ChatMessage] = []
    sanitized = 0
    dropped = 0
    archaic_trimmed = 0
    for role, text in load_recent_turns():
        text = (text or "").strip()
        if not text:
            continue
        if role == "assistant":
            original = text
            cleaned = scrub_recalled_assistant_text(text)
            if cleaned is None:
                dropped += 1
                continue
            if cleaned != original:
                if ARCHAIC_OPENER_RE.match(original):
                    archaic_trimmed += 1
                else:
                    sanitized += 1
            text = cleaned
        items.append(ChatMessage(role=role, content=[text]))
    if items:
        notes = []
        if sanitized: notes.append(f"{sanitized} tool-leak-cleaned")
        if archaic_trimmed: notes.append(f"{archaic_trimmed} archaic-trimmed")
        if dropped: notes.append(f"{dropped} dropped")
        extra = f" ({', '.join(notes)})" if notes else ""
        logger.info(f"[recall] seeded chat_ctx with {len(items)} prior turns{extra}")
    return ChatContext(items=items)

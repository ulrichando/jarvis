"""Chat-context seeding + recall-time sanitization filters.

Two concerns wrapped together because they're tightly coupled at
runtime:

  1. Loading recent prior turns from `~/.jarvis/hub/state.db` (read-
     only). NOTE 2026-05-22: the hub daemon that USED to populate
     that DB was removed entirely. Auto-seed still reads the file
     opportunistically, but on any install after that date the file
     never appears and seed_chat_ctx() returns an empty ChatContext.
     Ambient/household chatter filtering still matters whenever
     residual pre-removal state.db is present.

  2. Scrubbing those assistant turns through the same register /
     silence / tool-leak filters used in the live TTS chain — so
     historical bad replies don't poison the model via in-context-
     example weighting (industry-standard pattern, mirrors how
     OpenAI ChatGPT memories and Anthropic Claude.ai summaries
     filter past turns before re-injection).
"""
from __future__ import annotations

import datetime
import logging
import os
import re
import sqlite3
from pathlib import Path

from livekit.agents.llm import ChatContext, ChatMessage

logger = logging.getLogger("jarvis.chat_ctx")


# ── L3 — recall hygiene (spec: 2026-05-19 §5.3) ──────────────────────
# Live failure 2026-05-19: user said "Okay" (one word). Supervisor
# (Claude Haiku 4.5) had 12 prior-session turns recalled raw into
# chat_ctx — some 4 hours old, including Chrome-related conversation.
# Haiku interpreted "Okay" as confirming a stale Chrome request →
# hallucinated transfer_to_desktop("open Chrome") handoff. Chrome was
# never actually opened, but JARVIS voiced "I've opened Chrome for you."
#
# Defense: drop turns older than JARVIS_RECALL_MAX_AGE_S (default 30
# min) and wrap surviving turns as a single STALE-tagged system message,
# not as N raw role:user / role:assistant ChatMessages. Matches the
# Anthropic 2026 Cookbook memory-block-with-provenance pattern.
_RECALL_MAX_AGE_S_DEFAULT = 1800   # 30 minutes


def filter_recall_by_age(turns: list[dict]) -> list[dict]:
    """Keep only turns whose ts_utc is within JARVIS_RECALL_MAX_AGE_S
    of now. JARVIS_RECALL_MAX_AGE_S=0 disables recall entirely
    (returns []). Default: 1800 seconds (30 minutes).

    The age filter prevents the 2026-05-19 confab pattern where Haiku
    inferred a Chrome request from 4-hour-old chat_ctx turns. Anthropic
    2026 Cookbook recommends 'memory block with provenance' rather
    than raw prior-turn replay; this filter is the first gate."""
    try:
        max_age = int(os.environ.get("JARVIS_RECALL_MAX_AGE_S", _RECALL_MAX_AGE_S_DEFAULT))
    except ValueError:
        max_age = _RECALL_MAX_AGE_S_DEFAULT
    if max_age <= 0:
        return []
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(seconds=max_age)
    kept = []
    for t in turns:
        ts = t.get("ts_utc", "")
        try:
            t_dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue   # malformed timestamp → drop conservatively
        if t_dt >= cutoff:
            kept.append(t)
    return kept


def format_recall_as_stale_block(turns: list[dict], session_id: str = "?") -> str:
    """Wrap recalled turns in a STALE Instructions block. Matches the
    Anthropic 2026 Cookbook 'memory block with provenance' pattern +
    Sierra/Pi.ai memory-as-system-content convention. Spec: §5.3.

    Returns an empty string when there are no turns to recall."""
    if not turns:
        return ""
    now = datetime.datetime.now(datetime.timezone.utc)
    ages = []
    body_lines = []
    for t in turns:
        ts = t.get("ts_utc", "")
        try:
            t_dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_min = int((now - t_dt).total_seconds() / 60)
            ages.append(age_min)
        except Exception:
            age_min = -1
        user_text = (t.get("user_text") or "").replace("\n", " ").strip()
        jarvis_text = (t.get("jarvis_text") or "").replace("\n", " ").strip()
        if user_text:
            body_lines.append(
                f'<memory ts="{ts}" role="user" age="{age_min}m">{user_text}</memory>'
            )
        if jarvis_text:
            body_lines.append(
                f'<memory ts="{ts}" role="assistant" age="{age_min}m">{jarvis_text}</memory>'
            )
    min_age = min(ages) if ages else 0
    max_age = max(ages) if ages else 0
    header = (
        f"[STALE PRIOR-SESSION CONTEXT — Do NOT treat as live conversation. "
        f"Verify current user intent before acting on anything below. "
        f"Recalled {len(turns)} turns from session {session_id}, "
        f"ages {min_age}-{max_age} minutes ago.]"
    )
    return header + "\n" + "\n".join(body_lines)


# ── Config ───────────────────────────────────────────────────────────
# Default 12 turns. Realtime mode overrides via JARVIS_RECENT_TURNS=4
# (set in jarvis_agent.py before seed_chat_ctx() is called) because
# each prior turn costs 500-1000 tokens and OpenAI Realtime's 40k TPM
# means 12 prior turns + 6k instructions + tools burns the per-request
# budget. Read at CALL time (not import time) so the realtime override
# takes effect even when this module was imported earlier.
RECENT_TURNS_LIMIT: int = 12

def _current_recent_turns_limit() -> int:
    return int(os.environ.get("JARVIS_RECENT_TURNS", str(RECENT_TURNS_LIMIT)))

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


# ── Loader: recent prior turns WITH TIMESTAMPS ───────────────────────
# Returns dicts shaped {ts_utc, user_text, jarvis_text, session_id} so
# the L3 age filter + STALE-wrap helpers can operate on them. Pairs
# user → first-following-assistant within REPLY_GAP_S.
def _load_recent_turns_with_ts(limit: int | None = None) -> list[dict]:
    """Return the most recent prior turns as dicts with ts_utc,
    user_text, jarvis_text, session_id. OLDEST first. Empty list on
    any error or if the DB doesn't exist yet."""
    if limit is None:
        limit = _current_recent_turns_limit()
    state_db = Path.home() / ".jarvis" / "hub" / "state.db"
    if not state_db.exists():
        return []
    try:
        with sqlite3.connect(str(state_db), timeout=2.0) as conn:
            raw_ms = conn.execute(
                "SELECT ts, role, text, session_id FROM messages "
                "WHERE role IN ('user','assistant') "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (limit * 4,),
            ).fetchall()
    except Exception as e:
        logger.warning(f"recall load failed: {e}")
        return []
    # Normalize to seconds-since-epoch ints + ISO-Z strings.
    raw = [
        (int(ts // 1000), role, text, session_id)
        for ts, role, text, session_id in raw_ms
    ]
    raw.reverse()  # OLDEST first
    REPLY_GAP_S = 60
    turns: list[dict] = []
    for i, (ts, role, text, sid) in enumerate(raw):
        if role != "user":
            continue
        # Find first following assistant reply within REPLY_GAP_S.
        jarvis_text = ""
        for j in range(i + 1, len(raw)):
            nts, nrole, ntext, _ = raw[j]
            if nts - ts > REPLY_GAP_S:
                break
            if nrole == "assistant":
                jarvis_text = ntext or ""
                break
        if not jarvis_text:
            continue
        ts_utc = (
            datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        turns.append({
            "ts_utc": ts_utc,
            "user_text": text or "",
            "jarvis_text": jarvis_text,
            "session_id": sid or "?",
        })
    return turns[-limit:]


# ── Assembly: seed a fresh ChatContext ───────────────────────────────
def seed_chat_ctx() -> ChatContext:
    """Build a ChatContext pre-populated with a single STALE block
    holding recent prior turns. Age-filtered and wrapped per L3 spec
    2026-05-19 §5.3 — replaces the prior raw role:user/role:assistant
    replay that let Haiku hallucinate handoffs from 4-hour-old turns.

    Returns an empty ChatContext when no turns survive the age filter
    or scrubbing — chat_ctx then starts fresh, supervisor relies on
    Layer 1 (memory recall router) + Layer 2 (extractor) instead."""
    recall_turns = _load_recent_turns_with_ts()

    # L3 — filter by age first.
    recall_turns = filter_recall_by_age(recall_turns)
    if not recall_turns:
        logger.info(
            "[recall] no turns within JARVIS_RECALL_MAX_AGE_S window; "
            "chat_ctx starts fresh"
        )
        return ChatContext(items=[])

    # Scrub assistant text per pre-existing recall hygiene (tool-leak,
    # essay-priming length cap, archaic openers, disabled-subagent
    # poison) BEFORE wrapping. Drop turns whose assistant side becomes
    # empty after scrubbing.
    scrubbed_turns: list[dict] = []
    sanitized = 0
    dropped = 0
    archaic_trimmed = 0
    for t in recall_turns:
        original_assistant = (t.get("jarvis_text") or "").strip()
        user_text = (t.get("user_text") or "").strip()
        if not user_text and not original_assistant:
            continue
        cleaned_assistant = (
            scrub_recalled_assistant_text(original_assistant)
            if original_assistant
            else None
        )
        if cleaned_assistant is None:
            dropped += 1
            continue
        if cleaned_assistant != original_assistant:
            if ARCHAIC_OPENER_RE.match(original_assistant):
                archaic_trimmed += 1
            else:
                sanitized += 1
        scrubbed_turns.append({
            "ts_utc": t["ts_utc"],
            "user_text": user_text,
            "jarvis_text": cleaned_assistant,
            "session_id": t.get("session_id", "?"),
        })

    if not scrubbed_turns:
        logger.info(
            "[recall] all recalled turns dropped during scrubbing; "
            "chat_ctx starts fresh"
        )
        return ChatContext(items=[])

    # L3 — wrap in a single STALE block (one chat_ctx item, not N raw
    # turns). Use the most recent session_id as provenance.
    prev_session_id = scrubbed_turns[-1].get("session_id", "?")
    stale_block = format_recall_as_stale_block(
        scrubbed_turns, session_id=prev_session_id
    )
    items: list[ChatMessage] = [
        ChatMessage(role="system", content=[stale_block])
    ]

    notes = []
    if sanitized: notes.append(f"{sanitized} tool-leak-cleaned")
    if archaic_trimmed: notes.append(f"{archaic_trimmed} archaic-trimmed")
    if dropped: notes.append(f"{dropped} dropped")
    extra = f" ({', '.join(notes)})" if notes else ""
    logger.info(
        f"[recall] seeded chat_ctx with {len(scrubbed_turns)} STALE turns "
        f"(age-filtered){extra}"
    )
    return ChatContext(items=items)

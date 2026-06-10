"""Chat-context regex helpers — tool-leak / register / silence filters.

Three regex pattern definitions consumed across the live TTS chain
(jarvis_agent.py post-LLM scrubber) and the pycall sanitizer:

  * ``TOOL_LEAK_RE`` + ``sanitize_leaked_tool_text`` — strip text that
    looks like a leaked structured tool call (W-015/W-016 shapes:
    ``<function ...>``, ``<arguments>``, ``<tool_call>``, JSON arrays,
    ``task_done(...)``, prompt-label preambles).
  * ``META_SILENCE_RE`` — match whole-reply meta-silence acks
    ("(silent)", "Listening.", "Standing by, sir.").
  * ``ARCHAIC_OPENER_RE`` — match leading archaic / British-butler
    openers ("Quite.", "Indeed.", "Very well, sir.") so they can be
    trimmed from spoken replies.

Industry-standard recall-hygiene pattern: the same filters that gate
TTS output also feed the on-write filters in ``sanitizers/pycall.py``
so leaked shapes never persist to chat history in the first place.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("jarvis.chat_ctx")


# ── Live conversation accessor ───────────────────────────────────────
def session_chat_messages(session) -> list:
    """Return the live conversation's ChatMessage list, or ``[]``.

    The chat context lives on the **Agent** (``session.current_agent.chat_ctx``),
    NOT on ``AgentSession`` — ``session.chat_ctx`` raises ``AttributeError``
    (livekit-agents 1.5 migration; the same trap the 2026-05-27 pre-TTS-gate
    fix called out, see jarvis_agent.py). Two further 1.5 changes this guards:
      * ``ChatContext.messages`` is now a *method*, not a property — calling it
        returns the list; accessing it as an attribute yields a bound method
        (which ``reversed()``/slicing then choke on).
      * ``current_agent.chat_ctx`` is a ``_ReadOnlyChatContext``: its item
        *list* is immutable (append/del/setitem raise ``RuntimeError``), but
        mutating a returned message's ``.content`` attribute DOES persist to
        the live context (the message objects are shared). So callers may
        prefix-inject in place on the returned messages; they must NOT
        append/delete list items (use ``session.history.truncate(...)`` for
        that — ``session.history`` is the session's own mutable ctx).

    Returns ``[]`` on any access failure so callers degrade to "no history"
    instead of silently swallowing an ``AttributeError`` they never expected.
    """
    try:
        agent = getattr(session, "current_agent", None)
        if agent is None:
            return []
        ctx = agent.chat_ctx  # property; raises RuntimeError if no agent bound
        msgs = getattr(ctx, "messages", None)
        if callable(msgs):
            return msgs() or []
        return msgs or []
    except (AttributeError, RuntimeError):
        return []
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("[chat_ctx] session_chat_messages failed: %s", e)
        return []


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

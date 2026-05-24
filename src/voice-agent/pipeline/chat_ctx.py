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

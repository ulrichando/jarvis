"""Deterministic intent router — fires BEFORE the supervisor LLM.

Built 2026-05-11 evening in response to recurring orchestration
failures: the supervisor LLM (Haiku 4.5 originally, now Sonnet 4.6)
sometimes mis-routes obvious voice commands ("share my screen"
→ pre-announces "Let me share your screen" then forgets to call the
tool; "what's on my screen" → calls screenshot() instead of
transfer_to_screen_share). Soft prompt rules don't fix this
reliably; even smarter LLMs respect prose rules probabilistically.

This module is the structural fix: high-confidence voice commands
match a regex, fire the tool sequence DETERMINISTICALLY, and either
short-circuit the LLM (when the action is the entire response) or
augment its context so its decision is pre-determined (when the LLM
still needs to produce a verbal reply).

Design constraints:

  - Tight regex patterns only. False positives are worse than misses;
    a missed intent falls through to the LLM (still works), a false-
    positive intent hijacks an unrelated turn (broken UX). Anchor with
    `^…$` after stripping; require imperative form for action
    intents.

  - The router runs in JarvisAgent.on_user_turn_completed AFTER the
    silent-mode / quiet-hours / short-input gates and BEFORE memory
    extraction kicks off. Failures from any executor must NOT block
    the user's turn — exceptions get caught, the intent is logged,
    and the turn falls through to the LLM as if no match.

  - Replies stay SHORT and avoid the butler register the user has
    been steadily editing out. "Sharing now." not "Right away, sir,
    sharing your screen."

  - SCREEN_SHARE_START and SCREEN_SHARE_STOP short-circuit with a
    templated reply (no LLM call). SCREEN_SHARE_QUERY (what's on my
    screen?) fires the toggle but lets the LLM proceed — the verbal
    answer comes from the Live subagent after the transfer.

Adding intents: add a regex + executor to `_INTENTS`. Each entry
returns an `IntentMatch` that says (a) what action to fire and
(b) whether to short-circuit. Keep the bar high — every entry is a
deterministic carve-out of the supervisor's decision surface.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional


logger = logging.getLogger("jarvis.intent_router")


@dataclass(frozen=True)
class IntentMatch:
    """Result of intent_router.match() — what to do for this turn.

    Fields:
        name: identifier for telemetry / logs.
        executor: async callable that performs the side effect (e.g.
                  toggling screen-share). Run BEFORE the templated
                  reply is voiced and BEFORE control falls through to
                  the LLM (when short_circuit=False). Exceptions are
                  caught by the caller; never crash the user turn.
        reply: templated voiced reply for short_circuit=True intents.
               Ignored when short_circuit=False (the LLM produces the
               reply itself). Keep <30 chars — these are JARVIS
               persona shapes, not full sentences.
        short_circuit: when True, the caller (on_user_turn_completed)
                       voices `reply` via session.say() and raises
                       StopResponse to skip the LLM entirely. When
                       False, the executor runs as a side-effect and
                       the turn falls through to the LLM normally.
    """
    name: str
    executor: Callable[[], Awaitable[None]]
    reply: str
    short_circuit: bool


# Lazy import inside match() so the router module stays cheap to
# import (avoiding aiohttp pull at boot for callers that only need
# the regex constants). The function is defined at module top so
# it's available to test code and runtime callers alike.
async def _ensure_screen_share_on() -> None:
    """Idempotent: turn the screen-share track on. Logs the result."""
    from tools.screen_share_control import toggle_screen_share
    sharing, message = await toggle_screen_share(start=True)
    logger.info(f"[intent] screen-share start → sharing={sharing} ({message})")


async def _ensure_screen_share_off() -> None:
    """Idempotent: turn the screen-share track off. Logs the result."""
    from tools.screen_share_control import toggle_screen_share
    sharing, message = await toggle_screen_share(start=False)
    logger.info(f"[intent] screen-share stop → sharing={sharing} ({message})")


# ── Regex patterns ─────────────────────────────────────────────────
#
# Each pattern is anchored with `^…$` after the input is lowercased
# and stripped of leading/trailing whitespace + filler punctuation by
# the caller (jarvis_agent.on_user_turn_completed already does
# `text = text.lower().strip()`).
#
# Optional preambles ("jarvis,", "please", "can you", "could you")
# are absorbed by `_PREAMBLE` so the core pattern stays readable.

_PREAMBLE = (
    r"(?:"
    r"(?:hey\s+|yo\s+|okay\s+|ok\s+|i\s+said\s+)?jarvis[,.\s]+|"
    r"(?:please|can\s+you|could\s+you|would\s+you|i\s+need\s+you\s+to)\s+"
    r")?"
)
_TAIL = r"[,.!?\s]*$"


# Screen-share start. Matches imperative phrasing only — "share my
# screen" yes, "I want to share something" no.
_SCREEN_SHARE_START_RE = re.compile(
    r"^" + _PREAMBLE + (
        r"(?:"
        r"share\s+(?:my\s+)?screen|"
        r"start\s+(?:the\s+)?screen[-\s]?(?:share|sharing)|"
        r"start\s+sharing\s+(?:my\s+)?screen|"
        r"turn\s+on\s+screen[-\s]?(?:share|sharing)|"
        r"begin\s+(?:the\s+)?screen[-\s]?(?:share|sharing)"
        r")"
    ) + _TAIL,
    re.IGNORECASE,
)

# Screen-share stop. Requires explicit "stop" / "end" / "turn off" /
# "quit" verb — narrower than "share" because false-positive cost is
# the user losing their share unexpectedly.
_SCREEN_SHARE_STOP_RE = re.compile(
    r"^" + _PREAMBLE + (
        r"(?:"
        r"stop\s+(?:the\s+)?screen[-\s]?(?:share|sharing)|"
        r"stop\s+sharing\s+(?:my\s+)?screen|"
        r"stop\s+sharing|"
        r"end\s+(?:the\s+)?screen[-\s]?(?:share|sharing)|"
        r"turn\s+off\s+(?:the\s+)?screen[-\s]?(?:share|sharing)|"
        r"quit\s+(?:the\s+)?screen[-\s]?(?:share|sharing)"
        r")"
    ) + _TAIL,
    re.IGNORECASE,
)

# Screen-share query — "what's on my screen?" type questions. Fires
# the toggle as a side-effect so the Live subagent can land with
# frames flowing, then PASSES THROUGH to the LLM so the supervisor
# routes to transfer_to_screen_share normally. Without this side-
# effect, the LLM might call transfer_to_screen_share before the
# track is up (pre_transfer hook covers this too — defense in depth).
_SCREEN_SHARE_QUERY_RE = re.compile(
    r"^" + _PREAMBLE + (
        r"(?:"
        r"what(?:'?s|\s+is)\s+(?:on|in)\s+(?:my\s+)?screen|"
        r"what\s+do\s+you\s+see(?:\s+on\s+(?:my\s+)?screen)?|"
        r"what\s+can\s+you\s+see(?:\s+on\s+(?:my\s+)?screen)?|"
        r"describe\s+(?:my\s+)?screen|"
        r"can\s+you\s+see\s+(?:my\s+)?screen|"
        r"look\s+at\s+(?:my\s+)?screen|"
        r"tell\s+me\s+what(?:'?s|\s+is)\s+on\s+(?:my\s+)?screen"
        r")"
    ) + _TAIL,
    re.IGNORECASE,
)


def match(text: str) -> Optional[IntentMatch]:
    """Match `text` against the intent registry.

    Returns the first matching `IntentMatch`, or None if no high-
    confidence pattern matches. Matching is order-independent in
    practice — patterns are designed to be mutually exclusive.

    Caller contract:
      1. Lowercase + strip `text` before passing in (matches what the
         on_user_turn_completed gates already do).
      2. If a match is returned, await the executor (catching all
         exceptions — don't crash the turn).
      3. If `short_circuit=True`, voice `reply` via session.say() and
         raise StopResponse to skip the LLM.
      4. If `short_circuit=False`, fall through to the LLM as normal.
    """
    if not text:
        return None
    t = text.strip()

    if _SCREEN_SHARE_START_RE.match(t):
        return IntentMatch(
            name="screen_share.start",
            executor=_ensure_screen_share_on,
            reply="Sharing now.",
            short_circuit=True,
        )

    if _SCREEN_SHARE_STOP_RE.match(t):
        return IntentMatch(
            name="screen_share.stop",
            executor=_ensure_screen_share_off,
            reply="Stopped.",
            short_circuit=True,
        )

    if _SCREEN_SHARE_QUERY_RE.match(t):
        return IntentMatch(
            name="screen_share.query",
            # Side-effect only — turn share on, then let the supervisor
            # LLM see the user message and call transfer_to_screen_share
            # itself. The reply is produced by the Live subagent.
            executor=_ensure_screen_share_on,
            reply="",
            short_circuit=False,
        )

    return None

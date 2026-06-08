"""Suppress anticipatory text content from supervisor turns that include
a `transfer_to_*` (or `delegate`) tool call.

═══ The bug this fixes ═══

Captured live 2026-05-02 21:44 — user said:
    "Open a new tab on the current browser and navigate to YouTube."

The supervisor LLM (Groq llama-3.3-70b) emitted in a SINGLE response:
  • text content:  "A new tab is open on your current browser, and
                    YouTube is loaded, sir."
  • tool call:     transfer_to_browser(request="Open a new tab and
                                       navigate to YouTube.")

The framework streamed the text to TTS while ALSO firing the tool call.
The user heard a fake confirmation BEFORE the browser subagent ran.
The subagent then ran for real and said "New tab opened and navigated
to YouTube." — leading the user to perceive "first attempt failed,
second attempt worked." (`confab_detector` blocked the DB save so the
chat_ctx wasn't poisoned, but TTS had already played.)

Past failures of the prompt-level fix: the supervisor's instructions
already say "emit only the tool call, zero free-form text" but the LLM
ignores it ~30% of the time. We need a structural guard.

═══ The fix ═══

Patch `inference.llm.LLMStream._parse_choice`:
  1. Per-stream state keyed on the response id.
  2. When a delta carries a `tool_calls` whose function.name starts
     with `transfer_to_` or equals `delegate`, mark the stream as
     "handoff in progress."
  3. From that chunk forward, blank `delta.content` so the orig parser
     returns None (livekit short-circuits on empty content) — text
     stops streaming to TTS.
  4. The tool call itself flows through normally; the framework
     voices the spec's `ack_phrase` after dispatch — that's the only
     supervisor-side voice the user should hear.
  5. Subagent's `task_done(summary)` voices the actual outcome
     after the work completes.

Trade-off accepted: text emitted BEFORE the transfer_to_* delta arrives
isn't caught (we haven't seen the tool call yet). Empirically Groq
emits the tool_call delta within the first 2-3 chunks of the response
so this is a small leak window — typically just a "Of course, sir."
ack-style fragment that does no harm.

Idempotent. Stacks cleanly with the existing dsml/pycall/tool_name
sanitizers.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger("jarvis.handoff_text_suppressor")

# Per-stream state. Keys are LLM response ids. Values are flags
# "is the supervisor handing off in this stream?"
_HANDOFF_STATE: dict[str, bool] = {}

# Ids whose chat_ctx cross-stream walk has already run this stream. The
# walk result can't change mid-stream (chat_ctx is fixed during a single
# response), so we record "already walked" to avoid re-walking the full
# (≤80-item) ctx on every streamed chunk — the negative case (no pending
# handoff, the common case now handoffs are gone) was re-walking per chunk.
_CROSS_STREAM_CHECKED: set[str] = set()

# Anything matching this is a supervisor → subagent handoff. The
# `delegate` form is the single sub-agent dispatcher; transfer_to_X
# covers all HandoffSubagents (browser, desktop, planner, …).
_HANDOFF_RE = re.compile(r"^(?:transfer_to_[a-z][a-z0-9_]*|delegate)$")


def _chat_ctx_has_pending_handoff(stream: Any) -> bool:
    """Cross-stream guard. Walk the recent chat_ctx tail to decide
    whether a transfer_to_*/delegate was emitted earlier in this turn
    WITHOUT a corresponding task_done since — i.e. a subagent is
    currently running and any text content from a NEW supervisor
    stream (e.g. FallbackAdapter retried with DeepSeek) is
    necessarily anticipatory/hallucinated.

    Live-observed 2026-05-04 13:11: stream A emitted transfer_to_browser
    + handoff fired; stream B (DeepSeek fallback in same turn) emitted
    "Done, sir. New tab's open and headed to YouTube." as plain text
    with NO tool_call. The per-stream state in _HANDOFF_STATE didn't
    catch B because B had no tool_call. The chat_ctx tail at the
    moment B ran looked like:

        user: "open a tab..."
        assistant: tool_calls=[transfer_to_browser]      ← pending
        tool:      "At once, sir." (ack_phrase)
        (no task_done yet)

    This walk-backwards check catches that exact shape: the most
    recent FunctionCall in chat_ctx is transfer_to_*/delegate AND
    there's no task_done newer than it.

    Returns True when supervisor text content should be suppressed.
    """
    try:
        items = getattr(stream._chat_ctx, "items", None)
    except Exception:
        return False
    if not items:
        return False

    # 2026-05-06 — bug fix: was `tail = items[-15:]` which silently
    # dropped task_done out of the window once the conversation had
    # more than ~15 chat_ctx items (busy session: user msg + handoff
    # + ext_* calls + text replies + new user msg). With task_done
    # out of view, last_done_idx stayed at -1 while the handoff
    # lived in-window, walk-back returned True, supervisor text got
    # suppressed indefinitely → JARVIS went silent (live: 23:44 EDT).
    #
    # Fix: walk the full chat_ctx. Cost is O(n) per stream, n is
    # bounded by the ChatCtx trim cap (CTX_MAX_TURNS=80 ≈ 200 items).
    # That's a few microseconds per stream — well below the 10ms
    # budget for chunk processing.
    last_handoff_idx = -1
    last_done_idx = -1
    for i, it in enumerate(items):
        name = getattr(it, "name", None)
        if not name:
            continue
        if name == "task_done":
            last_done_idx = i
        elif _HANDOFF_RE.match(name):
            last_handoff_idx = i

    # Pending iff the most recent handoff is newer than the most
    # recent task_done.
    return last_handoff_idx > last_done_idx


def _delta_has_handoff(delta: Any) -> bool:
    """True if this delta carries a tool_call whose function.name is a
    handoff (transfer_to_* / delegate). Robust to both Pydantic and
    OpenAI-SDK shapes."""
    tool_calls = getattr(delta, "tool_calls", None)
    if not tool_calls:
        return False
    for tc in tool_calls:
        # OpenAI SDK shape: tc.function.name
        fn = getattr(tc, "function", None)
        name = getattr(fn, "name", None) if fn is not None else None
        if not name:
            # Some plugins put name directly on the tool call
            name = getattr(tc, "name", None)
        if name and _HANDOFF_RE.match(name):
            return True
    return False


def _try_blank_content(delta: Any) -> bool:
    """Set delta.content = "" if writable. Returns True on success."""
    try:
        delta.content = ""
        return True
    except Exception:
        try:
            object.__setattr__(delta, "content", "")
            return True
        except Exception:
            return False


def install() -> None:
    """Patch LLMStream._parse_choice to drop free-form text when the
    same stream contains a handoff tool call. Idempotent."""
    from livekit.agents.inference import llm as inf_llm

    if getattr(inf_llm.LLMStream, "_jarvis_handoff_suppressor_patched", False):
        return

    orig_parse = inf_llm.LLMStream._parse_choice

    def patched(self, id, choice, thinking):
        delta = getattr(choice, "delta", None)
        finish = getattr(choice, "finish_reason", None)

        if delta is not None:
            # 1. Per-stream check: this delta carries a handoff tool_call.
            if _delta_has_handoff(delta):
                if not _HANDOFF_STATE.get(id):
                    logger.warning(
                        "[handoff-suppressor] suppressing text content for"
                        " stream %s — transfer_to_*/delegate detected",
                        id[:12] if id else "?",
                    )
                _HANDOFF_STATE[id] = True

            # 2. Cross-stream check: chat_ctx tail shows a pending
            #    handoff with no task_done yet. Catches the FallbackAdapter
            #    case where stream B (DeepSeek) runs while subagent
            #    spawned by stream A is still working. Walked at most once
            #    per stream (chat_ctx is fixed during a response), so the
            #    common no-handoff case doesn't re-scan the ctx per chunk.
            if (
                os.environ.get("JARVIS_HANDOFF_CROSS_STREAM_GUARD", "1") == "1"
                and not _HANDOFF_STATE.get(id)
                and id not in _CROSS_STREAM_CHECKED
            ):
                _CROSS_STREAM_CHECKED.add(id)
                if _chat_ctx_has_pending_handoff(self):
                    logger.warning(
                        "[handoff-suppressor] suppressing supervisor text on"
                        " stream %s — chat_ctx shows pending handoff (subagent"
                        " still running)",
                        id[:12] if id else "?",
                    )
                    _HANDOFF_STATE[id] = True

            # Blank content during a handoff stream. The tool_call
            # field itself is preserved untouched.
            if _HANDOFF_STATE.get(id) and getattr(delta, "content", None):
                _try_blank_content(delta)

        # On stream end, drop the per-id state so we don't leak memory
        # across responses with reused ids (rare, but seen on retries).
        if finish:
            _HANDOFF_STATE.pop(id, None)
            _CROSS_STREAM_CHECKED.discard(id)

        return orig_parse(self, id, choice, thinking)

    inf_llm.LLMStream._parse_choice = patched
    inf_llm.LLMStream._jarvis_handoff_suppressor_patched = True
    logger.info(
        "handoff-text-suppressor installed (drops anticipatory text on"
        " transfer_to_*/delegate)"
    )

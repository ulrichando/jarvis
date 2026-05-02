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
The user heard a fake confirmation BEFORE the browser specialist ran.
The specialist then ran for real and said "New tab opened and navigated
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
  5. Specialist's `task_done(summary)` voices the actual outcome
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
import re
from typing import Any

logger = logging.getLogger("jarvis.handoff_text_suppressor")

# Per-stream state. Keys are LLM response ids. Values are flags
# "is the supervisor handing off in this stream?"
_HANDOFF_STATE: dict[str, bool] = {}

# Anything matching this is a supervisor → specialist handoff. The
# `delegate` form is the single sub-agent dispatcher; transfer_to_X
# covers all SpecialistSpecs (browser, desktop, planner, …).
_HANDOFF_RE = re.compile(r"^(?:transfer_to_[a-z][a-z0-9_]*|delegate)$")


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
            # Newly-detected handoff in this delta? Mark the stream.
            if _delta_has_handoff(delta):
                if not _HANDOFF_STATE.get(id):
                    logger.warning(
                        "[handoff-suppressor] suppressing text content for"
                        " stream %s — transfer_to_*/delegate detected",
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

        return orig_parse(self, id, choice, thinking)

    inf_llm.LLMStream._parse_choice = patched
    inf_llm.LLMStream._jarvis_handoff_suppressor_patched = True
    logger.info(
        "handoff-text-suppressor installed (drops anticipatory text on"
        " transfer_to_*/delegate)"
    )

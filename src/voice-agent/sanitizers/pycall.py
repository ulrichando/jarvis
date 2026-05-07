"""Suppress tool-call-as-Python-text leaks.

Captured live 2026-05-02 12:26: Groq llama-3.3-70b emitted an entire
tool sequence as plain content text:

    browser_task_v2("go to weather.com and report the current weather for Cleveland, Ohio")  task_done(summary)

Instead of using the structured `tool_calls` field, the model dumped
the call as Python source. The TTS voiced the function-call syntax
verbatim and the user heard "browser task v two left paren quote
go to weather dot com..." which is unintelligible.

Distinct from:
  - tool_name_sanitizer.py — recovers from Groq's `tool call validation
    failed` API error (the cramming-into-name shape)
  - dsml_sanitizer.py — recovers from DeepSeek's `<｜｜DSML｜｜...>`
    envelope leakage

This module patches `_parse_choice` to detect when a stream BEGINS
with `<tool_name>(...)` where `<tool_name>` is in the LLMStream's
known tool list, then suppresses the rest of that envelope.

Idempotent. install() can be called multiple times safely. Stacks
cleanly on top of the existing dsml + tool_name + deepseek-roundtrip
patches.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("jarvis.pycall_sanitizer")


# Match `<identifier>(`, capturing the identifier. Used to detect
# tool-call-as-text leaks at the start of a stream.
_PYCALL_OPEN_RE = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")


# Per-stream state. Keyed by response.id (passed to _parse_choice).
# Cleared when the envelope balances or the stream ends.
_PYCALL_STATE: dict[str, dict[str, Any]] = {}


def _try_set_content(delta: Any, value: str) -> None:
    """Best-effort mutate delta.content. Mirrors dsml_sanitizer."""
    try:
        delta.content = value
    except Exception:
        try:
            object.__setattr__(delta, "content", value)
        except Exception:
            logger.debug("[pycall] could not mutate delta.content; envelope may leak")


def install() -> None:
    """Patch LLMStream._parse_choice to suppress Python-syntax tool-call
    leaks. Idempotent. Stacks safely with the other parse_choice
    patches (dsml_sanitizer + deepseek_roundtrip)."""
    from livekit.agents.inference import llm as inf_llm

    if getattr(inf_llm.LLMStream, "_jarvis_pycall_patched", False):
        return

    orig_parse = inf_llm.LLMStream._parse_choice

    def patched(self, id, choice, thinking):
        delta = getattr(choice, "delta", None)
        if delta is not None:
            content = getattr(delta, "content", None) or ""
            state = _PYCALL_STATE.get(id)

            if state is None and content:
                # First chunk for this stream — peek for the leak.
                m = _PYCALL_OPEN_RE.match(content)
                if m:
                    name = m.group(1)
                    # Only trigger on KNOWN tools — the regex is too
                    # broad otherwise (any "word(" matches, including
                    # legitimate prose). Guard with the live tool map.
                    try:
                        known = set(self._tool_ctx.function_tools.keys())
                    except Exception:
                        known = set()
                    if name in known:
                        # Found a tool-call-as-text leak. Suppress
                        # everything from this chunk onward.
                        _PYCALL_STATE[id] = {
                            "buffer": content, "depth": 0,
                            "tool_name": name,
                        }
                        # Update depth from this chunk's parens.
                        s = _PYCALL_STATE[id]
                        s["depth"] = content.count("(") - content.count(")")
                        logger.warning(
                            "[pycall] tool-call-as-text leak detected: "
                            "name=%r prefix=%r — suppressing",
                            name, content[:80],
                        )
                        _try_set_content(delta, "")
                        # Fall through to the close-detection block
                        # below — if this chunk also closes the
                        # envelope (balanced parens already), the
                        # state cleanup runs on this same call.
            elif state is not None and content:
                # Inside the envelope — keep accumulating + suppressing.
                state["buffer"] += content
                state["depth"] += content.count("(") - content.count(")")
                _try_set_content(delta, "")

            # End-of-envelope detection: when paren depth returns to 0
            # AND we've consumed at least one char beyond the opener.
            state = _PYCALL_STATE.get(id)
            if state is not None:
                if state["depth"] <= 0 and len(state["buffer"]) > len(state["tool_name"]) + 1:
                    logger.info(
                        "[pycall] envelope closed (len=%d); state cleared",
                        len(state["buffer"]),
                    )
                    del _PYCALL_STATE[id]
                elif len(state["buffer"]) > 8000:
                    # Safety bailout — never accumulate forever.
                    logger.warning("[pycall] buffer overflow without close — discarding")
                    del _PYCALL_STATE[id]

        return orig_parse(self, id, choice, thinking)

    inf_llm.LLMStream._parse_choice = patched
    inf_llm.LLMStream._jarvis_pycall_patched = True
    logger.warning("Pycall sanitizer installed (suppresses tool-call-as-text leaks)")

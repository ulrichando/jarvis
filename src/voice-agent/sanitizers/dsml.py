"""Suppress + recover DeepSeek's DSML tool-call envelope when it leaks
as plain text content.

DeepSeek's reasoning models occasionally emit tool calls in their
native DSML envelope syntax instead of the OpenAI-compatible
`tool_calls` field. The envelope shape:

    <｜｜DSML｜｜tool_calls>
    <｜｜DSML｜｜invoke name="web_fetch">
    <｜｜DSML｜｜parameter name="url" string="true">https://…</｜｜DSML｜｜parameter>
    </｜｜DSML｜｜invoke>
    </｜｜DSML｜｜tool_calls>

(The `｜` characters are U+FF5C — fullwidth vertical line — part of
DeepSeek's tokenizer reserved-token set.)

When this happens, livekit-agents doesn't recognize it as a tool call
and streams the entire envelope to the TTS as plain content. The user
hears the URL + markup read aloud — captured live 2026-05-01 17:38
("on Google search for the weather in Columbus" → JARVIS reading the
open-meteo URL out loud, characters and all).

This module patches `inference.llm.LLMStream._parse_choice` to:
  1. Detect the DSML opener in `delta.content`
  2. Suppress the envelope text from streaming to TTS (mutate
     delta.content)
  3. Buffer the envelope per-stream until the closer arrives
  4. Parse the envelope, look up the tool in `self._tool_ctx`,
     execute it inline (same scaffolding as tool_name_sanitizer's
     Groq path)
  5. Emit a fresh `ChatChunk` with the tool's result so the
     framework voices the answer, not the markup

Idempotent. install() can be called multiple times safely. Stacks
cleanly with the existing deepseek_roundtrip + tool_name_sanitizer
patches.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import uuid
from typing import Any

logger = logging.getLogger("jarvis.dsml_sanitizer")

# DSML envelope markers. The `｜` chars are U+FF5C — DeepSeek's reserved
# tokenizer character, virtually never in regular text. We use a single
# `｜` as the START TRIGGER because the LLM streams the opener as 5-7
# tokens (`<`, `｜`, `｜`, `DSML`, `｜`, `｜`, `tool_calls`, `>`). The
# previous detector required the full 22-char opener in one chunk and
# silently failed on any streamed envelope (captured live 2026-05-02
# 08:32 — "At once, sir. <｜｜DSML｜｜tool_calls>..." leaked to TTS).
_DSML_TRIGGER_CHAR = "｜"             # ｜  (single fullwidth pipe)
_DSML_OPEN = "<｜｜DSML｜｜tool_calls>"
_DSML_CLOSE = "</｜｜DSML｜｜tool_calls>"

_DSML_INVOKE_RE = re.compile(
    r"<｜｜DSML｜｜invoke name=\"([^\"]+)\">(.*?)</｜｜DSML｜｜invoke>",
    re.DOTALL,
)
_DSML_PARAM_RE = re.compile(
    r"<｜｜DSML｜｜parameter name=\"([^\"]+)\"[^>]*>(.*?)</｜｜DSML｜｜parameter>",
    re.DOTALL,
)


# response.id -> {"buffer": str}. Per-stream so concurrent streams
# don't cross-contaminate. Cleared when the envelope closes or the
# stream finishes.
_DSML_STATE: dict[str, dict[str, Any]] = {}


def _try_set_content(delta: Any, value: str) -> None:
    """Best-effort mutate `delta.content`. Pydantic models in newer
    versions of openai/livekit-agents are frozen — bypass with
    object.__setattr__ if normal setattr fails."""
    try:
        delta.content = value
    except Exception:
        try:
            object.__setattr__(delta, "content", value)
        except Exception:
            # If we can't mutate, give up gracefully — the markup will
            # leak to TTS, but at least we won't crash the stream.
            logger.debug("[dsml] could not mutate delta.content; envelope will leak")


def _parse_envelope(envelope: str) -> list[tuple[str, dict[str, str]]]:
    """Pull (name, args_dict) out of a complete DSML envelope. Multiple
    invokes possible; we return them all but typically only fire the
    first one (matches OpenAI tool-call streaming convention)."""
    out = []
    for m in _DSML_INVOKE_RE.finditer(envelope):
        name = m.group(1)
        body = m.group(2)
        args: dict[str, str] = {}
        for pm in _DSML_PARAM_RE.finditer(body):
            args[pm.group(1)] = pm.group(2).strip()
        out.append((name, args))
    return out


def _tool_takes_context(tool) -> bool:
    """True if the tool's signature requires a `context` / `RunContext`
    argument we can't construct from outside the framework's tool
    dispatch. Mirrors tool_name_sanitizer's guard. Tools that need
    RunContext (like transfer_to_X built via build_transfer_tool)
    can't be inline-executed by us — fall back to silent suppression
    so we don't voice the framework's TypeError to the user."""
    func = getattr(tool, "_func", tool)
    try:
        sig = inspect.signature(func)
    except Exception:
        return True   # conservative — bail rather than guess
    for name, param in sig.parameters.items():
        if name in ("context", "ctx", "run_context"):
            return True
        ann = getattr(param, "annotation", None)
        if "RunContext" in repr(ann):
            return True
    return False


async def _execute_inline(stream, name: str, args: dict[str, str]) -> str | None:
    """Look up `name` in the LLMStream's tool context, execute, return
    a voice-friendly string. Returns None if dispatch is impossible —
    in that case the caller suppresses the envelope without voicing
    anything (better than reading a TypeError aloud)."""
    try:
        tool_map = stream._tool_ctx.function_tools
    except Exception:
        return None
    tool = tool_map.get(name)
    if tool is None:
        logger.warning("[dsml] tool %r not in stream's tool list — suppressing silently", name)
        return None

    # Tools that need RunContext can't be invoked from outside the
    # framework. Captured live 2026-05-02 13:16: DSML envelope
    # contained transfer_to_desktop, recovery tried tool() and got
    # "missing 1 required positional argument: 'context'", that
    # error string was voiced. Silent suppression is the right move —
    # the framework's own retry path will dispatch the tool properly
    # in a moment.
    if _tool_takes_context(tool):
        logger.warning(
            "[dsml] tool %r needs RunContext; can't recover inline — "
            "suppressing envelope, framework will retry",
            name,
        )
        return None

    # Convert string args to whatever the tool's signature expects.
    # Cheap heuristic: try JSON-decoding first, fall back to string.
    typed_args: dict[str, Any] = {}
    for k, v in args.items():
        try:
            typed_args[k] = json.loads(v)
        except Exception:
            typed_args[k] = v

    try:
        result = tool(**typed_args)
        if inspect.iscoroutine(result):
            result = await result
    except Exception as e:
        # Don't voice the failure — log + suppress. The user shouldn't
        # hear "tool X failed: TypeError ...". If the LLM emitted DSML
        # again on its next attempt, this path runs again; if it
        # emitted structured tool_calls, the framework dispatches
        # cleanly and the user just hears the real result.
        logger.warning("[dsml] tool %r raised during inline exec: %s — suppressing", name, e)
        return None

    if isinstance(result, str):
        return result
    if isinstance(result, tuple):
        for elem in result:
            if isinstance(elem, str):
                return elem
        return str(result)
    if isinstance(result, dict):
        for key in ("message", "content", "text", "result"):
            v = result.get(key)
            if isinstance(v, str):
                return v
        return json.dumps(result)
    return str(result)


def install() -> None:
    """Patch LLMStream._parse_choice to suppress + recover DSML
    envelopes. Idempotent. Stacks safely on top of the existing
    deepseek_roundtrip + tool_name_sanitizer patches."""
    from livekit.agents.inference import llm as inf_llm
    from livekit.agents import llm as agents_llm

    if getattr(inf_llm.LLMStream, "_jarvis_dsml_patched", False):
        return

    orig_parse = inf_llm.LLMStream._parse_choice

    def patched(self, id, choice, thinking):
        delta = getattr(choice, "delta", None)
        if delta is not None:
            content = getattr(delta, "content", None) or ""
            state = _DSML_STATE.get(id)

            if state is None and _DSML_TRIGGER_CHAR in content:
                # First chunk that contains the DSML trigger char.
                # Walk back to the nearest `<` to find the envelope
                # start (e.g. "Sure sir. <｜｜DSML..." → split at `<`).
                trig_idx = content.find(_DSML_TRIGGER_CHAR)
                start_idx = content.rfind("<", 0, trig_idx)
                if start_idx == -1:
                    # No `<` before the trigger — treat the trigger
                    # itself as the boundary (some LLMs emit a bare
                    # `｜｜tool_calls...` opener).
                    start_idx = trig_idx
                pre = content[:start_idx]
                rest = content[start_idx:]
                _DSML_STATE[id] = {"buffer": rest}
                logger.warning(
                    "[dsml] envelope detected, suppressing tail (pre_len=%d, rest_len=%d)",
                    len(pre), len(rest),
                )
                _try_set_content(delta, pre)
            elif state is not None and content:
                # Already in envelope — accumulate, suppress streamed text.
                state["buffer"] += content
                _try_set_content(delta, "")

            # Check whether the envelope just closed.
            state = _DSML_STATE.get(id)
            if state is not None and _DSML_CLOSE in state["buffer"]:
                envelope = state["buffer"]
                del _DSML_STATE[id]
                # Fire-and-forget executor — schedules the tool call +
                # emits a fresh chunk asynchronously. Won't block the
                # current parse_choice call. If no event loop is
                # running (test context, sync invocation), the envelope
                # is still suppressed; we just can't recover by
                # dispatching the tool. Suppression alone protects the
                # user from hearing the markup spoken aloud.
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(_dispatch(self, envelope, agents_llm))
                except RuntimeError:
                    logger.debug(
                        "[dsml] no running event loop; suppressed envelope without recovery"
                    )
            elif state is not None and len(state["buffer"]) > 16000:
                # Safety bailout — never accumulate forever. If we hit
                # 16KB without seeing the closer, the envelope is
                # malformed; give up so memory doesn't grow unbounded.
                logger.warning("[dsml] buffer overflow without closer — discarding")
                del _DSML_STATE[id]

        # Always pass through to the original (and any other patches
        # stacked on top of us).
        return orig_parse(self, id, choice, thinking)

    inf_llm.LLMStream._parse_choice = patched
    inf_llm.LLMStream._jarvis_dsml_patched = True
    # Use warning level so this shows in the JSON log without
    # configuring a handler — install confirmation needs to be visible.
    logger.warning("DSML sanitizer installed (trigger=U+FF5C)")


async def _dispatch(stream, envelope: str, agents_llm) -> None:
    """Parse a complete DSML envelope, execute the first invoke, emit
    the result as a fresh ChatChunk so the framework voices it."""
    invokes = _parse_envelope(envelope)
    if not invokes:
        logger.warning("[dsml] envelope produced no parseable invoke; suppressed silently")
        return

    name, args = invokes[0]
    logger.info("[dsml] recovered tool call: name=%r args=%r", name, str(args)[:120])

    text = await _execute_inline(stream, name, args)
    if not text:
        logger.warning("[dsml] no text result from tool %r; suppressing silently", name)
        return

    # Trim aggressively — TTS reads everything we emit.
    if len(text) > 800:
        text = text[:800] + "…"

    chunk = agents_llm.ChatChunk(
        id=f"dsml_{uuid.uuid4().hex[:8]}",
        delta=agents_llm.ChoiceDelta(role="assistant", content=text),
    )
    try:
        stream._event_ch.send_nowait(chunk)
        logger.info("[dsml] emitted recovery chunk for %r (len=%d)", name, len(text))
    except Exception as e:
        logger.warning("[dsml] could not enqueue recovery chunk: %s", e)

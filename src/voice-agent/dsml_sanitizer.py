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

# DSML envelope markers. The two `｜` chars are U+FF5C, NOT regular `|`.
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


async def _execute_inline(stream, name: str, args: dict[str, str]) -> str | None:
    """Look up `name` in the LLMStream's tool context, execute, return
    a voice-friendly string. None if dispatch is impossible."""
    try:
        tool_map = stream._tool_ctx.function_tools
    except Exception:
        return None
    tool = tool_map.get(name)
    if tool is None:
        logger.warning("[dsml] tool %r not in stream's tool list", name)
        return f"(I tried to call {name} but that tool isn't available in this context)"

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
        logger.warning("[dsml] tool %r raised: %s", name, e)
        return f"(tool {name} failed: {e})"

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

            if state is None and _DSML_OPEN in content:
                # Begin envelope buffering. Anything BEFORE the opener
                # in this chunk passes through as normal content.
                pre, _, rest = content.partition(_DSML_OPEN)
                _DSML_STATE[id] = {"buffer": _DSML_OPEN + rest}
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

        # Always pass through to the original (and any other patches
        # stacked on top of us).
        return orig_parse(self, id, choice, thinking)

    inf_llm.LLMStream._parse_choice = patched
    inf_llm.LLMStream._jarvis_dsml_patched = True
    logger.info("DSML sanitizer installed")


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

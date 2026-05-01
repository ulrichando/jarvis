"""Recover from `tool call validation failed` by parsing the malformed
name out of the provider's error AND executing the tool inline.

The recurring bug: certain LLMs (Groq's qwen3-32b, llama 3.3 70B at
times) produce tool_calls where the name field contains both the real
name AND the JSON arguments concatenated:

    name='recall_conversation {"query": "total"}'
    arguments=''

Groq's server validates tool names against `request.tools` and
rejects with HTTP/SSE error:

    openai.APIError: tool call validation failed: attempted to call
    tool 'recall_conversation {"query": "total"}' which was not in
    request.tools

Without intervention the entire turn is lost — the agent goes silent.

═══ Why inline execution (not chunk-injection) ═══

The previous implementation synthesized a `ChatChunk` containing the
recovered `FunctionToolCall` and pushed it through `_event_ch`. Live
2026-05-01: a real recovery for `get_location` fired (logged
"[sanitizer] recovered ..."), but `get_location` itself never
executed — `[get_location] Google/Wi-Fi → ...` log was absent for
every recovery. The framework's tool-dispatch loop in
`voice/generation.py` consumes the tee'd `_event_ch`, queues
FunctionCalls into `function_ch`, and `_execute_tools_task` runs them
with a real RunContext. Something in that chain (tee timing, channel
close ordering, RunContext expectations) drops chunks emitted from
inside an exception handler. After ~30 min of digging the cleanest
fix is to bypass the chain entirely.

This module patches `inference.llm.LLMStream._run` to:
  1. Catch the validation APIError.
  2. Parse the malformed name with a tight regex.
  3. Confirm the tool exists in `self._tool_ctx`.
  4. **Execute the tool's underlying coroutine inline** with the
     parsed JSON arguments.
  5. Format the result as plain text.
  6. Emit ONE ChatChunk with `delta.content = result_text` (no
     tool_calls). The framework treats it as plain LLM output and
     voices it directly.

Trade-off: the LLM never sees the tool result so it can't reason
about it for a follow-up reply. For simple lookup tools (get_location
returns "Parsons Avenue, Columbus", recall_conversation returns a
quote) the result IS the user-facing answer, so this is fine. For
tools where the LLM should narrate around the result, the recovery
voice is degraded — but degraded-but-working beats silent.
"""
from __future__ import annotations

import inspect
import json
import logging
import re
import uuid

logger = logging.getLogger("jarvis.tool_name_sanitizer")

# Provider error message shape (Groq specifically; others may follow).
_VALIDATION_RE = re.compile(
    r"tool call validation failed: "
    r"attempted to call tool '(.+?)' which was not in request\.tools",
    re.IGNORECASE | re.DOTALL,
)

# Tight pattern: identifier + (whitespace OR `=` OR `:`) + JSON object body.
# Captured forms seen live:
#   `recall_conversation {"query": "total"}`        — Groq qwen3 (space)
#   `web_fetch={"url":"...","timeout":"15"}`       — Groq llama (= sign)
#   `bash:{"cmd":"ls"}`                              — defensive (colon)
_NAME_JSON_RE = re.compile(
    r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[=:]?\s*(\{.*\})\s*$",
    re.DOTALL,
)


def _try_recover(error_msg: str, known_tool_names: set[str]) -> tuple[str, str] | None:
    """Parse (real_name, args_json) from a validation error message.

    Returns None if:
      - The error message doesn't match the validation pattern.
      - The malformed name doesn't fit the `name + JSON` shape.
      - The recovered name isn't actually in our tool list.
    """
    m = _VALIDATION_RE.search(error_msg)
    if not m:
        return None
    bad_name = m.group(1)
    sm = _NAME_JSON_RE.match(bad_name)
    if not sm:
        return None
    real_name = sm.group(1)
    args_json = sm.group(2)
    if real_name not in known_tool_names:
        return None
    return real_name, args_json


def _tool_takes_context(tool) -> bool:
    """True if the tool's signature requires a `context` / `RunContext`
    argument we'd have to construct. Inline execution can't supply
    that, so we skip those tools and let the original error propagate."""
    func = getattr(tool, "_func", tool)
    try:
        sig = inspect.signature(func)
    except Exception:
        return True  # Conservative — bail rather than guess.
    for name, param in sig.parameters.items():
        if name in ("context", "ctx", "run_context"):
            return True
        ann = getattr(param, "annotation", None)
        ann_str = repr(ann)
        if "RunContext" in ann_str:
            return True
    return False


def _format_result(result) -> str:
    """Coerce whatever the tool returned to a voice-friendly string.

    Tool returns can be: str, dict, tuple (Agent, str) for transfer
    tools, or other types. For the recovery path we just need
    SOMETHING to voice — best-effort stringification.
    """
    if isinstance(result, str):
        return result
    if isinstance(result, tuple):
        # Specialist transfer tools return (Agent, str). Voice the str.
        for elem in result:
            if isinstance(elem, str):
                return elem
        return str(result)
    if isinstance(result, dict):
        # Pick a 'message' / 'content' / 'text' field if present.
        for key in ("message", "content", "text", "result"):
            if key in result and isinstance(result[key], str):
                return result[key]
        return json.dumps(result)
    return str(result)


def install() -> None:
    """Wrap `inference.llm.LLMStream._run` with an inline-execution
    recovery layer. Idempotent."""
    from livekit.agents.inference import llm as inf_llm
    from livekit.agents import llm as agents_llm

    if getattr(inf_llm.LLMStream, "_jarvis_sanitizer_patched", False):
        return

    orig_run = inf_llm.LLMStream._run

    async def _patched_run(self) -> None:
        try:
            await orig_run(self)
            return
        except Exception as e:
            # Walk exception chain for the validation pattern. APIError
            # often gets wrapped as APIConnectionError by the plugin.
            chain_msgs: list[str] = []
            cur: BaseException | None = e
            for _ in range(6):
                if cur is None:
                    break
                chain_msgs.append(str(cur))
                cur = (
                    getattr(cur, "__cause__", None)
                    or getattr(cur, "__context__", None)
                )
            joined = " || ".join(chain_msgs)

            try:
                tool_map = self._tool_ctx.function_tools
                known = set(tool_map.keys())
            except Exception:
                tool_map = {}
                known = set()

            recovery = _try_recover(joined, known)
            if recovery is None:
                raise

            real_name, args_json = recovery
            tool = tool_map.get(real_name)
            if tool is None:
                raise

            if _tool_takes_context(tool):
                # Can't construct a RunContext from inside _run.
                # Surface the original error so the user gets the
                # apology voice (Phase 9.2) instead of a wrong answer.
                logger.warning(
                    "[sanitizer] tool %r requires context; can't recover inline",
                    real_name,
                )
                raise

            # Parse JSON args. If parsing fails, fall through to original.
            try:
                kwargs = json.loads(args_json) if args_json.strip() else {}
                if not isinstance(kwargs, dict):
                    raise ValueError("args not a dict")
            except Exception as parse_err:
                logger.warning(
                    "[sanitizer] could not parse args for %r: %s",
                    real_name, parse_err,
                )
                raise e from None

            logger.info(
                "[sanitizer] recovered + executing inline: name=%r args=%r",
                real_name, args_json[:80],
            )

            # Execute the tool's underlying function. FunctionTool's
            # __call__ delegates to self._func, returning a coroutine.
            try:
                result = tool(**kwargs)
                if inspect.iscoroutine(result):
                    result = await result
            except Exception as call_err:
                logger.warning(
                    "[sanitizer] tool %r raised during inline execution: %s",
                    real_name, call_err,
                )
                raise e from None

            text = _format_result(result)
            logger.info(
                "[sanitizer] inline result for %r: %r",
                real_name, text[:120],
            )

            # Emit ONE chunk with plain content. The framework's
            # text channel forwards content directly to TTS — no
            # tool dispatch needed.
            chunk = agents_llm.ChatChunk(
                id=f"recovery_{uuid.uuid4().hex[:8]}",
                delta=agents_llm.ChoiceDelta(
                    role="assistant",
                    content=text,
                ),
            )
            try:
                self._event_ch.send_nowait(chunk)
            except Exception as send_err:
                logger.warning(
                    "[sanitizer] could not enqueue recovery chunk: %s",
                    send_err,
                )
                raise e from None

    inf_llm.LLMStream._run = _patched_run
    inf_llm.LLMStream._jarvis_sanitizer_patched = True
    logger.info("tool-name sanitizer installed (inline-execution mode)")

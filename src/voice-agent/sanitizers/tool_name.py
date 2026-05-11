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
        # Subagent transfer tools return (Agent, str). Voice the str.
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
                # Can't run the tool inline (no RunContext available),
                # but we have the recovered name + args. Two recovery
                # paths depending on tool shape:
                #
                # (A) Re-emit as a proper FunctionToolCall chunk. The
                #     framework's normal tool-dispatch loop picks it
                #     up and runs it with a real RunContext. Use this
                #     for tools with simple single-string args where
                #     we trust the recovered JSON to round-trip
                #     cleanly — primarily the `transfer_to_*` family
                #     (request: str). This is what restores the user's
                #     actual intent ("open a tab" → handoff fires →
                #     subagent runs).
                #
                # (B) Soft-recovery apology chunk. Use for tools where
                #     the LLM's malformation usually corrupts args too
                #     (delegate, observed 2026-05-04) or where running
                #     out of order would be wrong (subagent-internal
                #     tools like ext_* / task_done — those shouldn't
                #     even reach this stream from the supervisor turn).
                #     User hears something, breaker stays closed, next
                #     turn gets a fresh attempt with chat_ctx nudge.
                #
                # The pre-449fc89 behavior was raise-everything, which
                # tripped _LLM_BREAKER and left the user with silence.
                # The 449fc89 behavior was soft-recover-everything,
                # which swallowed every transfer_to_X — no subagent
                # ever fired (live-observed 2026-05-04 13:07: two
                # browser handoffs, both swallowed, user heard
                # "rephrase that" on each instead of getting a tab).
                # This is the targeted fix: re-emit transfer_to_*,
                # soft-recover the rest.
                if real_name.startswith("transfer_to_"):
                    logger.info(
                        "[sanitizer] re-emitting %r as tool_call "
                        "(args=%s)",
                        real_name, args_json[:120],
                    )
                    tool_call = agents_llm.FunctionToolCall(
                        name=real_name,
                        arguments=args_json,
                        call_id=f"sanitized_{uuid.uuid4().hex[:8]}",
                    )
                    chunk = agents_llm.ChatChunk(
                        id=f"recovery_{uuid.uuid4().hex[:8]}",
                        delta=agents_llm.ChoiceDelta(
                            role="assistant",
                            tool_calls=[tool_call],
                        ),
                    )
                    try:
                        self._event_ch.send_nowait(chunk)
                        return
                    except Exception as send_err:
                        logger.warning(
                            "[sanitizer] could not enqueue recovered "
                            "tool_call for %r: %s; falling through to "
                            "soft-recovery",
                            real_name, send_err,
                        )
                        # Fall through to soft-recovery below.

                logger.warning(
                    "[sanitizer] tool %r requires context; soft recovery "
                    "(args truncated: %s)",
                    real_name, args_json[:80],
                )
                soft_msg = (
                    "Let me try that differently."
                    if real_name == "delegate"
                    else "One moment — let me rephrase that."
                )
                soft_chunk = agents_llm.ChatChunk(
                    id=f"soft_{uuid.uuid4().hex[:8]}",
                    delta=agents_llm.ChoiceDelta(
                        role="assistant",
                        content=soft_msg,
                    ),
                )
                try:
                    self._event_ch.send_nowait(soft_chunk)
                    return  # success — breaker stays closed
                except Exception as send_err:
                    logger.warning(
                        "[sanitizer] could not enqueue soft recovery: %s",
                        send_err,
                    )
                    raise

            # Re-emit as a proper FunctionToolCall chunk and let the
            # framework's tool-dispatch loop handle execution +
            # result-injection into chat_ctx. Same pattern as the
            # transfer_to_* branch above.
            #
            # Why NOT execute inline (the previous behavior): inline
            # execution emitted the raw tool result as
            # `role: "assistant", content: <result>`. Two failure
            # modes:
            #   1. TTS spoke the dict repr aloud — live-observed
            #      2026-05-05 21:55 with ext_navigate returning a
            #      page-headings dict; user heard
            #      "cmd_id ef758cc7 headings level 2 text SiteStripe..."
            #      verbatim.
            #   2. LLM saw `role: "assistant"`, not `role: "tool"`, on
            #      its next turn — so the next inference didn't know a
            #      tool had returned, and re-attempted the same call.
            #      Live-observed same session: three identical
            #      ext_navigate("https://www.amazon.com") calls within
            #      30 s, each "succeeding" but the LLM looping.
            #
            # The framework's dispatch loop puts results into chat_ctx
            # as `role: "tool"` messages and never sends them to TTS,
            # which is what we want. We just hand it a clean tool_call
            # and step out of the way.
            logger.info(
                "[sanitizer] re-emitting %r as tool_call (let framework "
                "dispatch with proper RunContext): args=%s",
                real_name, args_json[:120],
            )
            tool_call = agents_llm.FunctionToolCall(
                name=real_name,
                arguments=args_json,
                call_id=f"sanitized_{uuid.uuid4().hex[:8]}",
            )
            chunk = agents_llm.ChatChunk(
                id=f"recovery_{uuid.uuid4().hex[:8]}",
                delta=agents_llm.ChoiceDelta(
                    role="assistant",
                    tool_calls=[tool_call],
                ),
            )
            try:
                self._event_ch.send_nowait(chunk)
                return
            except Exception as send_err:
                logger.warning(
                    "[sanitizer] could not enqueue recovered tool_call "
                    "for %r: %s; falling back to soft recovery",
                    real_name, send_err,
                )
                # Soft fallback — never emit raw tool results as content.
                soft_chunk = agents_llm.ChatChunk(
                    id=f"soft_{uuid.uuid4().hex[:8]}",
                    delta=agents_llm.ChoiceDelta(
                        role="assistant",
                        content="One moment — let me try that again.",
                    ),
                )
                try:
                    self._event_ch.send_nowait(soft_chunk)
                    return
                except Exception:
                    raise e from None

    inf_llm.LLMStream._run = _patched_run
    inf_llm.LLMStream._jarvis_sanitizer_patched = True
    logger.info("tool-name sanitizer installed (inline-execution mode)")

"""Recover from `tool call validation failed` by parsing the malformed
name out of the provider's error and synthesizing a clean ChatChunk.

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
Phase 9.2 added a graceful fallback voice ("Sorry, sir, I had trouble
with that — could you rephrase?"), but the user still has to repeat
themselves.

This module patches `inference.llm.LLMStream._run` to:
  1. Catch the validation APIError.
  2. Parse the malformed name with a tight regex.
  3. Split it into a real tool name + JSON args body.
  4. Confirm the real name IS in this stream's tool list.
  5. Synthesize a ChatChunk as if the LLM had produced clean output.
  6. Send the chunk through `_event_ch` and return normally.

If any of those steps fails the original error propagates so callers
see the same behavior they did before.
"""
from __future__ import annotations

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

# Tight pattern: identifier + whitespace + JSON object body.
# We deliberately require the trailing `{...}` so we don't try to recover
# from genuinely unknown names like 'do_thing_xyz' that the LLM hallucinated.
_NAME_JSON_RE = re.compile(
    r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(\{.*\})\s*$",
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


def install() -> None:
    """Wrap `inference.llm.LLMStream._run` with a recovery layer.
    Idempotent."""
    from livekit.agents.inference import llm as inf_llm
    from livekit.agents import llm as agents_llm

    if getattr(inf_llm.LLMStream, "_jarvis_sanitizer_patched", False):
        return

    orig_run = inf_llm.LLMStream._run

    async def _patched_run(self) -> None:
        try:
            await orig_run(self)
        except Exception as e:
            # Search the exception chain — APIError can be wrapped as
            # APIConnectionError by the plugin's outer except clause.
            chain_msgs: list[str] = []
            cur: BaseException | None = e
            for _ in range(6):
                if cur is None:
                    break
                chain_msgs.append(str(cur))
                cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
            joined = " || ".join(chain_msgs)

            try:
                known = set(self._tool_ctx.function_tools.keys())
            except Exception:
                known = set()

            recovery = _try_recover(joined, known)
            if recovery is None:
                raise

            real_name, args_json = recovery
            call_id = f"call_jarvis_recovery_{uuid.uuid4().hex[:10]}"
            logger.info(
                "[sanitizer] recovered malformed tool_call: name=%r args_len=%d",
                real_name,
                len(args_json),
            )

            chunk = agents_llm.ChatChunk(
                id=f"recovery_{uuid.uuid4().hex[:8]}",
                delta=agents_llm.ChoiceDelta(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        agents_llm.FunctionToolCall(
                            arguments=args_json,
                            name=real_name,
                            call_id=call_id,
                        )
                    ],
                ),
            )
            try:
                self._event_ch.send_nowait(chunk)
            except Exception as send_err:
                logger.warning(
                    "[sanitizer] could not enqueue recovery chunk: %s", send_err
                )
                raise e from None

    inf_llm.LLMStream._run = _patched_run
    inf_llm.LLMStream._jarvis_sanitizer_patched = True
    logger.info("tool-name sanitizer installed")

"""
Model layer — call_claude() and call_qwen() with fallback logic and token logging.
Claude is only called when Qwen cannot handle the request (tool calls, complex reasoning).
"""

import logging
import os
import time
from typing import Any

import anthropic
import httpx

from brain.telemetry import APICall, telemetry
from brain.budget    import budget_guard

logger = logging.getLogger(__name__)

# ── Clients ───────────────────────────────────────────────────────────────────

ANTHROPIC_CLIENT = anthropic.AsyncAnthropic()  # reads ANTHROPIC_API_KEY from env

QWEN_BASE_URL: str = os.getenv("QWEN_BASE_URL", "http://localhost:11434")
QWEN_MODEL:    str = os.getenv("QWEN_MODEL",    "qwen2.5:7b")

CLAUDE_MODEL = "claude-haiku-4-5-20251001"  # cheapest Claude — use for all brain calls


async def call_claude(
    message: str,
    system: str,
    history: list[dict[str, str]],
    tools: list[dict[str, Any]],
    tool_choice: dict[str, Any],
    max_tokens: int,
    channel_id: str = "cli",
    tool_name: str | None = None,
) -> str:
    """
    Call Claude via Anthropic API. Only called when Qwen cannot handle the request.
    Handles tool use inline — executes the tool and sends results back for a final response.
    Logs token usage on every call for cost tracking.

    System prompt is sent with cache_control so repeated calls reuse the cached
    version (~90% input token reduction on cache hits).
    """
    # Force Qwen fallback if daily budget is exhausted
    if budget_guard.is_over_budget():
        logger.critical("[BUDGET] Over daily limit — routing to Qwen instead of Claude")
        return await call_qwen(message=message, history=history, max_tokens=max_tokens)

    messages = history + [{"role": "user", "content": message}]
    start_ms = time.monotonic()

    try:
        kwargs: dict[str, Any] = {
            "model":      CLAUDE_MODEL,
            "max_tokens": max_tokens,
            # cache_control marks the system prompt for Anthropic's prompt cache.
            # On cache hit, input token cost drops ~90%.
            "system": [
                {
                    "type":          "text",
                    "text":          system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages":     messages,
            "extra_headers": {"anthropic-beta": "prompt-caching-2024-07-31"},
        }

        if tools:
            kwargs["tools"]       = tools
            kwargs["tool_choice"] = tool_choice
        else:
            kwargs["tool_choice"] = {"type": "none"}

        response = await ANTHROPIC_CLIENT.messages.create(**kwargs)

        latency_ms = int((time.monotonic() - start_ms) * 1000)
        usage      = response.usage
        cache_read  = getattr(usage, "cache_read_input_tokens",    0)
        cache_write = getattr(usage, "cache_creation_input_tokens", 0)
        logger.info(
            f"[claude] in={usage.input_tokens} out={usage.output_tokens} "
            f"cache_read={cache_read} cache_write={cache_write} "
            f"total={usage.input_tokens + usage.output_tokens} "
            f"max_set={max_tokens} latency_ms={latency_ms}"
        )

        # Record in telemetry and budget
        telemetry.record(APICall(
            channel_id=channel_id,
            route="claude",
            tool_name=tool_name,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=latency_ms,
            message=message[:80],
        ))
        budget_guard.add(usage.input_tokens + usage.output_tokens)

        # Handle tool use — execute and get final response
        for block in response.content:
            if block.type == "tool_use":
                return await _execute_tool_and_respond(
                    tool_name=block.name,
                    tool_input=block.input,
                    system=system,
                    history=messages,
                    max_tokens=max_tokens,
                    channel_id=channel_id,
                )

        # Plain text response
        text_blocks = [b.text for b in response.content if hasattr(b, "text")]
        return " ".join(text_blocks).strip()

    except anthropic.APIStatusError as e:
        logger.error(f"[claude] API status error {e.status_code}: {e.message}")
        raise
    except anthropic.APIConnectionError as e:
        logger.error(f"[claude] connection error: {e}")
        raise


async def call_qwen(
    message: str,
    history: list[dict[str, str]],
    max_tokens: int,
    channel_id: str = "cli",
) -> str:
    """
    Call self-hosted Qwen 7B via Ollama API. Free — used for all simple requests.
    Falls back to Claude Haiku if Qwen is unreachable or returns an error.
    """
    messages = history + [{"role": "user", "content": message}]
    start_ms = time.monotonic()

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{QWEN_BASE_URL}/api/chat",
                json={
                    "model":    QWEN_MODEL,
                    "messages": messages,
                    "options":  {"num_predict": max_tokens},
                    "stream":   False,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        latency_ms = int((time.monotonic() - start_ms) * 1000)
        logger.info(
            f"[qwen] model={QWEN_MODEL} max_tokens={max_tokens} latency_ms={latency_ms}"
        )

        # Qwen has no token counts in the response — record 0 cost
        telemetry.record(APICall(
            channel_id=channel_id,
            route="qwen",
            tool_name=None,
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            message=message[:80],
        ))

        return data["message"]["content"].strip()

    except httpx.HTTPStatusError as e:
        logger.warning(f"[qwen] HTTP {e.response.status_code} — falling back to Claude Haiku")
    except httpx.RequestError as e:
        logger.warning(f"[qwen] unreachable ({e}) — falling back to Claude Haiku")
    except KeyError as e:
        logger.warning(f"[qwen] unexpected response shape ({e}) — falling back to Claude Haiku")

    # Fallback: Claude Haiku with no tools, strict token cap
    # Skip fallback when LOCAL_ONLY — no API key available
    import os
    if os.getenv("LOCAL_ONLY", "0") == "1":
        logger.error("[qwen] failed and LOCAL_ONLY=1 — no fallback available")
        return "Sorry, the local model is unavailable right now."
    return await call_claude(
        message=message,
        system="You are JARVIS, a concise AI assistant. Answer in 1-2 sentences.",
        history=history,
        tools=[],
        tool_choice={"type": "none"},
        max_tokens=min(max_tokens, 200),
        channel_id=channel_id,
    )


async def _execute_tool_and_respond(
    tool_name: str,
    tool_input: dict[str, Any],
    system: str,
    history: list[dict[str, str]],
    max_tokens: int,
    channel_id: str = "cli",
) -> str:
    """
    Execute a tool call returned by Claude and send the result back for a final
    natural-language response. Uses tool_choice=none on the followup to prevent loops.
    """
    from brain.tools.executor import execute_tool  # local import to avoid circular

    tool_result = await execute_tool(tool_name, tool_input)

    followup_messages = history + [
        {
            "role":    "user",
            "content": f"Tool result for {tool_name}: {tool_result}",
        }
    ]

    start_ms = time.monotonic()

    try:
        response = await ANTHROPIC_CLIENT.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=[
                {
                    "type":          "text",
                    "text":          system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=followup_messages,
            tool_choice={"type": "none"},  # no more tools on the followup
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
        )

        latency_ms = int((time.monotonic() - start_ms) * 1000)
        usage      = response.usage
        logger.info(
            f"[claude][followup] tool={tool_name} "
            f"in={usage.input_tokens} out={usage.output_tokens} "
            f"latency_ms={latency_ms}"
        )

        telemetry.record(APICall(
            channel_id=channel_id,
            route="claude",
            tool_name=f"{tool_name}:followup",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=latency_ms,
            message=f"[followup for {tool_name}]",
        ))
        budget_guard.add(usage.input_tokens + usage.output_tokens)

        text_blocks = [b.text for b in response.content if hasattr(b, "text")]
        return " ".join(text_blocks).strip()

    except anthropic.APIStatusError as e:
        logger.error(f"[claude][followup] API error {e.status_code}: {e.message}")
        raise

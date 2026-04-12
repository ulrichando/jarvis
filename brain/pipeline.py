"""
Main request pipeline. Every incoming message from every channel passes through here.
Order: inline → cache → classify → qwen → claude
"""

import logging
import os
from datetime import datetime

from brain.classifier         import classify_request
from brain.history            import ChannelHistory
from brain.cache              import ResponseCache
from brain.models             import call_claude, call_qwen
from brain.tools.registry     import get_tools_for_request
from brain.prompts            import build_system_prompt

logger     = logging.getLogger(__name__)
history    = ChannelHistory()
cache      = ResponseCache()

# Set LOCAL_ONLY=1 to route everything through Ollama — no Claude API calls
LOCAL_ONLY: bool = os.getenv("LOCAL_ONLY", "0") == "1"


async def handle_request(channel_id: str, message: str) -> str:
    """
    Process one message from one channel end-to-end.
    Returns the response string to send back to the channel.

    Pipeline:
      0. Inline — answer directly from server state (zero model cost)
      1. Cache  — return a recent identical response (zero API cost)
      2. Classify — determine route, tool, and token budget
      3. Qwen   — free local model for simple/conversational requests
      4. Claude — paid API only for tools and complex reasoning
    """

    # ── Step 0: Inline answers ────────────────────────────────────────────────
    inline = _try_inline_answer(message, channel_id)
    if inline is not None:
        logger.info(f"[pipeline] inline channel={channel_id}")
        return inline

    # ── Step 1: Cache check ───────────────────────────────────────────────────
    cache_key = cache.make_key(channel_id, message)
    cached    = cache.get(cache_key)
    if cached is not None:
        logger.info(f"[pipeline] cache_hit channel={channel_id}")
        return cached

    # ── Step 2: Classify ──────────────────────────────────────────────────────
    classification = classify_request(message, channel_id)
    logger.info(
        f"[pipeline] channel={channel_id} route={classification.route_to} "
        f"task={classification.task_type} tool={classification.tool_name} "
        f"max_tokens={classification.max_tokens}"
    )

    # ── Step 3: Qwen (free) ───────────────────────────────────────────────────
    if classification.route_to == "qwen":
        response = await call_qwen(
            message=message,
            history=history.get(channel_id),
            max_tokens=classification.max_tokens,
            channel_id=channel_id,
        )
        history.add_user(channel_id, message)
        history.add_assistant(channel_id, response)
        if classification.cache_key:
            cache.set(classification.cache_key, response)
        return response

    # ── Step 4: Claude (paid) — skipped when LOCAL_ONLY=1 ────────────────────
    if LOCAL_ONLY:
        logger.info(f"[pipeline] LOCAL_ONLY — routing to Qwen instead of Claude")
        response = await call_qwen(
            message=message,
            history=history.get(channel_id),
            max_tokens=classification.max_tokens,
            channel_id=channel_id,
        )
    else:
        tools = get_tools_for_request(classification.tool_name)
        tool_choice: dict = (
            {"type": "tool", "name": classification.tool_name}
            if classification.needs_tool and classification.tool_name
            else {"type": "none"}
        )
        response = await call_claude(
            message=message,
            system=build_system_prompt(channel_id),
            history=history.get(channel_id),
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=classification.max_tokens,
            channel_id=channel_id,
            tool_name=classification.tool_name,
        )

    history.add_user(channel_id, message)
    history.add_assistant(channel_id, response)
    if classification.cache_key:
        cache.set(classification.cache_key, response, classification.tool_name)
    return response


def _try_inline_answer(message: str, channel_id: str) -> str | None:
    """
    Answer trivial requests directly from server state — no model call whatsoever.
    Returns None if the request cannot be answered inline.
    """
    msg = message.lower().strip()

    time_triggers   = ["what time", "current time", "quelle heure", "what's the time"]
    date_triggers   = ["what day", "today's date", "what date", "quelle date", "what is today"]
    status_triggers = ["are you online", "are you there", "you there", "jarvis online"]

    if any(t in msg for t in time_triggers):
        now = datetime.now().strftime("%I:%M %p")
        return f"It's {now}." if channel_id == "voice" else f"Current time: {now}"

    if any(t in msg for t in date_triggers):
        today = datetime.now().strftime("%A, %B %d %Y")
        return f"Today is {today}." if channel_id == "voice" else f"Today: {today}"

    if any(t in msg for t in status_triggers):
        return "Online and ready." if channel_id == "voice" else "JARVIS is online."

    return None

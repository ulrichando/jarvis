"""Dispatch nodes for the supervisor graph.

One node per route. The TASK node is the load-bearing one — it forces
`tool_choice="required"` at the Groq API level so the LLM literally
cannot emit completion text. The BANTER/REASONING/EMOTIONAL nodes
are normal "speak" nodes that produce content.

Model choices:
  - TASK         → llama-3-groq-8b-tool-use (Groq's tool-tuned variant
                    that doesn't emit `<|python_tag|>` malformations).
                    Falls back to llama-3.3-70b-versatile via env.
  - BANTER       → llama-3.1-8b-instant (fastest; no tools attached so
                    the malformation surface is gone).
  - REASONING    → qwen3-32b (best for analysis; optional tools).
  - EMOTIONAL    → llama-4-scout-17b (warm tone).

All env-overridable via JARVIS_GRAPH_<ROUTE>_MODEL.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq

logger = logging.getLogger("supervisor_graph.dispatch")


def _build_task_llm():
    """Tool-dispatch LLM. Default: tool-tuned llama variant. Override
    via JARVIS_GRAPH_TASK_MODEL."""
    model = os.environ.get(
        "JARVIS_GRAPH_TASK_MODEL", "llama-3.3-70b-versatile"
    )
    return ChatGroq(model=model, temperature=0.3, max_tokens=512)


def _build_task_fallback_llm():
    """DeepSeek fallback for TASK turns. Uses the same
    tool_choice='required' contract — the fallback CANNOT lie about
    completion either. Cures cross-stream hallucination (failure
    mode #5, live-observed 2026-05-04)."""
    from langchain_openai import ChatOpenAI  # DeepSeek is OpenAI-compat
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get(
        "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
    )
    return ChatOpenAI(
        model=os.environ.get("JARVIS_GRAPH_TASK_FALLBACK_MODEL", "deepseek-chat"),
        api_key=api_key,
        base_url=base_url,
        temperature=0.3,
        max_tokens=512,
    )


def task_dispatch_node(state: dict, tools: list[Any]) -> dict:
    """Force a tool_call. Primary: Groq. Fallback (on any exception):
    DeepSeek, also with tool_choice='required'. The fallback sees the
    SAME state — no partial assistant content has been appended yet,
    so it cannot confabulate completion.
    """
    user_query = state.get("user_query") or ""
    history = state.get("messages") or []
    failed: list[str] = list(state.get("failed_providers") or [])

    sys_prompt = (
        "You are JARVIS's task-dispatch supervisor. The user just gave "
        "an imperative. Pick the right specialist via transfer_to_X "
        "and emit ONLY that tool call — never any text content. "
        "If unsure which specialist, pick the closest match."
    )

    msgs = [SystemMessage(content=sys_prompt)] + list(history) + [
        HumanMessage(content=user_query),
    ]

    def _try(builder, provider_name: str):
        llm = builder()
        bound = llm.bind_tools(tools, tool_choice="required")
        return bound.invoke(msgs)

    response: AIMessage
    try:
        response = _try(_build_task_llm, "groq")
    except Exception as e:
        logger.warning(
            "[task-dispatch] primary (groq) failed: %s: %s — falling back to deepseek",
            type(e).__name__, e,
        )
        failed.append("groq")
        # Fallback: re-invoke with the SAME messages + SAME contract.
        # No partial assistant turn has been appended; fallback gets a
        # clean state and cannot lie about completion.
        try:
            response = _try(_build_task_fallback_llm, "deepseek")
        except Exception as e2:
            logger.error(
                "[task-dispatch] fallback (deepseek) ALSO failed: %s: %s",
                type(e2).__name__, e2,
            )
            raise

    tool_calls = response.tool_calls or []
    pending = [tc["id"] for tc in tool_calls if tc.get("id")]

    # Detect specialist handoff so the graph's downstream branch can
    # route to specialist_node (Task 11 added this; preserve it).
    pending_specialist = None
    for tc in tool_calls:
        name = tc.get("name", "")
        if name.startswith("transfer_to_"):
            pending_specialist = name[len("transfer_to_"):]
            break

    logger.info(
        "[task-dispatch] emitted %d tool_call(s): %s (failed_providers=%s)",
        len(tool_calls),
        ", ".join(tc.get("name", "?") for tc in tool_calls),
        failed,
    )

    return {
        "messages": [response],
        "pending_tool_calls": pending,
        "pending_specialist": pending_specialist,
        "failed_providers": failed,
    }


def _build_banter_llm():
    model = os.environ.get(
        "JARVIS_GRAPH_BANTER_MODEL", "llama-3.1-8b-instant"
    )
    return ChatGroq(model=model, temperature=0.6, max_tokens=160)


def _build_reasoning_llm():
    model = os.environ.get(
        "JARVIS_GRAPH_REASONING_MODEL", "qwen/qwen3-32b"
    )
    return ChatGroq(model=model, temperature=0.4, max_tokens=512)


def _build_emotional_llm():
    model = os.environ.get(
        "JARVIS_GRAPH_EMOTIONAL_MODEL",
        "meta-llama/llama-4-scout-17b-16e-instruct",
    )
    return ChatGroq(model=model, temperature=0.7, max_tokens=300)


_PERSONA = (
    "You are JARVIS, a dignified British butler. Address the user as "
    "'sir' sparingly — at most once per reply, only when natural. "
    "Speak in plain English; never use markdown, bullet lists, or "
    "emoji. Keep replies short for voice — one or two sentences."
)


def banter_speak_node(state: dict) -> dict:
    """Chitchat. No tools. Pure content."""
    return _speak_with(state, _build_banter_llm(),
                       extra_system="Reply briefly, casually, warmly.")


def reasoning_speak_node(state: dict) -> dict:
    """Explanation / analysis. No tools."""
    return _speak_with(state, _build_reasoning_llm(),
                       extra_system="Explain clearly. Use plain language.")


def emotional_speak_node(state: dict) -> dict:
    """Empathic acknowledgment. No tools."""
    return _speak_with(state, _build_emotional_llm(),
                       extra_system="Acknowledge feelings warmly; do not lecture.")


def _speak_with(state: dict, llm, *, extra_system: str) -> dict:
    """Common 'invoke an LLM with the persona + history + user_query'
    body for the no-tool speak nodes."""
    user_query = state.get("user_query") or ""
    history = state.get("messages") or []

    msgs = [
        SystemMessage(content=_PERSONA),
        SystemMessage(content=extra_system),
    ] + list(history) + [HumanMessage(content=user_query)]

    try:
        response = llm.invoke(msgs)
    except Exception as e:
        logger.warning(
            "[speak] LLM error: %s: %s", type(e).__name__, e,
        )
        raise

    return {"messages": [response]}

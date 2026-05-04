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


def task_dispatch_node(state: dict, tools: list[Any]) -> dict:
    """Force a tool_call. The supervisor cannot emit completion text
    on TASK turns; tool_choice='required' guarantees this at the API
    level. The output AIMessage's tool_calls populate
    `pending_tool_calls` so speak_gate refuses to fire until the
    matching ToolMessages arrive.

    This node is called with the supervisor's tool list bound — the
    graph builder (graph.py) injects the registered transfer_to_X
    tools.
    """
    user_query = state.get("user_query") or ""
    history = state.get("messages") or []

    llm = _build_task_llm()
    bound = llm.bind_tools(tools, tool_choice="required")

    sys_prompt = (
        "You are JARVIS's task-dispatch supervisor. The user just gave "
        "an imperative. Pick the right specialist via transfer_to_X "
        "and emit ONLY that tool call — never any text content. "
        "If unsure which specialist, pick the closest match."
    )

    msgs = [SystemMessage(content=sys_prompt)] + list(history) + [
        HumanMessage(content=user_query),
    ]

    try:
        response: AIMessage = bound.invoke(msgs)
    except Exception as e:
        # Caller (the graph) handles fallback. Re-raise here so the
        # graph's recovery edge fires.
        logger.warning(
            "[task-dispatch] LLM error: %s: %s", type(e).__name__, e,
        )
        raise

    # tool_calls is a list of dicts in LangChain shape:
    #   {"name": ..., "args": ..., "id": ..., "type": "tool_call"}
    tool_calls = response.tool_calls or []
    pending = [tc["id"] for tc in tool_calls if tc.get("id")]

    logger.info(
        "[task-dispatch] emitted %d tool_call(s): %s",
        len(tool_calls),
        ", ".join(tc.get("name", "?") for tc in tool_calls),
    )

    return {
        "messages": [response],
        "pending_tool_calls": pending,
    }

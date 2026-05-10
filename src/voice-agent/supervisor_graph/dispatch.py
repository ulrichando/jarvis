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


def _livekit_tools_to_openai_schemas(tools: list[Any]) -> list[dict]:
    """Convert livekit FunctionTool / RawFunctionTool objects to OpenAI
    tool-schema dicts so that ChatGroq.bind_tools can consume them.

    livekit FunctionTools include a RunContext parameter. Pydantic cannot
    generate a JSON schema for RunContext, so passing the raw tool object
    to bind_tools causes a schema-generation error on the first live
    TASK turn. This converter uses livekit's own schema builder
    (build_legacy_openai_schema / RawFunctionToolInfo.raw_schema) which
    already strips the RunContext parameter before emitting JSON Schema.

    Passthrough rules:
    - dict objects are passed through unchanged (pre-converted or test
      fixtures that already carry the right shape).
    - Objects whose getattr probes all return MagicMock-like values (unit
      tests) also produce a best-effort schema rather than raising.
    """
    try:
        from livekit.agents.llm.tool_context import FunctionTool as LKFunctionTool
        from livekit.agents.llm.tool_context import RawFunctionTool as LKRawFunctionTool
        from livekit.agents.llm.utils import build_legacy_openai_schema
        _livekit_available = True
    except ImportError:
        _livekit_available = False

    schemas: list[dict] = []
    for t in tools:
        # Already a dict — pass through unchanged.
        if isinstance(t, dict):
            schemas.append(t)
            continue

        # Use livekit's own schema builder when the type is known — it
        # already excludes the RunContext parameter correctly.
        if _livekit_available:
            if isinstance(t, LKRawFunctionTool):
                schemas.append({
                    "type": "function",
                    "function": t.info.raw_schema,
                })
                continue
            if isinstance(t, LKFunctionTool):
                try:
                    schemas.append(build_legacy_openai_schema(t))
                    continue
                except Exception as e:
                    logger.warning(
                        "[task-dispatch] build_legacy_openai_schema failed for %r: %s; "
                        "falling back to best-effort extraction",
                        getattr(t, "name", repr(t)), e,
                    )

        # Best-effort extraction for unknown tool shapes (mock objects,
        # future livekit versions with a different class hierarchy, etc.).
        info = getattr(t, "info", None)
        name = getattr(info, "name", None) or getattr(t, "name", "") or "tool"
        description = (
            getattr(info, "description", None)
            or getattr(t, "description", "")
            or ""
        )
        # Try several attribute paths for the parameters schema.
        params = (
            getattr(info, "arguments_dict", None)
            or getattr(info, "arguments_schema", None)
            or getattr(t, "arguments_dict", None)
            or {"type": "object", "properties": {}, "required": []}
        )
        # Guard: if params is not a real dict (e.g. MagicMock), replace
        # with an empty schema to keep the request well-formed.
        if not isinstance(params, dict):
            params = {"type": "object", "properties": {}, "required": []}
        schemas.append({
            "type": "function",
            "function": {
                "name": str(name),
                "description": str(description),
                "parameters": params,
            },
        })
    return schemas


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

    tool_schemas = _livekit_tools_to_openai_schemas(tools)

    def _try(builder, provider_name: str):
        llm = builder()
        bound = llm.bind_tools(tool_schemas, tool_choice="required")
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
    "You are JARVIS, Ulrich's voice-first system on his Linux laptop. "
    "Direct, helpful, technically grounded — peer engineer, not butler. "
    "Never use 'sir' or other honorifics. Speak in plain English; "
    "never use markdown, bullet lists, or emoji. Keep replies short "
    "for voice — one or two sentences."
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

"""When the primary task_dispatch LLM fails (Groq rate-limit, tool
malformation, etc.), the node falls back to DeepSeek with the SAME
tool_choice=required, so the fallback CANNOT confabulate completion.
This is the cure for cross-stream lies (failure mode #5)."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")


def _ai_tool(name: str, args: dict, call_id: str):
    from langchain_core.messages import AIMessage
    return AIMessage(content="", tool_calls=[{
        "name": name, "args": args, "id": call_id, "type": "tool_call",
    }])


def test_task_dispatch_falls_back_on_primary_failure():
    """Primary raises → fallback runs and emits a clean tool_call."""
    from supervisor_graph.dispatch import task_dispatch_node
    from supervisor_graph.state import initial_state

    primary = MagicMock()
    primary.bind_tools = MagicMock(return_value=primary)
    primary.invoke = MagicMock(side_effect=RuntimeError("Failed to call a function"))

    fallback = MagicMock()
    fallback.bind_tools = MagicMock(return_value=fallback)
    fallback.invoke = MagicMock(return_value=_ai_tool(
        "transfer_to_browser", {"request": "open"}, "call_fb",
    ))

    state = initial_state(user_query="open a tab")

    with patch(
        "supervisor_graph.dispatch._build_task_llm",
        return_value=primary,
    ), patch(
        "supervisor_graph.dispatch._build_task_fallback_llm",
        return_value=fallback,
    ):
        out = task_dispatch_node(
            state, tools=[MagicMock(name="transfer_to_browser")],
        )

    assert out["pending_tool_calls"] == ["call_fb"]
    assert out["failed_providers"] == ["groq"]
    # Fallback was invoked, so it MUST have used tool_choice=required.
    bind_call = fallback.bind_tools.call_args
    assert bind_call.kwargs.get("tool_choice") == "required" \
        or (len(bind_call.args) >= 2 and bind_call.args[1] == "required")


def test_task_dispatch_re_raises_when_both_fail():
    """If primary and fallback both raise, propagate so the framework
    can show a graceful error to the user."""
    from supervisor_graph.dispatch import task_dispatch_node
    from supervisor_graph.state import initial_state

    primary = MagicMock()
    primary.bind_tools = MagicMock(return_value=primary)
    primary.invoke = MagicMock(side_effect=RuntimeError("groq down"))
    fallback = MagicMock()
    fallback.bind_tools = MagicMock(return_value=fallback)
    fallback.invoke = MagicMock(side_effect=RuntimeError("deepseek down"))

    state = initial_state(user_query="open a tab")

    import pytest
    with patch(
        "supervisor_graph.dispatch._build_task_llm",
        return_value=primary,
    ), patch(
        "supervisor_graph.dispatch._build_task_fallback_llm",
        return_value=fallback,
    ), pytest.raises(RuntimeError):
        task_dispatch_node(
            state, tools=[MagicMock(name="transfer_to_browser")],
        )

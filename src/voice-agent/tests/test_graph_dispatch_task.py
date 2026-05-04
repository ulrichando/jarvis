"""task_dispatch_node binds the supervisor's tool list to a Groq LLM
with tool_choice='required' so the model CANNOT emit free-form
completion text. The output AIMessage must always have tool_calls
and empty content. After emission, pending_tool_calls is populated."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def _fake_ai_message_with_tool_call(name: str, args: dict, call_id: str):
    """Build a LangChain AIMessage with a tool_call. The framework
    accepts dict-shape tool_calls and Pydantic ToolCall objects; we use
    the dict shape because that's what ChatGroq returns."""
    from langchain_core.messages import AIMessage
    return AIMessage(
        content="",
        tool_calls=[{
            "name": name,
            "args": args,
            "id": call_id,
            "type": "tool_call",
        }],
    )


def test_task_dispatch_emits_tool_call_and_marks_pending():
    from supervisor_graph.dispatch import task_dispatch_node
    from supervisor_graph.state import initial_state

    fake_msg = _fake_ai_message_with_tool_call(
        "transfer_to_browser",
        {"request": "open a new tab"},
        "call_abc123",
    )
    fake_llm = MagicMock()
    # Configure both direct invoke and the chainable bind_tools path
    fake_llm.invoke = MagicMock(return_value=fake_msg)
    fake_llm.bind_tools.return_value = fake_llm

    state = initial_state(user_query="open a new tab")

    with patch(
        "supervisor_graph.dispatch._build_task_llm",
        return_value=fake_llm,
    ):
        out = task_dispatch_node(state, tools=[MagicMock(name="transfer_to_browser")])

    # Must populate pending_tool_calls with the call_id.
    assert out["pending_tool_calls"] == ["call_abc123"]
    # Must append the AIMessage to messages.
    assert len(out["messages"]) == 1
    assert out["messages"][0].tool_calls[0]["name"] == "transfer_to_browser"
    # Must NOT emit free-form content (tool_choice=required guarantees this
    # at the API level; assert it for our recovery path).
    assert (out["messages"][0].content or "") == ""


def test_task_dispatch_uses_tool_choice_required():
    """Verify the LLM is invoked with tool_choice='required'. This is
    the structural lever that prevents the lying-supervisor failure
    mode at the API layer (Groq won't return content alongside the
    tool call)."""
    from supervisor_graph.dispatch import task_dispatch_node
    from supervisor_graph.state import initial_state

    captured_kwargs = {}

    class _RecordingLLM:
        def bind_tools(self, tools, tool_choice=None):
            captured_kwargs["tool_choice"] = tool_choice
            captured_kwargs["tools"] = tools
            return self

        def invoke(self, messages):
            return _fake_ai_message_with_tool_call(
                "transfer_to_browser", {"request": "x"}, "call_x"
            )

    state = initial_state(user_query="open a tab")

    with patch(
        "supervisor_graph.dispatch._build_task_llm",
        return_value=_RecordingLLM(),
    ):
        task_dispatch_node(state, tools=[MagicMock(name="transfer_to_browser")])

    assert captured_kwargs.get("tool_choice") == "required", (
        f"expected tool_choice='required', got {captured_kwargs.get('tool_choice')!r}"
    )

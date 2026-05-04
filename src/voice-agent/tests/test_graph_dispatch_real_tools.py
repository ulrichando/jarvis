"""Verify task_dispatch_node accepts real livekit FunctionTool
objects. The MagicMock-based tests can't catch the JSON-schema
failure that happens when bind_tools() tries to introspect a livekit
FunctionTool's RunContext parameter."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")


def test_task_dispatch_accepts_real_specialist_tools():
    """Build the actual production tool list and verify
    task_dispatch_node can process it without a JSON-schema error.

    The LLM is mocked, but the tools list and the conversion path are
    real."""
    from supervisor_graph.dispatch import (
        task_dispatch_node, _livekit_tools_to_openai_schemas,
    )
    from supervisor_graph.state import initial_state
    from langchain_core.messages import AIMessage

    # Skip the test if the registry isn't loadable (specialist
    # registration sometimes requires a live LiveKit context).
    try:
        from specialists.agent import build_all_transfer_tools
        tools = build_all_transfer_tools()
    except Exception as e:
        import pytest
        pytest.skip(f"specialist registry unavailable: {e}")

    if not tools:
        import pytest
        pytest.skip("no specialists registered")

    # 1. The conversion alone shouldn't raise.
    schemas = _livekit_tools_to_openai_schemas(tools)
    assert len(schemas) == len(tools)
    for s in schemas:
        assert s["type"] == "function"
        assert "name" in s["function"]
        assert "parameters" in s["function"]

    # 2. task_dispatch_node end-to-end with these tools (mocked LLM).
    fake_response = AIMessage(content="", tool_calls=[{
        "name": schemas[0]["function"]["name"],
        "args": {"request": "test"},
        "id": "call_xyz",
        "type": "tool_call",
    }])
    fake_llm = MagicMock()
    fake_llm.bind_tools = MagicMock(return_value=fake_llm)
    fake_llm.invoke = MagicMock(return_value=fake_response)

    state = initial_state(user_query="open a tab")
    with patch(
        "supervisor_graph.dispatch._build_task_llm",
        return_value=fake_llm,
    ):
        out = task_dispatch_node(state, tools=tools)
    assert out["pending_tool_calls"] == ["call_xyz"]
    # bind_tools must have been called with the SCHEMA dicts, not the
    # raw FunctionTool objects.
    call_args = fake_llm.bind_tools.call_args
    passed_tools = call_args.args[0] if call_args.args else call_args.kwargs.get("tools")
    assert isinstance(passed_tools, list)
    assert all(isinstance(t, dict) for t in passed_tools), (
        "expected dict schemas after _livekit_tools_to_openai_schemas; "
        f"got {[type(t).__name__ for t in passed_tools]}"
    )

"""BANTER / REASONING / EMOTIONAL nodes emit content with no tools.
No tools = no malformation surface = no breaker thrash."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def _ai(content: str):
    from langchain_core.messages import AIMessage
    return AIMessage(content=content)


def test_banter_speak_emits_content():
    from supervisor_graph.dispatch import banter_speak_node
    from supervisor_graph.state import initial_state

    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(return_value=_ai("Just fine, sir."))

    with patch(
        "supervisor_graph.dispatch._build_banter_llm",
        return_value=fake_llm,
    ):
        out = banter_speak_node(initial_state(user_query="how are you"))

    assert len(out["messages"]) == 1
    assert "fine" in out["messages"][0].content.lower()
    # No tool calls — banter speaks freely.
    assert not (out["messages"][0].tool_calls or [])


def test_reasoning_speak_emits_content():
    from supervisor_graph.dispatch import reasoning_speak_node
    from supervisor_graph.state import initial_state

    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(
        return_value=_ai("Recursion is a function calling itself...")
    )
    with patch(
        "supervisor_graph.dispatch._build_reasoning_llm",
        return_value=fake_llm,
    ):
        out = reasoning_speak_node(initial_state(
            user_query="explain recursion"
        ))
    assert len(out["messages"]) == 1
    assert "recursion" in out["messages"][0].content.lower()


def test_emotional_speak_emits_content():
    from supervisor_graph.dispatch import emotional_speak_node
    from supervisor_graph.state import initial_state

    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(
        return_value=_ai("That sounds rough, sir. I'm here.")
    )
    with patch(
        "supervisor_graph.dispatch._build_emotional_llm",
        return_value=fake_llm,
    ):
        out = emotional_speak_node(initial_state(user_query="I'm tired"))
    assert len(out["messages"]) == 1
    assert "sir" in out["messages"][0].content.lower()

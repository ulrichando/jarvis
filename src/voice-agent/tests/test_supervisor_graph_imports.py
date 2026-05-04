"""Verify the supervisor_graph package and its required dependencies
import cleanly. If LangGraph or langchain-core moves a symbol, this
test surfaces it before any other test runs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_supervisor_graph_package_imports():
    import supervisor_graph  # noqa: F401


def test_required_langgraph_symbols_available():
    from langgraph.graph import END, START, StateGraph
    from langgraph.prebuilt import ToolNode
    from langgraph.checkpoint.sqlite import SqliteSaver
    assert START is not None
    assert END is not None
    assert StateGraph is not None
    assert ToolNode is not None
    assert SqliteSaver is not None


def test_required_langchain_symbols_available():
    from langchain_core.messages import (
        AIMessage, HumanMessage, ToolMessage, SystemMessage,
    )
    assert all(c is not None for c in (
        AIMessage, HumanMessage, ToolMessage, SystemMessage,
    ))

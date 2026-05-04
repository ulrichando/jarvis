"""The adapter exposes the compiled graph behind LiveKit's LLM
interface so AgentSession can drop it in unchanged. Drives the full
async-with / async-for protocol the framework uses."""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_adapter_constructs_with_specialist_tools():
    from supervisor_graph.llm_adapter import JarvisSupervisorGraphLLM
    llm = JarvisSupervisorGraphLLM(specialist_tools=[])
    assert llm is not None


def test_adapter_chat_streams_banter_response():
    """End-to-end: invoke chat() and read its stream; verify that
    the banter content surfaces as ChatChunk content deltas."""
    from supervisor_graph.llm_adapter import JarvisSupervisorGraphLLM
    from langchain_core.messages import AIMessage

    fake_classifier = MagicMock()
    fake_classifier.invoke = MagicMock(
        return_value=MagicMock(content='{"route": "BANTER", "confidence": 0.9}')
    )
    fake_banter = MagicMock()
    fake_banter.invoke = MagicMock(return_value=AIMessage(content="Hello, sir."))

    with patch(
        "supervisor_graph.classify._build_classifier_chain",
        return_value=fake_classifier,
    ), patch(
        "supervisor_graph.dispatch._build_banter_llm",
        return_value=fake_banter,
    ):
        from livekit.agents import llm as agents_llm

        # Build a fake chat_ctx with a single user turn.
        chat_ctx = agents_llm.ChatContext()
        chat_ctx.add_message(role="user", content="hi")

        adapter = JarvisSupervisorGraphLLM(specialist_tools=[])
        stream = adapter.chat(chat_ctx=chat_ctx)

        async def collect():
            chunks = []
            async with stream:
                async for chunk in stream:
                    chunks.append(chunk)
            return chunks

        chunks = _run(collect())

    contents = "".join(
        (c.delta.content or "")
        for c in chunks
        if c.delta is not None
    )
    assert "hello" in contents.lower()


def test_adapter_chat_surfaces_handoff_as_tool_call_chunk():
    """When the graph state ends with a transfer_to_* AIMessage,
    the adapter must emit a ChatChunk whose delta has tool_calls
    populated — so AgentSession dispatches the tool through the
    existing RegistrySpecialist path. This is the Phase 6 wiring
    that makes graph-supervised TASK turns actually work."""
    from supervisor_graph.llm_adapter import JarvisSupervisorGraphLLM
    from langchain_core.messages import AIMessage

    fake_task_llm_response = AIMessage(content="", tool_calls=[{
        "name": "transfer_to_browser",
        "args": {"request": "open a tab"},
        "id": "call_xyz",
        "type": "tool_call",
    }])
    fake_task_llm = MagicMock()
    fake_task_llm.bind_tools = MagicMock(return_value=fake_task_llm)
    fake_task_llm.invoke = MagicMock(return_value=fake_task_llm_response)

    fake_specialist_tool = MagicMock()
    fake_specialist_tool.name = "transfer_to_browser"

    with patch(
        "supervisor_graph.dispatch._build_task_llm",
        return_value=fake_task_llm,
    ):
        from livekit.agents import llm as agents_llm

        chat_ctx = agents_llm.ChatContext()
        chat_ctx.add_message(role="user", content="open a tab")

        adapter = JarvisSupervisorGraphLLM(
            specialist_tools=[fake_specialist_tool],
        )
        stream = adapter.chat(chat_ctx=chat_ctx)

        async def collect():
            chunks = []
            async with stream:
                async for chunk in stream:
                    chunks.append(chunk)
            return chunks

        chunks = _run(collect())

    # We expect AT LEAST one chunk with tool_calls populated.
    has_tool_call_chunk = any(
        c.delta is not None and (c.delta.tool_calls or [])
        for c in chunks
    )
    assert has_tool_call_chunk, (
        f"expected at least one ChatChunk with tool_calls populated; "
        f"got chunks: {[(c.delta and c.delta.content, c.delta and c.delta.tool_calls) for c in chunks]}"
    )
    # And at least one content chunk for the filler.
    has_content_chunk = any(
        c.delta is not None and c.delta.content
        for c in chunks
    )
    assert has_content_chunk, "expected the filler content chunk"

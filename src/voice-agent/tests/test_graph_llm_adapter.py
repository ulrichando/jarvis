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

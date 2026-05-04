"""LangGraph-as-LLM adapter for LiveKit AgentSession.

`JarvisSupervisorGraphLLM` extends `livekit.agents.llm.LLM`. Its
`chat()` runs the compiled supervisor graph and streams the resulting
assistant content back as `ChatChunk` deltas, which AgentSession
forwards to TTS just like any other LLM.

Why this works without changing AgentSession:

  - The framework drives turn timing (STT -> LLM.chat -> TTS).
  - The "LLM" is just an object with a `chat()` returning an async
    iterable of ChatChunk. The graph satisfies that contract: run
    the graph synchronously inside chat(), then yield one chunk per
    new AssistantMessage's content split across messages.

Trade-off: we don't stream tokens (the graph runs to completion
before any chunk is yielded). For voice that is fine — the audio
plays while the graph runs; users feel snappy because the FILLER
chunk goes out first.

Implementation note on the LiveKit contract
-------------------------------------------
LiveKit 1.5.x requires `chat()` to return an `LLMStream` by the
abstract base class signature. However, `LLMStream.__init__` calls
`asyncio.create_task()` which requires a running event loop at
construction time. Since `chat()` is called synchronously by the
test harness (and often by the framework before the event loop is
entered), we return a plain async-iterable object that satisfies
the async-with / async-for / aclose protocol instead of subclassing
`LLMStream`. This matches the pattern used by `_BreakeredLLMStream`
in jarvis_agent.py and is compatible with AgentSession's actual usage.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from livekit.agents import llm as agents_llm

from .graph import build_graph
from .state import initial_state

logger = logging.getLogger("supervisor_graph.llm_adapter")


# ---------------------------------------------------------------------------
# Context conversion helpers
# ---------------------------------------------------------------------------

def _ctx_to_lc_messages(chat_ctx: agents_llm.ChatContext) -> list:
    """Convert LiveKit ChatContext items to LangChain BaseMessages.

    Defensive about both dict and Pydantic shapes; LiveKit versions
    have shifted on this boundary. Only ChatMessage items are walked;
    FunctionCall / FunctionCallOutput items are skipped (the graph
    manages tool execution itself).
    """
    out = []
    for item in getattr(chat_ctx, "items", []) or []:
        # Skip non-message items (FunctionCall, FunctionCallOutput, etc.)
        item_type = getattr(item, "type", None)
        if item_type != "message":
            continue

        role = getattr(item, "role", None)
        content = getattr(item, "content", "") or ""
        # content may be a list of ChatContent (str | ImageContent | AudioContent)
        if isinstance(content, list):
            content = " ".join(
                c if isinstance(c, str) else getattr(c, "text", "") or ""
                for c in content
            )

        if role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
        elif role == "system":
            out.append(SystemMessage(content=content))
        elif role == "tool":
            out.append(ToolMessage(
                content=content,
                tool_call_id=getattr(item, "tool_call_id", "") or "?",
            ))
    return out


def _ai_messages_to_chunks(ai_messages: list) -> list[agents_llm.ChatChunk]:
    """Convert AIMessages to ChatChunks for the LiveKit stream.

    Tool calls are NOT surfaced — the graph already executed them
    internally; TTS only needs the assistant content.
    """
    chunks = []
    for m in ai_messages:
        content = getattr(m, "content", "") or ""
        if not content:
            continue
        chunks.append(agents_llm.ChatChunk(
            id=f"graph_{uuid.uuid4().hex[:8]}",
            delta=agents_llm.ChoiceDelta(role="assistant", content=content),
        ))
    return chunks


# ---------------------------------------------------------------------------
# Plain async-iterable stream (no LLMStream subclass)
# ---------------------------------------------------------------------------

class _GraphLLMStream:
    """Minimal async iterator yielding ChatChunk deltas from the graph
    output. LiveKit's contract: support `async with`, `async for`,
    `aclose`. See test_graph_llm_adapter for the exercised contract.

    This is intentionally NOT a subclass of `livekit.agents.llm.LLMStream`
    because that ABC calls asyncio.create_task() in __init__, which
    requires a running event loop at construction time — breaking the
    synchronous `chat()` call pattern. The framework only cares about
    the async-iterable protocol, which this class satisfies.
    """

    def __init__(
        self,
        *,
        chat_ctx: agents_llm.ChatContext,
        graph,
    ):
        self._chat_ctx = chat_ctx
        self._graph = graph
        self._chunks: list[agents_llm.ChatChunk] | None = None
        self._idx = 0
        self._closed = False

    def _build_chunks(self) -> list[agents_llm.ChatChunk]:
        """Invoke the graph synchronously and return ChatChunks."""
        lc_messages = _ctx_to_lc_messages(self._chat_ctx)

        user_query = ""
        history: list = []
        for m in reversed(lc_messages):
            if isinstance(m, HumanMessage):
                user_query = m.content
                history = lc_messages[:lc_messages.index(m)]
                break

        state = initial_state(user_query=user_query)
        state["messages"] = history

        try:
            final_state = self._graph.invoke(state)
        except Exception as e:
            logger.exception("[graph] invoke failed: %s", e)
            final_state = {
                "messages": [AIMessage(
                    content="My apologies, sir — something went wrong on my end."
                )]
            }

        # Pick out AIMessages appended during the run (past history).
        appended = (final_state.get("messages") or [])[len(history):]
        ai_messages = [m for m in appended if isinstance(m, AIMessage)]

        chunks = _ai_messages_to_chunks(ai_messages)
        if not chunks:
            chunks = [agents_llm.ChatChunk(
                id=f"graph_empty_{uuid.uuid4().hex[:8]}",
                delta=agents_llm.ChoiceDelta(role="assistant", content=""),
            )]
        return chunks

    def __aiter__(self):
        return self

    async def __anext__(self) -> agents_llm.ChatChunk:
        if self._closed:
            raise StopAsyncIteration

        # Lazy: run the graph on first iteration.
        if self._chunks is None:
            loop = asyncio.get_event_loop()
            self._chunks = await loop.run_in_executor(None, self._build_chunks)

        if self._idx >= len(self._chunks):
            raise StopAsyncIteration

        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk

    async def aclose(self) -> None:
        self._closed = True

    async def __aenter__(self) -> "_GraphLLMStream":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()


# ---------------------------------------------------------------------------
# LLM adapter
# ---------------------------------------------------------------------------

class JarvisSupervisorGraphLLM(agents_llm.LLM):
    """Wraps a compiled supervisor StateGraph behind the LiveKit LLM
    contract. Construct once at agent startup; each user turn calls
    `chat()` which runs the graph fresh."""

    def __init__(self, *, specialist_tools: list[Any]):
        super().__init__()
        self._graph = build_graph(specialist_tools=specialist_tools)
        self._specialist_tools = specialist_tools

    @property
    def model(self) -> str:
        return "jarvis-supervisor-graph"

    @property
    def provider(self) -> str:
        return "jarvis"

    def chat(
        self,
        *,
        chat_ctx: agents_llm.ChatContext,
        tools: list | None = None,
        **kwargs,
    ) -> _GraphLLMStream:  # type: ignore[override]
        """LiveKit calls this for each turn. Tools is the
        AgentSession's tool list — we ignore it because our graph has
        its own tool list bound at compile time."""
        return _GraphLLMStream(
            chat_ctx=chat_ctx,
            graph=self._graph,
        )

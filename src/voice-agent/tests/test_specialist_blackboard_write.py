"""When task_done fires, the specialist must write a ToolResult to
the blackboard so the grounding gate can later validate claims."""
import asyncio
import os
import sys
import time
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


def test_task_done_writes_to_blackboard_when_v2_enabled():
    from livekit.agents.llm import FunctionCall, ChatContext
    from specialists.agent import RegistrySpecialist
    from specialists.registry import SpecialistSpec

    spec = SpecialistSpec(
        name="browser", transfer_tool="transfer_to_browser",
        when_to_use="x", instructions="x", tool_factory=lambda: [],
        ack_phrase="ok", max_history_items=4, enabled=True,
    )
    supervisor = MagicMock()
    specialist = RegistrySpecialist(spec=spec, supervisor=supervisor)
    specialist._handoff_start_idx = 0
    specialist._chat_ctx = ChatContext(items=[
        FunctionCall(call_id="call_abc", arguments="{}", name="ext_new_tab"),
        FunctionCall(call_id="call_done", arguments="{}", name="task_done"),
    ])

    written = []

    class _StubClient:
        def write_tool_result(self, r):
            written.append(r)

    ctx = MagicMock()
    with patch.dict(os.environ, {"JARVIS_BLACKBOARD": "1"}), \
         patch("blackboard.client.BlackboardClient", return_value=_StubClient()):
        _run(specialist.task_done(ctx, "Tab opened, sir."))

    assert len(written) == 1, f"expected one ToolResult write, got {len(written)}"
    r = written[0]
    assert r.tool == "browser_task_done"  # naming convention
    assert r.ok is True
    assert "tab opened" in r.result.lower()


def test_task_done_does_NOT_write_when_v2_disabled():
    """Default behaviour: env var unset → no blackboard write."""
    from livekit.agents.llm import FunctionCall, ChatContext
    from specialists.agent import RegistrySpecialist
    from specialists.registry import SpecialistSpec

    spec = SpecialistSpec(
        name="browser", transfer_tool="transfer_to_browser",
        when_to_use="x", instructions="x", tool_factory=lambda: [],
        ack_phrase="ok", max_history_items=4, enabled=True,
    )
    supervisor = MagicMock()
    specialist = RegistrySpecialist(spec=spec, supervisor=supervisor)
    specialist._handoff_start_idx = 1
    specialist._chat_ctx = ChatContext(items=[
        FunctionCall(call_id="call_abc", arguments="{}", name="ext_new_tab"),
    ])

    written = []

    class _StubClient:
        def write_tool_result(self, r):
            written.append(r)

    ctx = MagicMock()
    with patch.dict(os.environ, {"JARVIS_BLACKBOARD": "0"}), \
         patch("blackboard.client.BlackboardClient", return_value=_StubClient()):
        _run(specialist.task_done(ctx, "Tab opened, sir."))

    assert len(written) == 0, "v2 disabled — must not write to blackboard"

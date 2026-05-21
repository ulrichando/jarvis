"""T13 — L1 synthesis via session stash.

Pycall stashes parsed call info on session._jarvis_text_shape_pending.

Note: subagent gate drain tests removed — subagents.agent was removed in
the Hermes teardown.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_pycall_stashes_call_info_on_session_when_chat_ctx_unreachable():
    """When pycall detects a leak, it stashes (tool_name, raw_args)
    on session._jarvis_text_shape_pending."""
    from livekit.agents.inference import llm as inf_llm
    import sanitizers.pycall as pycall_sanitizer
    pycall_sanitizer._PYCALL_STATE.clear()
    pycall_sanitizer.install()

    session = SimpleNamespace()
    self_mock = SimpleNamespace(
        _tool_call_id=None, _fnc_name=None, _fnc_raw_arguments=None,
        _tool_extra=None, _tool_index=None,
        _tool_ctx=SimpleNamespace(
            function_tools={"launch_app": object(), "task_done": object()}
        ),
        _event_ch=SimpleNamespace(send_nowait=lambda c: None),
        _chat_ctx=None,  # explicitly unreachable
        _session=session,
    )
    import threading
    thinking = threading.Event()

    chunks = ['launch_app("google-chrome")']
    for content in chunks:
        delta = SimpleNamespace(content=content, tool_calls=None,
                                reasoning_content=None)
        c = SimpleNamespace(delta=delta, finish_reason=None)
        inf_llm.LLMStream._parse_choice(self_mock, "resp_stash", c, thinking)

    pending = getattr(session, "_jarvis_text_shape_pending", None)
    assert pending is not None
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "launch_app"
    assert "google-chrome" in pending[0]["raw_args"]



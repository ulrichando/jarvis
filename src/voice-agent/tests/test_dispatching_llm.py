import asyncio
from unittest.mock import MagicMock

from pipeline.dispatching_llm import DispatchingLLM


def _stub_inner(label: str):
    # spec=[] makes MagicMock not auto-generate attributes — getattr returns
    # the default for missing attrs instead of a fresh MagicMock. This mirrors
    # the real livekit plugin LLM classes where `label` is a read-only property
    # so production code uses `_jarvis_label` to avoid the collision.
    inner = MagicMock(name=f"inner-{label}", spec=["_jarvis_label", "label"])
    inner._jarvis_label = label
    inner.label = label
    return inner


def test_dispatcher_returns_inner_for_route():
    inners = {
        "BANTER":     _stub_inner("haiku"),
        "TASK":       _stub_inner("haiku"),
        "REASONING":  _stub_inner("sonnet"),
        "EMOTIONAL":  _stub_inner("deepseek"),
    }
    d = DispatchingLLM(inners=inners, fallback=inners["TASK"])
    assert d.pick("BANTER").label == "haiku"
    assert d.pick("REASONING").label == "sonnet"
    assert d.pick("EMOTIONAL").label == "deepseek"


def test_dispatcher_unknown_route_uses_fallback():
    inners = {"TASK": _stub_inner("haiku")}
    d = DispatchingLLM(inners=inners, fallback=inners["TASK"])
    assert d.pick("BOGUS").label == "haiku"


def test_dispatcher_records_route_for_telemetry():
    inners = {
        "TASK": _stub_inner("haiku"),
        "REASONING": _stub_inner("sonnet"),
    }
    d = DispatchingLLM(inners=inners, fallback=inners["TASK"])
    d.pick("REASONING")
    assert d.last_route == "REASONING"
    assert d.last_llm_label == "sonnet"

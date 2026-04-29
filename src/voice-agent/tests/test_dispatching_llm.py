import asyncio
from unittest.mock import MagicMock

from dispatching_llm import DispatchingLLM


def _stub_inner(label: str):
    inner = MagicMock(name=f"inner-{label}")
    inner.label = label
    return inner


def test_dispatcher_returns_inner_for_route():
    inners = {
        "BANTER":     _stub_inner("groq"),
        "TASK":       _stub_inner("groq"),
        "REASONING":  _stub_inner("dsr"),
        "EMOTIONAL":  _stub_inner("haiku"),
    }
    d = DispatchingLLM(inners=inners, fallback=inners["TASK"])
    assert d.pick("BANTER").label == "groq"
    assert d.pick("REASONING").label == "dsr"
    assert d.pick("EMOTIONAL").label == "haiku"


def test_dispatcher_unknown_route_uses_fallback():
    inners = {"TASK": _stub_inner("groq")}
    d = DispatchingLLM(inners=inners, fallback=inners["TASK"])
    assert d.pick("BOGUS").label == "groq"


def test_dispatcher_records_route_for_telemetry():
    inners = {
        "TASK": _stub_inner("groq"),
        "REASONING": _stub_inner("dsr"),
    }
    d = DispatchingLLM(inners=inners, fallback=inners["TASK"])
    d.pick("REASONING")
    assert d.last_route == "REASONING"
    assert d.last_llm_label == "dsr"

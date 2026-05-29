"""Source-level guardrails — every dispatch_tts.pick(...) /
tts_dispatcher.pick(...) call must include a `lang=` kwarg sourced
from session._jarvis_lang_ctx.get().

These tests don't try to construct a real LiveKit session. They
inspect the source of pipeline/turn_dispatcher.py and pipeline/turn_graph.py
directly to prevent a future refactor from silently dropping the
lang= kwarg."""
from __future__ import annotations

import inspect
import re


def test_turn_dispatcher_passes_lang_to_all_pick_calls():
    """Every dispatch_tts.pick(...) in turn_dispatcher.py must include
    a lang= kwarg."""
    from pipeline import turn_dispatcher

    src = inspect.getsource(turn_dispatcher)
    pick_calls = re.findall(r"dispatch_tts\.pick\([^)]*\)", src)
    assert pick_calls, (
        "no dispatch_tts.pick(...) calls found — has the module been "
        "refactored?"
    )
    for call in pick_calls:
        assert "lang=" in call, (
            f"dispatch_tts.pick call missing lang= kwarg: {call}"
        )


def test_turn_dispatcher_lang_sources_from_jarvis_lang_ctx():
    """The lang= kwarg should read from session._jarvis_lang_ctx.get()
    (the prefix-convention-compliant attribute). This guards against a
    future change to a different attribute path."""
    from pipeline import turn_dispatcher

    src = inspect.getsource(turn_dispatcher)
    pick_calls = re.findall(r"dispatch_tts\.pick\([^)]*\)", src)
    for call in pick_calls:
        assert "_jarvis_lang_ctx" in call, (
            f"dispatch_tts.pick call's lang= should source from "
            f"session._jarvis_lang_ctx, got: {call}"
        )


def test_turn_graph_passes_lang_to_pick_call():
    """tts_dispatcher.pick(...) in turn_graph.py must include lang="""
    from pipeline import turn_graph

    src = inspect.getsource(turn_graph)
    pick_calls = re.findall(r"tts_dispatcher\.pick\([^)]*\)", src)
    assert pick_calls, (
        "no tts_dispatcher.pick(...) call found in turn_graph — has "
        "the module been refactored?"
    )
    for call in pick_calls:
        assert "lang=" in call, (
            f"tts_dispatcher.pick call missing lang= kwarg: {call}"
        )
        assert "_jarvis_lang_ctx" in call, (
            f"tts_dispatcher.pick call's lang= should source from "
            f"session._jarvis_lang_ctx, got: {call}"
        )

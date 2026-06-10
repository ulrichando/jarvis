"""Tests for turn_graph.py — the LangGraph dispatcher.

Mocks the LiveKit session and the LangChain classifier so we exercise
graph topology + node logic without livekit / network. Asserts the
key invariants:

1. BANTER fast-path skips the classifier.
2. Non-banter turns invoke the classifier and respect its route.
3. The route swap calls dispatcher.pick(route) for both LLM and TTS.
4. The prefix injection mutates the latest user message correctly.
5. Per-route interrupt tuning lands.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.turn_graph import build_turn_graph


def _mk_session(prior_user_text: str = "hey there") -> SimpleNamespace:
    """Build a minimal session double exposing the attrs the nodes touch."""
    from pipeline.lang_context import LangContext
    user_msg = SimpleNamespace(role="user", content=prior_user_text)
    chat_ctx = SimpleNamespace(messages=[user_msg])
    options = SimpleNamespace(interruption={"min_words": 2, "min_duration": 0.4})
    return SimpleNamespace(
        # livekit 1.5: the live ctx is reached via current_agent.chat_ctx
        # (what session_chat_messages reads), NOT session.chat_ctx (which
        # raises AttributeError on a real AgentSession — see
        # test_chat_ctx_session_messages). Point both at the SAME object so
        # the node's in-place prefix mutation is visible to the assertions.
        current_agent=SimpleNamespace(chat_ctx=chat_ctx),
        chat_ctx=chat_ctx,
        options=options,
        _llm=None, _tts=None,
        _jarvis_baseline_wpm=140.0,
        _jarvis_session_start=None,
        _jarvis_lang_ctx=LangContext(),
    )


def _mk_dispatcher(label: str = "groq:llama-X"):
    """A DispatchingLLM/TTS double — supports `pick(route)` returning a
    distinct object per route so the test can assert which inner won."""
    inners = {
        "BANTER":    SimpleNamespace(_jarvis_label=f"{label}-banter"),
        "TASK":      SimpleNamespace(_jarvis_label=f"{label}-task"),
        "REASONING": SimpleNamespace(_jarvis_label=f"{label}-reasoning"),
        "EMOTIONAL": SimpleNamespace(_jarvis_label=f"{label}-emotional"),
    }
    return SimpleNamespace(
        pick=lambda route, **kw: inners.get(route, inners["TASK"]),
        _inners=inners,
    )


def _mk_tts_dispatcher():
    """TTS dispatcher double — voice_id distinguishes the inners."""
    inners = {
        "BANTER":    SimpleNamespace(voice_id="austin"),
        "TASK":      SimpleNamespace(voice_id="troy"),
        "REASONING": SimpleNamespace(voice_id="troy"),
        "EMOTIONAL": SimpleNamespace(voice_id="daniel"),
    }
    return SimpleNamespace(pick=lambda route, **kw: inners.get(route, inners["TASK"]))


def _mk_classifier(reply: str = "TASK"):
    """LangChain ChatModel double exposing `ainvoke(prompt) → AIMessage`."""
    msg = SimpleNamespace(content=reply)
    cm = MagicMock()
    cm.ainvoke = AsyncMock(return_value=msg)
    return cm


def _invoke(g, state, *, session, dispatcher, tts_dispatcher, classifier, history=None):
    """Sync helper: wrap g.ainvoke(...) in asyncio.run() so tests don't
    need pytest-asyncio (which is in strict mode here and cranky)."""
    return asyncio.run(g.ainvoke(
        state,
        config={"configurable": {
            "session": session,
            "dispatcher": dispatcher,
            "tts_dispatcher": tts_dispatcher,
            "classifier": classifier,
            "history": history or [],
        }},
    ))


# ── Tests ─────────────────────────────────────────────────────────────


def test_fast_path_skips_classifier_and_swaps_banter():
    g = build_turn_graph()
    session = _mk_session("hey jarvis")
    # Unseed baseline so the speech-rate emotion path is skipped —
    # isolates this test to "fast-path swap" without picking up an
    # incidental urgent/sad refinement from the rate signal.
    session._jarvis_baseline_wpm = 0.0
    dispatcher, tts_dispatcher = _mk_dispatcher(), _mk_tts_dispatcher()
    classifier = _mk_classifier(reply="TASK")  # would be wrong if invoked

    result = _invoke(
        g, {"transcript": "hey jarvis", "fast_path": True, "duration_s": 0.5},
        session=session, dispatcher=dispatcher,
        tts_dispatcher=tts_dispatcher, classifier=classifier,
    )

    assert result["route"] == "BANTER"
    assert result["classifier_skipped"] is True
    classifier.ainvoke.assert_not_called()
    assert session._llm is dispatcher._inners["BANTER"]
    # Per-turn model label is stamped on the SESSION (turn-local) so
    # telemetry doesn't read the racy shared dispatcher field.
    assert session._jarvis_llm_label == "groq:llama-X-banter"
    # BANTER neutral: route base (0, 0.3) + neutral overlay (0, 0) = (0, 0.3).
    # All routes moved to min_words=0 on 2026-05-18 for VAD-only barge-in.
    assert session.options.interruption == {"min_words": 0, "min_duration": 0.3}


def test_fast_path_applies_urgent_emotion_overlay():
    """Phase-7: BANTER fast-path with urgent speech (high WPM relative
    to baseline) should snap interrupts down. BANTER base (0, 0.3) +
    urgent overlay (-1, -0.1) → (-1, 0.2) pre-floor; floors clamp to
    (0, 0.2). Floor on min_words relaxed from 1 → 0 on 2026-05-18 for
    VAD-only barge-in."""
    g = build_turn_graph()
    session = _mk_session("hey jarvis")
    dispatcher, tts_dispatcher = _mk_dispatcher(), _mk_tts_dispatcher()
    classifier = _mk_classifier(reply="TASK")

    # 2 words / 0.5s → 240 wpm vs 140 baseline → urgent
    _invoke(
        g, {"transcript": "hey jarvis", "fast_path": True, "duration_s": 0.5},
        session=session, dispatcher=dispatcher,
        tts_dispatcher=tts_dispatcher, classifier=classifier,
    )
    assert session.options.interruption["min_words"] == 0   # floor
    assert session.options.interruption["min_duration"] == 0.2  # floor


def test_non_fast_path_runs_classifier_and_picks_its_route():
    g = build_turn_graph()
    session = _mk_session("explain how http works")
    dispatcher, tts_dispatcher = _mk_dispatcher(), _mk_tts_dispatcher()
    classifier = _mk_classifier(reply="REASONING")

    result = _invoke(
        g, {"transcript": "explain how http works", "fast_path": False, "duration_s": 2.0},
        session=session, dispatcher=dispatcher,
        tts_dispatcher=tts_dispatcher, classifier=classifier,
    )

    assert result["route"] == "REASONING"
    assert result.get("classifier_skipped") is False
    classifier.ainvoke.assert_called_once()
    assert session._llm is dispatcher._inners["REASONING"]
    assert session._jarvis_llm_label == "groq:llama-X-reasoning"
    # REASONING base (0, 0.5) + neutral (0, 0) — see _ROUTE_BASE.
    assert session.options.interruption == {"min_words": 0, "min_duration": 0.5}


def test_session_label_is_turn_local_no_stale_banter_leak():
    """Regression for the 2026-05-20 mis-diagnosis: a TASK turn that
    followed a BANTER turn showed `llm_used=groq:llama-3.1-8b-instant`
    in telemetry even though TASK routes to a different model. Root
    cause: telemetry read `dispatcher.last_llm_label`, a single mutable
    field that carried the prior BANTER turn's label. The fix stamps a
    turn-local `session._jarvis_llm_label` at swap time. Verify a BANTER
    turn then a non-fast-path TASK turn on the SAME session leaves the
    TASK label, not the stale BANTER one."""
    g = build_turn_graph()
    session = _mk_session("hey jarvis")
    session._jarvis_baseline_wpm = 0.0
    dispatcher, tts_dispatcher = _mk_dispatcher(), _mk_tts_dispatcher()

    # Turn 1: BANTER fast-path → banter label stamped.
    _invoke(
        g, {"transcript": "hey jarvis", "fast_path": True, "duration_s": 0.5},
        session=session, dispatcher=dispatcher,
        tts_dispatcher=tts_dispatcher, classifier=_mk_classifier("TASK"),
    )
    assert session._jarvis_llm_label == "groq:llama-X-banter"

    # Turn 2 (same session): non-fast-path, classifier says TASK.
    session.chat_ctx.messages = [SimpleNamespace(role="user", content="open the build log")]
    _invoke(
        g, {"transcript": "open the build log", "fast_path": False, "duration_s": 1.5},
        session=session, dispatcher=dispatcher,
        tts_dispatcher=tts_dispatcher, classifier=_mk_classifier("TASK"),
    )
    # The stamp must reflect THIS turn's model, not the stale banter one.
    assert session._jarvis_llm_label == "groq:llama-X-task"


def test_classifier_garbage_falls_back_to_task():
    g = build_turn_graph()
    session = _mk_session("blarg")
    dispatcher, tts_dispatcher = _mk_dispatcher(), _mk_tts_dispatcher()
    classifier = _mk_classifier(reply="not a route")

    result = _invoke(
        g, {"transcript": "blarg", "fast_path": False, "duration_s": 0.5},
        session=session, dispatcher=dispatcher,
        tts_dispatcher=tts_dispatcher, classifier=classifier,
    )
    # As of 2026-05-24 the TASK label was split into 5 sub-routes;
    # the unknown-classifier fallback is now TASK_OTHER.
    assert result["route"] == "TASK_OTHER"


def test_no_classifier_defaults_to_task():
    g = build_turn_graph()
    session = _mk_session("something")
    dispatcher, tts_dispatcher = _mk_dispatcher(), _mk_tts_dispatcher()

    result = _invoke(
        g, {"transcript": "something", "fast_path": False, "duration_s": 0.5},
        session=session, dispatcher=dispatcher,
        tts_dispatcher=tts_dispatcher, classifier=None,
    )
    assert result["route"] == "TASK_OTHER"


def test_prefix_injection_modifies_latest_user_message():
    g = build_turn_graph()
    session = _mk_session("explain something")
    dispatcher, tts_dispatcher = _mk_dispatcher(), _mk_tts_dispatcher()
    classifier = _mk_classifier(reply="REASONING")

    _invoke(
        g, {"transcript": "explain something", "fast_path": False, "duration_s": 2.0},
        session=session, dispatcher=dispatcher,
        tts_dispatcher=tts_dispatcher, classifier=classifier,
    )
    last = session.chat_ctx.messages[-1].content
    assert last.startswith("[Route: REASONING]")
    assert "[Emotion:" in last


def test_speech_rate_baseline_updates_on_session():
    g = build_turn_graph()
    session = _mk_session()
    session._jarvis_baseline_wpm = 0.0  # unseeded
    dispatcher, tts_dispatcher = _mk_dispatcher(), _mk_tts_dispatcher()
    classifier = _mk_classifier(reply="TASK")

    # 4 words in 1.5s = 160 wpm
    result = _invoke(
        g, {"transcript": "open the file please", "fast_path": False, "duration_s": 1.5},
        session=session, dispatcher=dispatcher,
        tts_dispatcher=tts_dispatcher, classifier=classifier,
    )
    assert session._jarvis_baseline_wpm == pytest.approx(160.0)
    assert result["current_wpm"] == pytest.approx(160.0)


def test_session_telemetry_attrs_populated():
    """The graph should write the same `session._jarvis_emotion` /
    `_jarvis_route` attrs the legacy code wrote, so the assistant
    `_on_item` telemetry hook keeps working."""
    g = build_turn_graph()
    session = _mk_session()
    dispatcher, tts_dispatcher = _mk_dispatcher(), _mk_tts_dispatcher()
    classifier = _mk_classifier(reply="EMOTIONAL")

    _invoke(
        g, {"transcript": "i'm frustrated with this", "fast_path": False, "duration_s": 1.5},
        session=session, dispatcher=dispatcher,
        tts_dispatcher=tts_dispatcher, classifier=classifier,
    )
    assert session._jarvis_route == "EMOTIONAL"
    assert session._jarvis_emotion in {
        "frustrated", "neutral", "sad", "urgent", "excited", "curious"
    }

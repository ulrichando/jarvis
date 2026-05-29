"""LangGraph dispatcher for JARVIS voice turns.

Replaces the imperative `_classify_and_swap` flow in jarvis_agent.py with
an explicit StateGraph. Each former branch becomes a node:

    detect_emotion → compute_speech_rate → fast_path_check
                                                │
                          ┌─────────────────────┴────────────────────┐
                          ↓                                          ↓
                  apply_banter_swap                          run_classifier
                          │                                          ↓
                          └──────────► swap_route ◄──────────────────┘
                                            ↓
                                     inject_prefix
                                            ↓
                                    tune_interrupt
                                            ↓
                                          END

Why a graph here, given the logic already worked imperatively:

1. **Replayable** — every node is a pure function of (state, config). Saving
   the state at any point lets us replay a turn for debugging.
2. **Extensible** — the user wants more sub-agents soon (planner, browser,
   research). Each subagent becomes a downstream node from `swap_route`,
   not another async branch in a 100-line function.
3. **Cross-domain reuse** — the LangChain ChatModel used by `run_classifier`
   is the same primitive used by future RAG / document QA / tool agents.
4. **Provider-pluggable** — `JARVIS_ROUTER_PROVIDER` + `JARVIS_ROUTER_MODEL`
   resolves via `langchain.chat_models.init_chat_model`, so swapping Groq for
   DeepSeek for the classifier is a config change, not a code change.

Things that explicitly stayed in jarvis_agent.py / LiveKit:
- The voice-loop LLM (`session._llm`) — that has to be a `livekit.agents.llm.LLM`
  instance, not a LangChain ChatModel. The `swap_route` node uses the existing
  `DispatchingLLM` registry to pick a LiveKit-compatible inner.
- The TTS dispatcher (`DispatchingTTS`) — same constraint.
- The synchronous-listener constraint — `apply_banter_swap` and `swap_route`
  must complete before LiveKit reads `session._llm`. The graph is invoked
  with `ainvoke` from inside the listener; we await it before returning.
- All filters/middleware on the LLM stream.

Phase 2 (NOT in this spike): replace `transfer_to_desktop` / future subagents
with subgraphs invoked from `swap_route` based on the classified route.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from pipeline.turn_router import (
    AudioMeta,
    classify_turn,
    compute_interrupt_tuning,
    compute_speech_rate,
    detect_emotion,
    update_baseline,
)

logger = logging.getLogger("jarvis.turn_graph")


# Per-turn state. Mutable through node returns; LangGraph merges by key.
class TurnState(TypedDict, total=False):
    transcript: str
    emotion: str
    route: str
    current_wpm: float
    baseline_wpm: float          # the baseline that detect_emotion used
    new_baseline_wpm: float      # post-update baseline to stash on session
    duration_s: float
    fast_path: bool              # True if BANTER fast-path matched
    classifier_skipped: bool     # True when the regex fast-path won
    interrupted: bool            # True if prior assistant message ended mid-sentence
    turn_n: int
    session_min: int
    llm_label: str
    voice_id: str


# ── Node functions ────────────────────────────────────────────────────


def _node_detect_emotion(state: TurnState, config: Optional[RunnableConfig] = None) -> dict:
    """Pure: lexical + caps-ratio emotion tag. Speech-rate path is handled
    by the next node which has the rate signal."""
    transcript = state["transcript"]
    # Caller (compute_speech_rate node) populates baseline_wpm — at this
    # point we use a temporarily-empty AudioMeta and let the rate node refine.
    audio = AudioMeta(speech_rate_wpm=0.0, baseline_wpm=0.0)
    return {"emotion": detect_emotion(transcript, audio)}


def _node_compute_speech_rate(state: TurnState, config: Optional[RunnableConfig] = None) -> dict:
    """Compute current WPM, update baseline EMA, refine emotion if the
    rate signal flips it."""
    transcript = state["transcript"]
    duration_s = state.get("duration_s", 0.0) or 0.0
    cfg = (config or {}).get("configurable", {}) or {}
    session = cfg.get("session")

    prior_baseline = float(getattr(session, "_jarvis_baseline_wpm", 0.0) or 0.0) if session else 0.0
    current_wpm = compute_speech_rate(transcript, duration_s)
    new_baseline = update_baseline(current_wpm, prior_baseline)

    # Refine emotion now that we have rate signal
    audio = AudioMeta(speech_rate_wpm=current_wpm, baseline_wpm=prior_baseline)
    refined = detect_emotion(transcript, audio)

    if session is not None:
        session._jarvis_baseline_wpm = new_baseline

    return {
        "current_wpm": current_wpm,
        "baseline_wpm": prior_baseline,
        "new_baseline_wpm": new_baseline,
        "emotion": refined,
    }


def _node_fast_path_check(state: TurnState, config: Optional[RunnableConfig] = None) -> dict:
    """Decide whether the BANTER regex pre-classifier wins this turn.
    The regex itself lives in jarvis_agent.py; we receive the precomputed
    `fast_path` bool from the caller (the listener) since the regex import
    sits at the module level there."""
    return {"fast_path": bool(state.get("fast_path", False))}


def _route_after_fast_path(state: TurnState) -> str:
    """Conditional edge: BANTER fast-path → apply_banter_swap, else classifier."""
    return "apply_banter_swap" if state.get("fast_path") else "run_classifier"


def _node_apply_banter_swap(state: TurnState, config: Optional[RunnableConfig] = None) -> dict:
    """Synchronous swap to BANTER inners. Mirror of the inline fast-path
    that was in jarvis_agent.py, just relocated to a graph node."""
    cfg = (config or {}).get("configurable", {}) or {}
    session = cfg.get("session")
    dispatcher = cfg.get("dispatcher")
    tts_dispatcher = cfg.get("tts_dispatcher")
    if not (session and dispatcher and tts_dispatcher):
        return {"route": "BANTER", "classifier_skipped": True}

    try:
        new_llm = dispatcher.pick("BANTER")
        new_tts = tts_dispatcher.pick("BANTER", lang=session._jarvis_lang_ctx.get())
        session._llm = new_llm
        session._tts = new_tts
        # Stamp the per-turn model label on the SESSION (turn-local),
        # not the shared dispatcher.last_llm_label field — see
        # _node_swap_route for why.
        session._jarvis_llm_label = getattr(new_llm, "_jarvis_label", None)
        # BANTER interrupt tuning with per-emotion overlay (Phase 7)
        mw, md = compute_interrupt_tuning("BANTER", state.get("emotion", "neutral"))
        opts = getattr(session, "options", None)
        if opts is not None and hasattr(opts, "interruption"):
            opts.interruption["min_words"] = mw
            opts.interruption["min_duration"] = md
        return {
            "route": "BANTER",
            "classifier_skipped": True,
            "llm_label": getattr(new_llm, "_jarvis_label", repr(new_llm)),
            "voice_id": getattr(new_tts, "voice_id", "?"),
        }
    except Exception as e:
        logger.warning(f"[turn-graph:banter-swap] failed; falling back: {e}")
        # Fall back to classifier on any swap exception
        return {"route": "TASK_OTHER", "classifier_skipped": False, "fast_path": False}


async def _node_run_classifier(state: TurnState, config: Optional[RunnableConfig] = None) -> dict:
    """Run the LangChain ChatModel classifier. Uses the same prompt and
    timeout semantics as the legacy classifier, so behaviour is preserved."""
    cfg = (config or {}).get("configurable", {}) or {}
    classifier = cfg.get("classifier")
    history = cfg.get("history") or []
    timeout_ms = int(os.environ.get("JARVIS_ROUTER_TIMEOUT_MS", "500"))

    if classifier is None:
        # No classifier configured (e.g. no GROQ_API_KEY); default to TASK_OTHER
        return {"route": "TASK_OTHER", "classifier_skipped": False}

    history = list(history) + [("user", state["transcript"])]

    async def _call(prompt: str) -> str:
        msg = await classifier.ainvoke(prompt)
        # ChatModel.ainvoke returns AIMessage(content=...)
        return getattr(msg, "content", "") or ""

    route = await classify_turn(
        history=history,
        emotion=state.get("emotion", "neutral"),
        groq_call=_call,
        timeout_ms=timeout_ms,
    )
    return {"route": route, "classifier_skipped": False}


def _node_swap_route(state: TurnState, config: Optional[RunnableConfig] = None) -> dict:
    """Apply route → LLM/TTS swap on the LiveKit session. Skipped if the
    BANTER fast-path already swapped (classifier_skipped=True)."""
    if state.get("classifier_skipped"):
        return {}  # already swapped
    cfg = (config or {}).get("configurable", {}) or {}
    session = cfg.get("session")
    dispatcher = cfg.get("dispatcher")
    tts_dispatcher = cfg.get("tts_dispatcher")
    route = state.get("route", "TASK_OTHER")
    if not (session and dispatcher and tts_dispatcher):
        return {}
    try:
        new_llm = dispatcher.pick(route)
        new_tts = tts_dispatcher.pick(route, lang=session._jarvis_lang_ctx.get())
        session._llm = new_llm
        session._tts = new_tts
        # Stamp the per-turn model label on the SESSION (turn-local).
        # dispatcher.last_llm_label is a single mutable attr on the
        # DispatchingLLM instance — it races across async turns and
        # survives dispatcher rebuilds on reconnect, so per-turn
        # telemetry read stale BANTER (8b) labels on TASK turns
        # (2026-05-20 mis-diagnosis). The session attr is turn-local.
        _label = getattr(new_llm, "_jarvis_label", None)
        session._jarvis_llm_label = _label
        logger.info(f"[turn-graph:swap] route={route} llm={_label or '?'}")
        return {
            "llm_label": getattr(new_llm, "_jarvis_label", repr(new_llm)),
            "voice_id": getattr(new_tts, "voice_id", "?"),
        }
    except Exception as e:
        logger.warning(f"[turn-graph:swap-route] failed for route={route}: {e}")
        return {}


def _node_inject_prefix(state: TurnState, config: Optional[RunnableConfig] = None) -> dict:
    """Mutate the latest user ChatMessage in chat_ctx with
    `[Route: X] [Emotion: Y] [Turn N · session Mm] [Interrupted]?` prefix
    so the LLM can shape its reply per the system prompt's ROUTE TAGS
    section."""
    cfg = (config or {}).get("configurable", {}) or {}
    session = cfg.get("session")
    if session is None:
        return {}
    try:
        session._jarvis_turn_count = int(getattr(session, "_jarvis_turn_count", 0)) + 1
        _start = getattr(session, "_jarvis_session_start", None)
        session_min = int((time.monotonic() - _start) / 60) if _start else 0
        turn_n = session._jarvis_turn_count

        msgs = getattr(session.chat_ctx, "messages", None) or []
        for m in reversed(msgs):
            if getattr(m, "role", None) == "user":
                content = getattr(m, "content", None)
                interrupt_tag = "[Interrupted] " if state.get("interrupted") else ""
                prefix = (
                    f"[Route: {state.get('route', 'TASK_OTHER')}] "
                    f"[Emotion: {state.get('emotion', 'neutral')}] "
                    f"[Turn {turn_n} · session {session_min}m] "
                    f"{interrupt_tag}"
                )
                if isinstance(content, str) and not content.startswith("[Route:"):
                    m.content = prefix + content
                elif isinstance(content, list) and content:
                    first = content[0]
                    if isinstance(first, str) and not first.startswith("[Route:"):
                        content[0] = prefix + first
                break
        # Stash for telemetry
        session._jarvis_emotion = state.get("emotion", "neutral")
        session._jarvis_route = state.get("route", "TASK_OTHER")
        return {"turn_n": turn_n, "session_min": session_min}
    except Exception as e:
        logger.debug(f"[turn-graph:inject-prefix] skipped: {e}")
        return {}


def _node_tune_interrupt(state: TurnState, config: Optional[RunnableConfig] = None) -> dict:
    """Per-route + per-emotion interrupt tuning. BANTER already tuned
    in its fast-path node; for non-fast-path turns, set the params here.

    Phase-7 update: emotion overlay applied on top of the route base.
    A frustrated/sad user gets a longer min_duration so a pause doesn't
    let JARVIS cut them off; an urgent user gets snappier interrupts.
    """
    if state.get("classifier_skipped"):
        return {}
    cfg = (config or {}).get("configurable", {}) or {}
    session = cfg.get("session")
    if session is None:
        return {}
    mw, md = compute_interrupt_tuning(
        state.get("route", "TASK_OTHER"),
        state.get("emotion", "neutral"),
    )
    try:
        opts = getattr(session, "options", None)
        if opts is not None and hasattr(opts, "interruption"):
            opts.interruption["min_words"]    = mw
            opts.interruption["min_duration"] = md
    except Exception as e:
        logger.debug(f"[turn-graph:tune-interrupt] skipped: {e}")
    return {}


# ── Graph compilation ────────────────────────────────────────────────


def build_turn_graph():
    """Compile the turn-dispatch graph. Caller invokes via
    `await graph.ainvoke(state, config={'configurable': {...}})`."""
    g = StateGraph(TurnState)

    g.add_node("detect_emotion",     _node_detect_emotion)
    g.add_node("compute_rate",       _node_compute_speech_rate)
    g.add_node("fast_path_check",    _node_fast_path_check)
    g.add_node("apply_banter_swap",  _node_apply_banter_swap)
    g.add_node("run_classifier",     _node_run_classifier)
    g.add_node("swap_route",         _node_swap_route)
    g.add_node("inject_prefix",      _node_inject_prefix)
    g.add_node("tune_interrupt",     _node_tune_interrupt)

    g.add_edge(START, "detect_emotion")
    g.add_edge("detect_emotion", "compute_rate")
    g.add_edge("compute_rate", "fast_path_check")

    g.add_conditional_edges(
        "fast_path_check",
        _route_after_fast_path,
        {
            "apply_banter_swap": "apply_banter_swap",
            "run_classifier":    "run_classifier",
        },
    )

    g.add_edge("apply_banter_swap", "swap_route")
    g.add_edge("run_classifier",    "swap_route")
    g.add_edge("swap_route",        "inject_prefix")
    g.add_edge("inject_prefix",     "tune_interrupt")
    g.add_edge("tune_interrupt",    END)

    return g.compile()


# ── Classifier construction ────────────────────────────────────────


def make_classifier():
    """Provider-pluggable classifier built via LangChain's unified
    init_chat_model. Defaults to Groq llama-3.1-8b-instant for speed.

    Override:
      JARVIS_ROUTER_PROVIDER   one of {groq, deepseek, openai, anthropic}
      JARVIS_ROUTER_MODEL      e.g. llama-3.1-8b-instant, deepseek-chat
    """
    provider = os.environ.get("JARVIS_ROUTER_PROVIDER", "groq").lower()
    model = os.environ.get(
        "JARVIS_ROUTER_MODEL",
        "llama-3.1-8b-instant" if provider == "groq" else "deepseek-chat",
    )

    # Need a key to talk to the provider. If absent, return None and the
    # classifier node defaults to TASK.
    key_envs = {
        "groq":      "GROQ_API_KEY",
        "deepseek":  "DEEPSEEK_API_KEY",
        "openai":    "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }
    key_env = key_envs.get(provider)
    if key_env and not os.environ.get(key_env):
        logger.warning(
            f"[turn-graph] {key_env} unset; classifier disabled, falling back to TASK"
        )
        return None

    try:
        from langchain.chat_models import init_chat_model

        return init_chat_model(
            model=model,
            model_provider=provider,
            temperature=0.0,
            max_tokens=6,
        )
    except Exception as e:
        logger.warning(f"[turn-graph] classifier init failed ({provider}/{model}): {e}")
        return None

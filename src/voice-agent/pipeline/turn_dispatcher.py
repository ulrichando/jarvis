"""Per-turn Maya-class router — picks LLM + TTS based on emotion + classifier.

Hoisted from `jarvis_agent.py::entrypoint._on_user_input_for_dispatch`
2026-05-10 (Step 8d-3 of the 10/10 refactor). The handler was ~570
lines of closure body mutating entrypoint locals via `nonlocal` —
the original step-8 vision called for a state-container redesign
before extraction, and this module is that container.

Pipeline (each FINAL transcript runs through):

  1. Stamp `turn_start_monotonic` + `turn_user_text` on session for
     downstream TTFW telemetry.
  2. Recall-query check — force `tool_choice` to
     `recall_conversation` if the transcript is recall-shaped;
     otherwise reset.
  3. Hot-reload prompt state — rebuild memory block + breaker block;
     if either changed, call `agent.update_instructions()` off-band.
  4. Compute speech-rate (from VAD stamps) + RMS dB (from acoustic
     tap) + detect_emotion → stamp `_jarvis_emotion`.
  5. Early route guess (regex-only) → stamp `_jarvis_route` so
     telemetry has a populated value even if the dispatcher is off
     or the classifier task fails.
  6. Reset session._llm / ._tts to TASK defaults (prevents
     prior-turn 8B leak — live 2026-05-08 12:42-12:43).
  7. BANTER fast-path: regex match + ≤6 words → sync swap to
     llama-3.1-8b-instant + inject `[Route: BANTER]` prefix → return.
  8. REASONING fast-path: regex match → sync swap to qwen3-32b +
     inject prefix → return.
  9. LangGraph slow-path: kick the compiled graph as a background
     task (it handles classifier → swap → prefix → tune internally).
 10. Inline async classifier (fallback when graph is disabled):
     Groq llama-3.1-8b classifies, swap session._llm / ._tts, inject
     prefix, tune interrupt thresholds per route.

State management:
  * `prompt_state: dict` — shared with `_build_initial_prompt_state`.
    The handler reads `instructions_prefix` and mutates
    `memory_block` / `breaker_block` in place.
  * Everything else is either a session attribute mutation or a
    `bg_tasks` set spawn.

Construction:
    handler = make_dispatch_handler(
        session=session,
        dispatch_llm=_dispatch_llm,
        dispatch_tts=_dispatch_tts,
        turn_graph=_turn_graph,
        turn_classifier=_turn_classifier,
        bg_tasks=_bg_tasks,
        jarvis_agent=_jarvis_agent,
        prompt_state=_ps,
    )
    session.on("user_input_transcribed")(handler)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Callable

import aiohttp as _aiohttp

from pipeline.fast_path_classifier import (
    BANTER_FAST_PATH_RE,
    REASONING_FAST_PATH_RE,
)
from pipeline.turn_router import (
    AudioMeta,
    classify_turn,
    compute_interrupt_tuning,
    compute_speech_rate,
    detect_emotion,
    is_recall_query,
    update_baseline,
)


__all__ = ["make_dispatch_handler"]


logger = logging.getLogger("jarvis.turn_dispatcher")


def _agent_has_tool(agent: Any, tool_name: str) -> bool:
    """True if `agent` exposes a tool literally named `tool_name`.

    Walks the agent's tool list and reads each tool's name. Handles both
    LiveKit RawFunctionTools (name lives on `.info.name`) and ordinary
    @function_tool callables (name via `get_function_info`). Defensive:
    any failure means "not present" — the caller treats absence as a
    no-op, never an error. Used to gate forced tool_choice so we never
    force a tool that isn't in the supervisor's (registry-only) surface.
    """
    try:
        tools = getattr(agent, "tools", None) or []
    except Exception:
        return False
    for tool in tools:
        name = None
        info = getattr(tool, "info", None)
        if info is not None:
            name = getattr(info, "name", None)
        if name is None:
            try:
                from livekit.agents.llm.tool_context import get_function_info

                name = get_function_info(tool).name
            except Exception:
                name = None
        if name == tool_name:
            return True
    return False


def make_dispatch_handler(
    *,
    session: Any,
    dispatch_llm: Any,
    dispatch_tts: Any,
    turn_graph: Any,
    turn_classifier: Any,
    bg_tasks: set,
    jarvis_agent: Any,
    prompt_state: dict,
    build_memory_block: Callable[[], str],
    build_breaker_status_block: Callable[[], str],
) -> Callable[[Any], None]:
    """Build the `user_input_transcribed` handler closure.

    See module docstring for the pipeline. The closure captures all
    dependencies; the returned callable is registered with
    `session.on("user_input_transcribed", handler)`.
    """

    def _handler(ev) -> None:  # noqa: C901 — single-purpose dispatch pipeline
        # No `if dispatch_llm is None: return` — that gate is now
        # scoped to the swap calls themselves at the bottom.
        if not getattr(ev, "is_final", False):
            return
        transcript = getattr(ev, "transcript", "") or ""
        if not transcript.strip():
            return

        # Stash turn-start timestamp so `_on_item` can compute approx
        # TTFW. Note: a re-fired is_final from STT will overwrite this;
        # the second _classify_and_swap task wins the swap but the
        # dispatcher's last_route may reflect the first task when
        # telemetry reads it. Acceptable for v1.
        try:
            session._jarvis_turn_start_monotonic = time.monotonic()
            session._jarvis_turn_user_text = transcript
        except Exception:
            pass

        # Layer 2 (Phase 3 of memory-layer fix) — when the user
        # transcript is recall-shaped, force tool_choice on
        # recall_conversation so the supervisor LLM can't reject the
        # call via metacognition-conservatism. CRITICAL: explicitly
        # reset tool_choice to None on every non-recall turn (LiveKit
        # issue #4671: tool_choice persists across turns).
        #
        # Safe no-op when the tool is absent: the supervisor's surface
        # is registry-only and currently has no `recall_conversation`
        # tool, so forcing tool_choice on it would make the provider
        # reject the request ("tool not in request.tools"). We only
        # force the choice when the agent actually exposes the tool —
        # so this lights up again automatically once a recall tool is
        # re-ported into the registry, and stays inert until then.
        try:
            if is_recall_query(transcript) and _agent_has_tool(
                jarvis_agent, "recall_conversation"
            ):
                session._jarvis_force_tool_choice = {
                    "type": "function",
                    "function": {"name": "recall_conversation"},
                }
                logger.info(
                    f"[recall-route] forcing recall_conversation for {transcript[:60]!r}"
                )
            else:
                session._jarvis_force_tool_choice = None
        except Exception as e:
            logger.debug(f"[recall-route] check skipped: {e}")

        # Hot-reload the memory + breaker blocks if they changed since
        # last check, so mid-session memory edits / breaker transitions
        # take effect without an agent restart.
        try:
            new_memory_block = build_memory_block()
            new_breaker_block = build_breaker_status_block()

            memory_changed  = new_memory_block != prompt_state["memory_block"]
            breaker_changed = new_breaker_block != prompt_state["breaker_block"]

            if memory_changed or breaker_changed:
                new_instructions = (
                    prompt_state["instructions_prefix"]
                    + new_memory_block
                    + new_breaker_block
                )

                async def _push_instructions():
                    try:
                        await jarvis_agent.update_instructions(new_instructions)
                        if memory_changed:
                            logger.info(
                                f"[memory] block refreshed "
                                f"({len(new_memory_block)} chars)"
                            )
                        if breaker_changed:
                            transition = (
                                "→ degraded" if new_breaker_block else "→ healthy"
                            )
                            logger.info(
                                f"[breaker-status] block refreshed "
                                f"({len(new_breaker_block)} chars) {transition}"
                            )
                    except Exception as e:
                        logger.warning(f"[instructions] hot-reload push failed: {e}")

                _task = asyncio.create_task(_push_instructions())
                bg_tasks.add(_task)
                _task.add_done_callback(bg_tasks.discard)
                if memory_changed:
                    prompt_state["memory_block"] = new_memory_block
                if breaker_changed:
                    prompt_state["breaker_block"] = new_breaker_block
        except Exception as e:
            logger.debug(f"[prompt-refresh] block check skipped: {e}")

        # Derive speech_rate_wpm from VAD start/end timestamps the
        # state-tracking handler stamps. Falls back to 0 if VAD
        # transitions weren't seen. Maintain a rolling EMA baseline
        # on the session so the rate-vs-baseline ratio in
        # detect_emotion can flag urgent / sad turns.
        _start = getattr(session, "_jarvis_speech_started_at", None)
        _end   = getattr(session, "_jarvis_speech_ended_at", None)
        if _start and _end and _end > _start:
            duration_s = _end - _start
        elif _start:
            duration_s = max(0.0, time.monotonic() - _start)
        else:
            duration_s = 0.0
        current_wpm  = compute_speech_rate(transcript, duration_s)
        prior_base   = float(getattr(session, "_jarvis_baseline_wpm", 0.0) or 0.0)
        new_baseline = update_baseline(current_wpm, prior_base)
        session._jarvis_baseline_wpm = new_baseline

        # Phase 10.3 — query the acoustic tap for mean RMS dB over
        # the speech segment, maintain its own EMA baseline.
        current_rms_db = 0.0
        prior_rms_base = float(getattr(session, "_jarvis_baseline_rms_db", 0.0) or 0.0)
        tap = getattr(session, "_jarvis_acoustic_tap", None)
        if tap is not None and _start and _end and _end > _start:
            try:
                current_rms_db = tap.mean_rms_db(_start, _end)
            except Exception as e:
                logger.debug(f"[acoustic] rms query failed: {e}")
        new_rms_baseline = update_baseline(current_rms_db, prior_rms_base)
        session._jarvis_baseline_rms_db = new_rms_baseline

        audio = AudioMeta(
            speech_rate_wpm=current_wpm,
            baseline_wpm=prior_base,
            rms_db=current_rms_db,
            rms_baseline_db=prior_rms_base,
        )
        emotion = detect_emotion(transcript, audio)
        if current_wpm > 0 or current_rms_db < 0:
            logger.debug(
                f"[acoustic] wpm={current_wpm:.0f}/{prior_base:.0f} "
                f"rms_db={current_rms_db:.1f}/{prior_rms_base:.1f} → emotion={emotion}"
            )

        # Phase 10.4 — early-stamp emotion + a regex-only route guess
        # so telemetry has populated values even if the dispatcher is
        # off or the classifier task fails.
        session._jarvis_emotion = emotion
        _word_count_pre = len(transcript.split())
        if _word_count_pre <= 6 and BANTER_FAST_PATH_RE.match(transcript):
            session._jarvis_route = "BANTER"
        elif REASONING_FAST_PATH_RE.match(transcript):
            session._jarvis_route = "REASONING"
        elif emotion in ("frustrated", "sad"):
            session._jarvis_route = "EMOTIONAL"
        else:
            session._jarvis_route = "TASK"

        # Reset the per-utterance markers so the next turn starts
        # fresh — without this, a transcript that arrives after the
        # user has already started speaking again would carry stale
        # stamps.
        session._jarvis_speech_started_at = None
        session._jarvis_speech_ended_at   = None

        # Phase 10.4 — short-circuit when the dispatcher is bypassed.
        # Emotion + early route are already stamped above; downstream
        # branches only do the LLM/TTS swap.
        if dispatch_llm is None:
            return

        # ── 2026-05-08: prevent prior-turn LLM leak ───────────────
        # Bug: BANTER fast-path swaps session._llm to banter_inner
        # (8B); for SUBSEQUENT non-fast-path turns,
        # _classify_and_swap runs as a background task that races
        # the framework's reply pipeline. Fix: reset session._llm +
        # session._tts to TASK defaults at the top of every dispatch.
        try:
            session._llm = dispatch_llm.pick("TASK")
            session._tts = dispatch_tts.pick("TASK")
            # Stamp the per-turn model label on the SESSION (turn-local),
            # set synchronously here on EVERY dispatch. The shared
            # dispatch_llm.last_llm_label races across async turns and
            # survives dispatcher rebuilds on reconnect, so per-turn
            # telemetry read stale BANTER (8b) labels on TASK turns
            # (2026-05-20 mis-diagnosis). The session attr is turn-local.
            session._jarvis_llm_label = getattr(session._llm, "_jarvis_label", None)
        except Exception as _reset_err:
            logger.debug(f"[dispatch] LLM reset to TASK default skipped: {_reset_err}")

        # Synchronous BANTER fast-path. Listeners run synchronously
        # inside the event emitter so the swap lands before the
        # framework's reply pipeline reads session._llm.
        word_count = len(transcript.split())
        if word_count <= 6 and BANTER_FAST_PATH_RE.match(transcript):
            try:
                fast_llm = dispatch_llm.pick("BANTER")
                fast_tts = dispatch_tts.pick("BANTER")
                session._llm = fast_llm
                session._tts = fast_tts
                session._jarvis_emotion = emotion
                session._jarvis_route   = "BANTER"
                session._jarvis_llm_label = getattr(fast_llm, "_jarvis_label", None)

                try:
                    mw, md = compute_interrupt_tuning("BANTER", emotion)
                    opts = getattr(session, "options", None)
                    if opts is not None and hasattr(opts, "interruption"):
                        opts.interruption["min_words"]    = mw
                        opts.interruption["min_duration"] = md
                except Exception as ie:
                    logger.debug(f"[fast-path-banter] interrupt-tune skipped: {ie}")

                try:
                    session._jarvis_turn_count = int(getattr(session, "_jarvis_turn_count", 0)) + 1
                    _sstart = getattr(session, "_jarvis_session_start", None)
                    _session_min = int((time.monotonic() - _sstart) / 60) if _sstart else 0
                    _turn_n = session._jarvis_turn_count
                    msgs = getattr(session.chat_ctx, "messages", None) or []
                    for m in reversed(msgs):
                        if getattr(m, "role", None) == "user":
                            content = getattr(m, "content", None)
                            prefix = (
                                f"[Route: BANTER] [Emotion: {emotion}] "
                                f"[Turn {_turn_n} · session {_session_min}m] "
                            )
                            if isinstance(content, str) and not content.startswith("[Route:"):
                                m.content = prefix + content
                            elif isinstance(content, list) and content:
                                first = content[0]
                                if isinstance(first, str) and not first.startswith("[Route:"):
                                    content[0] = prefix + first
                            break
                except Exception as pe:
                    logger.debug(f"[fast-path-banter] prefix inject skipped: {pe}")

                logger.info(
                    f"[fast-path-banter] sync swap (no classifier): "
                    f"emotion={emotion} llm={getattr(fast_llm, '_jarvis_label', '?')} "
                    f"transcript={transcript[:60]!r}"
                )
                return
            except Exception as e:
                logger.warning(
                    f"[fast-path-banter] swap failed; falling back to classifier: {e}"
                )

        # Synchronous REASONING fast-path. Mirror of BANTER but for
        # the opposite end of the route spectrum.
        if REASONING_FAST_PATH_RE.match(transcript):
            try:
                fast_llm = dispatch_llm.pick("REASONING")
                fast_tts = dispatch_tts.pick("REASONING")
                session._llm = fast_llm
                session._tts = fast_tts
                session._jarvis_emotion = emotion
                session._jarvis_route   = "REASONING"
                session._jarvis_llm_label = getattr(fast_llm, "_jarvis_label", None)

                try:
                    mw, md = compute_interrupt_tuning("REASONING", emotion)
                    opts = getattr(session, "options", None)
                    if opts is not None and hasattr(opts, "interruption"):
                        opts.interruption["min_words"]    = mw
                        opts.interruption["min_duration"] = md
                except Exception as ie:
                    logger.debug(f"[fast-path-reasoning] interrupt-tune skipped: {ie}")

                try:
                    session._jarvis_turn_count = int(getattr(session, "_jarvis_turn_count", 0)) + 1
                    _sstart = getattr(session, "_jarvis_session_start", None)
                    _session_min = int((time.monotonic() - _sstart) / 60) if _sstart else 0
                    _turn_n = session._jarvis_turn_count
                    msgs = getattr(session.chat_ctx, "messages", None) or []
                    for m in reversed(msgs):
                        if getattr(m, "role", None) == "user":
                            content = getattr(m, "content", None)
                            prefix = (
                                f"[Route: REASONING] [Emotion: {emotion}] "
                                f"[Turn {_turn_n} · session {_session_min}m] "
                            )
                            if isinstance(content, str) and not content.startswith("[Route:"):
                                m.content = prefix + content
                            elif isinstance(content, list) and content:
                                first = content[0]
                                if isinstance(first, str) and not first.startswith("[Route:"):
                                    content[0] = prefix + first
                            break
                except Exception as pe:
                    logger.debug(f"[fast-path-reasoning] prefix inject skipped: {pe}")

                logger.info(
                    f"[fast-path-reasoning] sync swap (no classifier): "
                    f"emotion={emotion} llm={getattr(fast_llm, '_jarvis_label', '?')} "
                    f"transcript={transcript[:80]!r}"
                )
                return
            except Exception as e:
                logger.warning(
                    f"[fast-path-reasoning] swap failed; falling back to classifier: {e}"
                )

        # ── LangGraph dispatcher (Phase 1) ────────────────────────
        # When the graph is built (default), invoke it as a background
        # task in place of the inline _classify_and_swap below. Falls
        # back to the inline path on JARVIS_GRAPH_DISABLED=1 or build
        # failure.
        if turn_graph is not None:
            try:
                history = [
                    (m.role, getattr(m, "content", "") or "")
                    for m in (
                        session.chat_ctx.messages[-5:]
                        if hasattr(session, "chat_ctx") and session.chat_ctx
                        else []
                    )
                ]
            except Exception:
                history = []

            # Detect interrupt synchronously — same heuristic as the
            # inline path.
            interrupted = False
            try:
                msgs = getattr(session.chat_ctx, "messages", None) or []
                for m in reversed(msgs):
                    role = getattr(m, "role", None)
                    if role == "assistant":
                        c = getattr(m, "content", None)
                        text = c if isinstance(c, str) else (
                            c[0] if isinstance(c, list) and c and isinstance(c[0], str) else ""
                        )
                        text = (text or "").rstrip()
                        if (
                            text
                            and not text.endswith((".", "!", "?", '"'))
                            and len(text.split()) >= 4
                        ):
                            interrupted = True
                        break
                    if role == "user":
                        break
            except Exception:
                pass

            graph_state = {
                "transcript": transcript,
                "duration_s": duration_s,
                "fast_path": False,
                "interrupted": interrupted,
            }
            graph_cfg = {"configurable": {
                "session": session,
                "dispatcher": dispatch_llm,
                "tts_dispatcher": dispatch_tts,
                "classifier": turn_classifier,
                "history": history,
            }}
            task = asyncio.create_task(
                turn_graph.ainvoke(graph_state, config=graph_cfg)
            )
            bg_tasks.add(task)
            task.add_done_callback(bg_tasks.discard)
            return  # graph owns the rest of this turn's dispatch

        # ── Inline async classifier fallback ──────────────────────
        async def _classify_and_swap():
            async def _groq_call(prompt: str) -> str:
                # Reuse the top-level aiohttp so a missing dep surfaces
                # at startup, not per-turn.
                api_key = os.environ.get("GROQ_API_KEY", "")
                if not api_key:
                    return "TASK"
                async with _aiohttp.ClientSession() as s:
                    async with s.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json={
                            "model": os.environ.get("JARVIS_ROUTER_MODEL", "llama-3.1-8b-instant"),
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.0,
                            "max_tokens": 6,
                        },
                        timeout=_aiohttp.ClientTimeout(total=2.0),
                    ) as r:
                        if r.status != 200:
                            return "TASK"
                        data = await r.json()
                        return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

            try:
                history = [
                    (m.role, getattr(m, "content", "") or "")
                    for m in (
                        session.chat_ctx.messages[-5:]
                        if hasattr(session, "chat_ctx") and session.chat_ctx
                        else []
                    )
                ]
            except Exception:
                history = []
            history.append(("user", transcript))

            timeout_ms = int(os.environ.get("JARVIS_ROUTER_TIMEOUT_MS", "500"))
            route = await classify_turn(
                history=history,
                emotion=emotion,
                groq_call=_groq_call,
                timeout_ms=timeout_ms,
            )

            new_llm = dispatch_llm.pick(route)
            new_tts = dispatch_tts.pick(route)
            session._jarvis_emotion = emotion
            session._jarvis_route   = route

            # Inject [Route] [Emotion] [Turn N · session Mm] prefix
            # into the latest user message in chat_ctx.
            try:
                session._jarvis_turn_count = int(getattr(session, "_jarvis_turn_count", 0)) + 1
                _sstart = getattr(session, "_jarvis_session_start", None)
                _session_min = int((time.monotonic() - _sstart) / 60) if _sstart else 0
                _turn_n = session._jarvis_turn_count

                msgs = getattr(session.chat_ctx, "messages", None) or []

                # Detect interrupt: did the LLM's prior assistant
                # message end mid-sentence?
                interrupted = False
                for m in reversed(msgs):
                    role = getattr(m, "role", None)
                    if role == "assistant":
                        c = getattr(m, "content", None)
                        text = c if isinstance(c, str) else (
                            c[0] if isinstance(c, list) and c and isinstance(c[0], str) else ""
                        )
                        text = (text or "").rstrip()
                        if text and not text.endswith((".", "!", "?", '"')) and len(text.split()) >= 4:
                            interrupted = True
                        break
                    if role == "user":
                        break

                for m in reversed(msgs):
                    if getattr(m, "role", None) == "user":
                        content = getattr(m, "content", None)
                        interrupt_tag = "[Interrupted] " if interrupted else ""
                        prefix = (
                            f"[Route: {route}] [Emotion: {emotion}] "
                            f"[Turn {_turn_n} · session {_session_min}m] "
                            f"{interrupt_tag}"
                        )
                        if isinstance(content, str):
                            if not content.startswith("[Route:"):
                                m.content = prefix + content
                        elif isinstance(content, list) and content:
                            first = content[0]
                            if isinstance(first, str) and not first.startswith("[Route:"):
                                content[0] = prefix + first
                        break

                if interrupted:
                    logger.info(f"[dispatch] turn {_turn_n} preceded by interrupt — tagged")
            except Exception as ie:
                logger.debug(f"[dispatch] prefix inject skipped: {ie}")

            try:
                session._llm = new_llm
                session._tts = new_tts
                session._jarvis_llm_label = getattr(new_llm, "_jarvis_label", None)
                logger.debug(
                    f"[dispatch] route={route} emotion={emotion} "
                    f"llm={getattr(new_llm, '_jarvis_label', repr(new_llm))} "
                    f"voice={getattr(new_tts, 'voice_id', '?')}"
                )
            except Exception as e:
                logger.warning(f"[dispatch] swap failed for route={route}: {e}; will use fallback inner")

            # Per-route + per-emotion interrupt tuning overlay.
            try:
                mw, md = compute_interrupt_tuning(route, emotion)
                opts = getattr(session, "options", None)
                if opts is not None and hasattr(opts, "interruption"):
                    opts.interruption["min_words"]    = mw
                    opts.interruption["min_duration"] = md
            except Exception as ie:
                logger.debug(f"[dispatch] interrupt-tune skipped: {ie}")

        task = asyncio.create_task(_classify_and_swap())
        bg_tasks.add(task)
        task.add_done_callback(bg_tasks.discard)

    return _handler

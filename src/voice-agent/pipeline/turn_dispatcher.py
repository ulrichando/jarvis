"""Per-turn Maya-class router — picks LLM + TTS based on emotion + classifier.

Hoisted from `jarvis_agent.py::entrypoint._on_user_input_for_dispatch`
2026-05-10 (Step 8d-3 of the 10/10 refactor). The handler was ~570
lines of closure body mutating entrypoint locals via `nonlocal` —
the original step-8 vision called for a state-container redesign
before extraction, and this module is that container.

Pipeline (each FINAL transcript runs through):

  1. Stamp `turn_start_monotonic` + `turn_user_text` on session for
     downstream TTFW telemetry.
  2. Reset any per-turn forced `tool_choice` so a prior turn's
     override doesn't leak (LiveKit issue #4671).
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
    update_baseline,
)


__all__ = ["make_dispatch_handler"]


logger = logging.getLogger("jarvis.turn_dispatcher")


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

        # Reset any prior turn's forced tool_choice. LiveKit issue #4671:
        # tool_choice persists across turns on the AgentSession activity
        # unless explicitly cleared. Nothing currently sets a forced
        # choice here, but the consumer in jarvis_agent.py still forwards
        # whatever this attribute holds, so we keep it clean.
        try:
            session._jarvis_force_tool_choice = None
        except Exception:
            pass

        # Hot-reload the breaker block if it changed since last check, so
        # provider-degradation transitions take effect without a restart.
        #
        # Memory is FROZEN per session (file-backed model, 2026-05-21):
        # build_memory_block() returns the snapshot captured at session
        # start and is constant for the whole session, so `memory_changed`
        # is always False and a memory edit NEVER triggers a mid-session
        # update_instructions — that's deliberate, it keeps the prompt
        # prefix stable so the provider-side prefix cache survives. (A
        # `memory` tool write lands on disk now and shows in the prompt on
        # the NEXT session start.) The block is still recomputed + compared
        # here so the breaker hot-reload keeps working; don't "fix" the
        # no-op by making memory refresh per-turn.
        try:
            new_memory_block = build_memory_block()
            new_breaker_block = build_breaker_status_block()

            memory_changed  = new_memory_block != prompt_state["memory_block"]
            breaker_changed = new_breaker_block != prompt_state["breaker_block"]

            if memory_changed or breaker_changed:
                # Stable/volatile cache split (2026-05-23): the supervisor
                # prompt is assembled as STABLE PREFIX (SOUL +
                # JARVIS_INSTRUCTIONS + skill_catalog) + marker + VOLATILE
                # SUFFIX (runtime_id + memory + breaker). The breaker
                # block lives in the volatile suffix, so we rebuild the
                # whole volatile half and re-join with the unchanged
                # stable prefix — this leaves the provider-side cache on
                # the stable prefix VALID after the hot-reload. Same goes
                # for memory_changed, though that path is currently
                # disabled (memory is frozen per session — see the long
                # comment a few lines up).
                #
                # The legacy assembly (instructions_prefix +
                # memory_block + breaker_block + skill_catalog_block) is
                # preserved as a fallback for prompt_state shapes that
                # lack the new stable/volatile keys — older callers /
                # tests still produce the legacy shape only.
                stable_prefix = prompt_state.get("stable_prefix")
                if stable_prefix:
                    # runtime_id_block is session-stable (set once at
                    # session start) so we don't recompute it here —
                    # _build_initial_prompt_state stashed it alongside
                    # the other keys for exactly this rebuild path.
                    runtime_id_block = prompt_state.get("runtime_id_block", "")
                    new_volatile_suffix = (
                        runtime_id_block + new_memory_block + new_breaker_block
                        + prompt_state.get("recent_sessions_block", "")
                    )
                    from providers.prompt_cache import assemble_with_marker
                    new_instructions = assemble_with_marker(
                        stable_prefix, new_volatile_suffix
                    )
                else:
                    # Legacy fallback — preserve the original assembly so
                    # callers that pre-date the 2026-05-23 refactor (and
                    # therefore don't populate stable_prefix) still get a
                    # correctly-rebuilt prompt.
                    new_volatile_suffix = ""  # unused in legacy path
                    new_instructions = (
                        prompt_state["instructions_prefix"]
                        + new_memory_block
                        + new_breaker_block
                        + prompt_state.get("skill_catalog_block", "")
                        + prompt_state.get("recent_sessions_block", "")
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
                # Keep the consolidated volatile_suffix in sync so the
                # next hot-reload tick reads the up-to-date head. The
                # stable_prefix never changes mid-session, so it doesn't
                # need any upkeep here.
                if "stable_prefix" in prompt_state and new_volatile_suffix:
                    prompt_state["volatile_suffix"] = new_volatile_suffix
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
            session._jarvis_route = "TASK_OTHER"

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
            session._tts = dispatch_tts.pick("TASK", lang=session._jarvis_lang_ctx.get())
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
                fast_tts = dispatch_tts.pick("BANTER", lang=session._jarvis_lang_ctx.get())
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
                fast_tts = dispatch_tts.pick("REASONING", lang=session._jarvis_lang_ctx.get())
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
                    return "TASK_OTHER"
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
                            return "TASK_OTHER"
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
            new_tts = dispatch_tts.pick(route, lang=session._jarvis_lang_ctx.get())
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

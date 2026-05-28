# Post-tool reply-required gate + indicator heartbeat

**Date:** 2026-05-27
**Status:** spec, pre-implementation
**Author:** Ulrich + Claude
**Scope:** `src/voice-agent/pipeline/pre_tts_confab_gate.py` (extend), `src/voice-agent/jarvis_agent.py` (heartbeat + new hook), `src/voice-agent/pipeline/turn_telemetry.py` (4 new state constants), unit tests under `tests/test_pre_tts_confab_gate.py` + new `tests/test_thinking_heartbeat.py`.

**Out of scope:** Tool-result truncation (separate spec, addresses route-swaps-from-context-overflow), withholding+recovery error handling (separate spec, addresses chat_ctx ceiling), all CC parity gaps #2 and #3 from the 2026-05-27 audit.

## TL;DR

Two correlated fixes addressing the same UX failure ("JARVIS appears dead during long turns"):

**Part A — Post-tool reply-required gate.** Extends the existing pre-TTS confab gate with an inverse detection: trip when the supervisor emits `tool_use` blocks but no text reply. Retries through the same per-route ladder with a *text-forcing* system message; falls back to a safe filler if all tiers also produce empty text. Addresses live failure (2026-05-27 17:43): JARVIS said the ack "Looking into that.", fired one tool, then went silent — Sonnet's followup LLM call produced no text content.

**Part B — Indicator heartbeat.** Replaces the agent_state-transition-driven thinking-file management with a single background task that touches the file every 3 s while a turn is active. Removes dependency on the framework's agent_state machine transitioning back to "thinking" between tool calls (which today's testing showed isn't reliable). Indicator stays amber for the entire turn lifetime; goes green naturally when the worker dies or the turn ends.

## Why now

1. **The silent-after-ack failure is repeatable and load-bearing.** Three live tests today (17:21, 17:29, 17:43) all hit it. Supervisor says ack → fires tool → empty followup → user hears nothing. The dispatch_agent fix + supervisor.md routing + strip_preambles fix from earlier today each closed an upstream piece but none address "supervisor emits tools but no text".

2. **Indicator unreliability compounds the silence.** Even when the supervisor is genuinely working (tool calls visible in gate verdicts), the tray indicator flips to green during tool execution — telling the user JARVIS is idle. Two failure modes amplify each other: no audio + no visual signal of work in progress.

3. **CC has both pieces.** The 2026-05-27 CC parity audit identified the missing `needsFollowUp` / `isResultSuccessful` guards (`claude-code/src/query.ts:554-558, 1062-1264`; `utils/queryHelpers.ts:56-94`). CC's CLI gets streaming output for free as visual progress; JARVIS needs an explicit indicator equivalent.

## Background — what's there today

- **Pre-TTS confab gate** (`pipeline/pre_tts_confab_gate.py`, landed today as `71b8ea92` + `eaaae0ef`): buffers the LLM text stream, runs `should_gate` against text + tool_calls. Currently trips on `confab_detected` (text claims completion without tool calls). Retries via per-route ladder; falls back to filler `"I'm having trouble with that — could you try again?"`.
- **Tool-call telemetry** (`pipeline/turn_telemetry.py`): persists `subagent_type` / `subagent_ms` / `subagent_status` per turn. Confab states are `clean_*` / `caught_t*_passed` / `caught_filler` / `bypassed_killed` / `retry_factory_missing` / `retry_exception`.
- **Indicator pipeline**: `_AGENT_THINKING_FILE = ~/.jarvis/.agent-thinking` written by `_mark_thinking_start()` on agent_state → "thinking"; unlinked by `_mark_thinking_end()` on "idle"/"listening" (since `bee30aeb`, no longer on "speaking"). Voice-client polls via `agent_is_thinking()`, exposes `state.agent_thinking` on `/status`. Tauri maps `agent_thinking=True && !speaking → amber`.

## Part A — Post-tool reply-required gate

### Detection

Add a new branch to `should_gate` in `pre_tts_confab_gate.py`, sitting AFTER the existing `tool_called` / `no_claim` checks and BEFORE the final `confab_detected` return:

```python
if not text.strip() and tool_calls:
    # Supervisor emitted tool calls but no text reply for voice playback.
    # Without intervention the user hears nothing past the ack.
    return GateVerdict(True, "no_text_after_tool", pattern_matched=None)
```

This is the structural inverse of the existing `confab_detected` rule (which catches text-with-no-tools). Same `should_retry=True`, different `reason`.

### Retry behavior

Add a new constant alongside the existing `TOOL_FORCE_PROMPT`:

```python
TEXT_FORCE_PROMPT = (
    "Your previous response called tools but did NOT voice a result. "
    "The user is waiting — they only heard your acknowledgment. "
    "Summarize what you found in 2-3 sentences for voice playback. "
    "Do NOT call more tools. Just give the user the answer in plain text."
)
```

`run_retry_chain` accepts a `verdict` parameter (already in scope from the caller) and chooses the prompt based on `verdict.reason`:

- `confab_detected` → `TOOL_FORCE_PROMPT` (existing behavior)
- `no_text_after_tool` → `TEXT_FORCE_PROMPT` (new)

Per-route ladder walk is unchanged: tier 1 (primary same model) → tier 2 (escalation, opus-4.7 for action routes) → tier 3 (cross-provider, gpt-5.1) → filler. The retry chain reuses the same `llm_factory` stashed on session by jarvis_agent.

### Filler

When all retry tiers also produce empty text, voice:

> *"I checked but couldn't put together a clear summary. Want me to try again?"*

Distinct from the confab filler (`"I'm having trouble with that — could you try again?"`) because the failure mode is different — the user knows JARVIS did SOMETHING (the ack played) and the filler reflects the partial state.

### Belt-and-suspenders hook

The streaming transform path (`tts_text_transforms`) is not guaranteed to fire when the LLM emits zero text. Add a SECOND hook at `conversation_item_added` for assistant items, defined in `jarvis_agent.py` alongside the existing `_on_*` event handlers:

```python
@session.on("conversation_item_added")
def _on_conversation_item_added_for_text_recovery(ev) -> None:
    item = getattr(ev, "item", None)
    if item is None or getattr(item, "role", None) != "assistant":
        return
    content = getattr(item, "content", None) or []
    has_text = any(
        (isinstance(b, str) and b.strip()) or
        (getattr(b, "type", None) == "text" and getattr(b, "text", "").strip())
        for b in content
    )
    has_tool_use = any(
        getattr(b, "type", None) == "tool_use" or
        (isinstance(b, dict) and b.get("type") == "tool_use")
        for b in content
    )
    if has_tool_use and not has_text:
        if getattr(session, "_jarvis_text_recovery_fired", False):
            return  # streaming hook already handled it
        session._jarvis_text_recovery_fired = True
        asyncio.create_task(_post_turn_text_recovery(session))
```

`_post_turn_text_recovery(session)` is a new async function in `jarvis_agent.py`, defined near the gate filter wiring. Signature + responsibilities:

```python
async def _post_turn_text_recovery(session) -> None:
    """Run the TEXT_FORCE_PROMPT retry chain and voice the result.

    Reuses pre_tts_confab_gate.run_retry_chain with a synthetic
    GateVerdict(should_retry=True, reason="no_text_after_tool"). On
    success voices the produced text via session.say(); on total failure
    voices the no_text filler. Writes telemetry state via the same
    helper the streaming gate uses."""
```

The streaming hook also sets `session._jarvis_text_recovery_fired=True` immediately on detection so the post-turn hook short-circuits. The flag is initialized to `False` at session setup and reset to `False` at every turn-start in `_on_user_input` when `is_final=True`.

### Telemetry

Four new states in `pipeline/turn_telemetry.py`:

```python
CONFAB_STATE_NO_TEXT_T1_PASSED  = "no_text_t1_passed"   # retry tier 1 produced text
CONFAB_STATE_NO_TEXT_T2_PASSED  = "no_text_t2_passed"
CONFAB_STATE_NO_TEXT_T3_PASSED  = "no_text_t3_passed"
CONFAB_STATE_NO_TEXT_FILLER     = "no_text_filler"      # all tiers exhausted
```

`telemetry_state_for_clean` in `pre_tts_confab_gate.py` gets a no-op for these (they're already retry-result states, not clean states). The agent's filter writes them via the existing path that handles `caught_t*_passed`.

## Part B — Indicator heartbeat

### Heartbeat task

New session attribute `_jarvis_thinking_heartbeat: asyncio.Task | None`, started on turn-start and cancelled on turn-end.

```python
async def _thinking_heartbeat(session):
    """Touch _AGENT_THINKING_FILE every 3s while a turn is active.

    Replaces agent_state-transition-driven file management. Robust to
    framework state-machine quirks (e.g., transient 'listening' state
    during tool execution that would otherwise unlink the file)."""
    try:
        while True:
            _mark_thinking_start()
            await asyncio.sleep(3.0)
    except asyncio.CancelledError:
        _mark_thinking_end()
        raise
```

Lifecycle:
- **Start:** in `_on_user_input` when `is_final=True`. Before starting, cancel any prior heartbeat (defensive — handles back-to-back user inputs that arrive faster than turn-end).
- **End (normal):** in `conversation_item_added` for the **final** assistant reply. **Detection rule:** the item has non-empty text content AND no `tool_use` blocks. This avoids the false positive where the FIRST assistant turn has both an ack text ("Looking into that.") AND a `tool_use` block — that turn is interstitial, the tool result hasn't been processed yet, so the heartbeat must keep running. Only an assistant item that is *pure text, no tool_use* counts as the final reply.
- **End (error / barge-in):** wrap turn-execution paths with `try/finally` that cancels the heartbeat. Specifically: the existing barge-in handler at `jarvis_agent.py` (the one that calls `session.interrupt()`) should also cancel the heartbeat. Idempotent: cancelling an already-cancelled task is a no-op.
- **End (worker death):** if the worker job process dies, the heartbeat dies with it. File stops being refreshed. After 60 s TTL, `agent_is_thinking()` returns False naturally. Correct behavior.

### Simplification of agent_state_changed handler

Remove the file write/unlink calls from `_on_agent_state` (lines 4761-4812):
- DELETE: `_mark_thinking_start()` call on `new_state == "thinking"` (line 4762) — the heartbeat now owns this.
- DELETE: `_mark_thinking_end()` + `_mark_tool_end()` calls on `new_state in ("idle", "listening")` (line 4805-4806) — the heartbeat's cancellation cleans up.
- KEEP: everything else (`total_audio_ms` tracking, `_front_loaded_ack` scheduling, `acoustic` state). Those are independent of indicator management.

Also delete the `_mark_thinking_start()` calls from `_on_user_input` (line 4875) and `_on_function_tools_executed` (the one I added in `c534987d`) — both subsumed by the heartbeat.

### Filler-text variety reuse

The 8 front-ack phrases from `c534987d` (`"One moment." / "On it." / "Working on it." / ...`) are already varied. No additional change for Part B specifically.

## Architecture summary

```
turn_start (_on_user_input is_final=True)
  ├─ cancel any prior heartbeat task (defensive)
  ├─ start heartbeat task → loop {touch file, sleep 3s}
  └─ existing turn-start work (reset attrs, bump session token, etc.)

[turn runs: LLM → tool_use(s) → tool_result(s) → LLM → ...]

streaming transform on each LLM text stream:
  ├─ if !buffer && tool_calls → trigger no_text_after_tool retry
  ├─ if buffer matches confab → trigger confab_detected retry (existing)
  └─ pass through otherwise

conversation_item_added (assistant, FINAL = text-only, no tool_use):
  └─ cancel heartbeat task → finally: unlink file

conversation_item_added (assistant, INTERSTITIAL = has tool_use):
  ├─ keep heartbeat running (more LLM iterations coming)
  └─ if tool_use AND no text → trigger no_text_after_tool retry
     via fallback path (catches streaming-transform misses)
```

## Risk

- **`conversation_item_added` may fire before tool_results land.** Need to verify with livekit-agents source that "final reply" detection works correctly when the assistant turn is the LAST item in chat_ctx. Worst case: heartbeat lingers a few extra seconds after the actual final reply; user sees amber for 3 s of extra time. Acceptable.
- **Mid-chain tool_use false positive.** A model legitimately chaining 2+ tools may emit `tool_use` blocks with no text mid-chain (intentionally silent until final summary). The streaming gate's `not text.strip() and tool_calls` rule would trip on every silent iteration. **Mitigation for v1:** the streaming gate adds a guard — only trip `no_text_after_tool` when the iteration produced NO NEW `tool_use` blocks (i.e., this iteration is meant to be the final text reply, not a tool-chain continuation). Implementation reads `session._jarvis_tool_calls_this_iteration` (a new transient counter) — if zero new tool_use blocks were emitted in THIS iteration AND the session has any prior tool_calls AND text is empty → trip. The implementer surfaces this if the livekit-agents API doesn't expose per-iteration tool-call deltas; in that case, fall back to "only fire at turn-end via the belt-and-suspenders hook".
- **False trips on `no_text_after_tool`.** A turn that calls dispatch_agent and returns its raw output as the final text would have empty `text.strip()` if dispatch_agent returns just whitespace. Real dispatch_agent results are always non-empty per the handler contract. Mitigation: detection requires `not text.strip()` (whitespace-only counts as empty). If a pathological subagent returns blank output, this still trips — which is correct (user needs to hear SOMETHING).
- **Retry cost.** Each tripped turn costs ~$0.01 in tokens for the retry chain (Sonnet then Opus then GPT-5.1). Cheap compared to total silence. Daily cap if we see >5/day: not in this PR.
- **Heartbeat task leak.** If the worker dies mid-task without graceful shutdown, the asyncio task is reaped with the process. Acceptable.

## Testing

`tests/test_pre_tts_confab_gate.py` adds:
1. `test_no_text_after_tool_trips_gate` — should_gate returns `should_retry=True, reason="no_text_after_tool"` when text="" and tool_calls non-empty
2. `test_no_text_retry_uses_text_force_prompt` — the system message appended on retry contains "did NOT voice a result"
3. `test_pure_text_no_tools_does_not_trip_no_text_branch` — turn with text but no tool calls passes through (no false positive)
4. `test_no_text_filler_when_all_tiers_empty` — when all retry tiers also produce empty text, fallback filler is used

`tests/test_turn_telemetry.py` adds:
5. `test_no_text_states_distinct` — 4 new state constants exist, are distinct from each other and from existing states

`tests/test_thinking_heartbeat.py` (new file):
6. `test_heartbeat_touches_file_during_active_turn` — start heartbeat, wait 4 s, file mtime is < 4 s old
7. `test_heartbeat_unlinks_file_on_cancel` — cancel heartbeat task, file is unlinked
8. `test_heartbeat_survives_speaking_state` — simulate agent_state → speaking → listening → thinking cycle; file stays present throughout
9. `test_back_to_back_turns_dont_leak_heartbeat` — start heartbeat, start a new one, only one is running, prior is cancelled

## Verification path

1. Unit tests pass (8 tests across 3 files)
2. Live restart of voice-agent, manual confab trigger ("Jarvis, review my uncommitted changes")
3. Indicator: tray should stay amber from the moment ack plays through final reply (no flicker to green during tool work)
4. Inspect log: should see exactly one `[heartbeat] touch` line every 3 s during the turn (add INFO logging to the heartbeat for observability)
5. Inspect telemetry: turn row should have `confab_check_state` in `caught_t1_passed` / `no_text_t1_passed` / `no_text_filler` etc., not the bare `clean_*` states that would indicate the gate didn't fire

## References

- 2026-05-27 confab-gate spec: `docs/superpowers/specs/2026-05-27-pre-tts-confab-gate-pattern-coverage.md`
- 2026-05-27 dispatch_agent spec: `docs/superpowers/specs/2026-05-27-voice-agent-subagent-dispatch.md`
- 2026-05-27 CC parity audit: in-session Explore-agent report (gaps #1 + #4)
- Indicator pipeline reference: `voice_client_tray_config.py:212` (60s TTL), `voice_client_http_api.py:173` (state mapping), Tauri `App.jsx:251-259` (color routing)

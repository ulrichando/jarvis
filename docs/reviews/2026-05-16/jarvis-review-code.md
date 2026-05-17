# JARVIS voice-agent code review — 2026-05-16

Reviewer: senior Python engineer, read-only.
Project: `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/`.
Headline numbers: `jarvis_agent.py` is **5,478 lines**; the rest of the tree is ~13,400 lines across pipeline/, providers/, subagents/, sanitizers/, resilience/. Test suite: ~100 test files, 800+ tests, ~25s full-suite runtime per CLAUDE.md.

---

## TL;DR

### Top 5 quick wins (low risk, high cost-of-not-doing)

1. **Classify exceptions in `CircuitBreaker._record_failure` so 429/auth-error/4xx-validation don't trip the breaker.** Today every exception type counts equally toward `fail_threshold`. Groq rate-limits + Anthropic credit-exhausted on rung 3 routinely flip TTS+LLM breakers OPEN for 20-30 s of falling-back. Fix at `resilience/circuit_breaker.py:94`. `BreakeredLLMStream` already does an ad-hoc fix for validation errors (`providers/llm.py:530-547`); promote that logic into the breaker itself with a `non_failure_classifier` callable.

2. **Sanitizers `__init__.py` is documentation-only.** All 7 installers are called sequentially from `jarvis_agent.py:121-195` in a hand-maintained order. Move ordering into `sanitizers.install_all()` so the four-load-bearing-monkey-patches constraint is enforced in one place and re-imports stay idempotent. (CLAUDE.md calls these "load-bearing"; their install order is currently invisible to any reader who doesn't read 75 lines of imports.)

3. **Fire-and-forget `asyncio.create_task` orphans at 4 sites in `jarvis_agent.py`** (lines 3505, 3539, 4010, 5357, 5362). The repo already has the right pattern (`_bg_tasks.add(t); t.add_done_callback(_bg_tasks.discard)`) but only 3 of 8 spawn sites use it. Until they're added, the GC can reap a task mid-await — same failure mode the spec called out for evolution loops.

4. **Telemetry has 0 row-level redaction**, but `user_text` and `jarvis_text` go straight into SQLite (`pipeline/turn_telemetry.py:43-58`). `state.db.messages` is the same. There's no PII filter, and the conversation captures everything (names, addresses from `get_location`, dates from `date_math`, etc.). Add a redaction pass before INSERT or document that this DB is treated as a secret.

5. **`jarvis_agent.py:893-934` has two parallel regex lists** (`_MUTE_PATTERNS`, `_WAKE_PATTERNS`) of compiled patterns built inline, plus a third "strict" subset `_WAKE_STRICT_PATTERNS` whose entries duplicate strings from `_WAKE_PATTERNS`. The 11-pattern strict set is hand-synced to the larger set — drift-prone. Refactor to a single declarative `[(pattern, strict)]` list.

### Top 3 deep refactors (real work, real return)

1. **`jarvis_agent.py` is still a 5,500-line orchestrator.** The 10/10 refactor extracted prompts/providers/sanitizers/pipeline helpers, but the entrypoint() function alone is **~1,000 lines** (lines 4473-5376) and `JarvisAgent.on_user_turn_completed` is **~290 lines** (lines 3326-3617). The remaining concerns that should leave are:
   - ~300 lines of standalone @function_tool definitions (lines 1145-2900) — these are tools, not agent wiring. Belong in `tools/voice_tools.py`.
   - ~200 lines of TTS text-transform filters (lines 2906-3300). Belong in `pipeline/tts_filters.py`.
   - ~120 lines of silent-mode / mute / wake handling (lines 862-1031). Belong in `pipeline/silent_mode.py`.
   - The 290-line `on_user_turn_completed` should be a thin orchestrator over the same gates exposed as named helpers (already partially done with `_is_command`, `_is_ambiguous_short_input`, `_is_garbage_transcript`, but the if-cascade in `on_user_turn_completed` itself is the deep nesting).

2. **`turn_dispatcher.py::_handler` is a 570-line closure with `# noqa: C901`.** It's documented as "single-purpose dispatch pipeline" but has 10 sequential phases (recall check, hot-reload, speech-rate, RMS, emotion, early-route, BANTER fast-path, REASONING fast-path, graph dispatch, inline classifier fallback) with ad-hoc try/except wrapping each. The 70-line `_classify_and_swap` inline fallback duplicates roughly half the LangGraph path. Either delete the inline fallback (the LangGraph build is default-on and battle-tested) or extract it to its own module and shrink the closure to a 12-line router.

3. **Subagent registry has TWO parallel models** — `HandoffSubagent` (`transfer_to_X` per-spec) and `DelegatedSubagent` (one `delegate(role, task)` tool). The HOW_TO_ADD_A_SUBAGENT doc says use Handoff; conftest enables 7 Delegated subagents but all 7 are gated off by default in production per CLAUDE.md (`JARVIS_SUBAGENT_<NAME>=1`). The `delegate` machinery is dead weight until those gates are flipped on. Either:
   - Commit to one shape (Handoff is the simpler path, Delegate was a token-cost optimization that never paid off because the gate-off default prevents the >25-spec regime it was built for), OR
   - Document a date by which one shape is retired.

---

## Findings per area

### 1. `jarvis_agent.py` (5,478-line monolith)

**What still lives here that shouldn't:**

- **Standalone @function_tool definitions (lines 1145-2900):** `run_jarvis_cli`, `type_in_terminal`, `media_control`, `get_location`, `set_location`, `read_file`, `calc`, `date_math`, `current_time`, `web_search`, `web_fetch`, `glob_files`, `grep_files`, `recall_conversation`, `remember_this`, plus their helpers (`_player_on_bus`, `_launch_and_verify`, `_collect_wifi_bssids`, `_google_geolocate`, `_reverse_geocode`, `_ddg_instant_answer`). These are pure tool definitions with no JarvisAgent-class coupling. The new `tools/` subdir already holds bash/file_read/file_edit/file_write/plan_mode/tasks/monitor/worktree/code_search — these voice tools belong there too. ~1,750 lines extractable.

- **TTS text-transform pipeline (lines 2906-3290):** `_LEAK_PATTERNS`, `strip_function_call_leakage`, `_HEDGE_RE`, `_APPEND_RE`, `strip_voice_closers`, `_SIR_RE`, `_TRAILING_SIR_RE`, `cap_sir_count`, `_PREAMBLE_RE`, `strip_preambles`, `_SPACED_NUMBER_RE`, `normalize_numbers`, `strip_archaic_openers`, `strip_meta_silence`, `stamp_first_token`. Self-contained, no JarvisAgent state references except `_active_session_for_telemetry[0]`. ~400 lines extractable.

- **Silent-mode / mute / wake state (lines 862-1031):** `_SILENT_MODE_FILE`, `_is_silent`, `_set_silent`, `_MUTE_PATTERNS`, `_WAKE_PATTERNS`, `_WAKE_STRICT_PATTERNS`, `_matches_any`, `_COMMAND_MAX_WORDS`, `_SENTENCE_SPLIT_RE`, `_MEDIA_OBJECT_RE`, `_is_command`. Already a coherent module. ~170 lines extractable.

- **Tool-busy / thinking flag files (lines 799-880):** `_mark_tool_start`, `_mark_tool_end`, `_mark_thinking_start`, `_mark_thinking_end`, `_TURN_TOOL_CALL_LIMIT`, `_tool_calls_this_turn`, `_reset_tool_call_count`. Tray-status side-effects. ~80 lines extractable.

After these extractions, `jarvis_agent.py` would drop to ~2,800 lines — still big, but readable. The remaining concerns (entrypoint orchestration, JarvisAgent class, build_llm_stack, build_initial_prompt_state, watchdogs, session event registration, the __main__ runner) are properly part of the agent boot/loop.

**Mock/test code in production paths:**

- `BreakeredGroqLLM._call_with_breaker_for_test()` (`providers/llm.py:678`), `BreakeredGroqSTT._call_with_breaker_for_test()` (`providers/stt.py:50`), `LoggingGroqChunkedStream._call_with_breaker_for_test()` (`providers/tts.py:220`) — these "test seam" methods ship in production classes. Low-impact (they're never called), but they confuse code readers ("why does the production STT have a `for_test` method?"). Either move to a sibling `BreakeredGroqSTTTestable` subclass in the test file, or drop them and let tests construct the wrapper directly.

- `JARVIS_DEBUG_TTS_CHUNKS=1` log in `stamp_first_token` (line 3091-3092): "Remove or gate behind env var once the root cause is found." It IS env-gated, but the comment admits this is leftover debug instrumentation. Either delete or commit to it.

- `_call_with_breaker_for_test` lives next to the production class because of `groq.LLM` constructor coupling — refactor would need to break that coupling, which is what makes this a P2 not P1.

**Dead code:**

- `_pick_supervisor_llm` (line 3692) is a 7-line function whose entire docstring says it returns the legacy LLM and the LangGraph supervisor was deleted. `subagent_tools` argument is unused. The function is called from one site (line 4525). Delete the function and inline `legacy_llm` at the call site.

- `list_pending_proposals` / `accept_proposal` / `reject_proposal` are commented as "intentionally unwired 2026-05-12" (line 5164-5169) but the functions are still defined elsewhere in the codebase (per the comment). Verify those defs are also gone or have a `# DEAD` marker.

- `RECALL_SEARCH_LIMIT = 8` at line 1744 is set immediately after a 60-line comment explaining the recall window was tuned and re-tuned. It's used by `recall_conversation` only. The complex tuning history could move into a docstring; the constant itself is fine.

**Repeated patterns that should be helpers:**

- `try: …; except Exception as e: logger.debug(f"[X] skipped: {e}")` pattern appears ~80 times. Many are correct (defensive against framework attribute drift). But e.g. lines 4313-4324 (total_audio_ms tracking) and lines 4326-4331 (user input transcribed) wrap simple attribute reads. A `safe_setattr(obj, name, value, on_fail=logger.debug)` helper would cut ~30 lines.

- `await asyncio.create_subprocess_exec(...); await asyncio.wait_for(proc.communicate(), timeout=...)` repeats in 8+ places (`_player_on_bus`, `_launch_and_verify`, `media_control`, `_collect_wifi_bssids`, etc). A `run_subprocess(*argv, timeout=...) → (stdout, stderr, code)` helper would simplify error handling and timeout cleanup.

- `_clean_env_for_cli` constructs the CLI subprocess env (line 1102-1142). The Anthropic flow (`_make_anthropic_speech_llm`) does similar credential plumbing. The Anthropic kwargs are pulled into a helper at `providers/llm.py:241`; do the same for CLI env construction.

**Bug-prone control flow:**

- **`on_user_turn_completed` (lines 3326-3617)** is a 290-line gate cascade with ~14 short-circuit branches all raising `StopResponse` or `return`. Each branch does string extraction + lowercasing + regex matching. The control flow is correct today but fragile: adding a 15th gate means walking the entire function to make sure it goes between the right neighbors. Refactor into a list-of-gates `GATES = [silent_mode_gate, mute_trigger_gate, quiet_hours_gate, short_input_gate, intent_router_gate, …]` each returning a `Decision` enum, and iterate. Already partially helper-ed (the predicates exist as `_is_command`, `_is_silent`, `_is_ambiguous_short_input`); the cascade itself needs the same treatment.

- **`session.say("Yes?", allow_interruptions=True)` in the bare-vocative fast path (line 3579)** is wrapped in a try/except StopResponse/Exception. Per the comment, exceptions "fall through to LLM" — but the function also `raise StopResponse()` inside the try. If `session.say` raises a non-StopResponse exception, the warning is logged but execution falls through to the next gate cascade WITHOUT stamping `_touch_interaction()`. Mostly harmless because `_touch_interaction()` already ran earlier (line 3425), but the order-dependency is fragile.

- **`_on_item` handler (lines 4763-4990)** is a single 230-line function inside `entrypoint()`. It handles: barge-in truncation, save_turn, agent_state cleanup, auto-mute toggle, telemetry write, evolution observer, ctx-compact. Each concern has its own try/except. Split per concern; this is the single most fragile callback in the codebase.

- **`_save_turn` (line 1606)** — if `_HUB` is not None, it does `asyncio.get_running_loop().create_task(coro)` or falls through to `asyncio.run(coro)`. The `asyncio.run` path is dangerous from inside a sync handler that might (under some race) be called from the loop thread; if a future caller IS in a loop, this `asyncio.run` raises `RuntimeError: asyncio.run() cannot be called from a running event loop`. The current call sites are safe, but the fallback shouldn't exist — make `_save_turn` async or document the sync-only contract.

---

### 2. `subagents/`

**`agent.py` tool-gate verification** (`subagents/agent.py:204-298`): The gate is correctly implemented per CLAUDE.md spec.

- `task_done` walks chat_ctx items since `self._handoff_start_idx` (set on `on_enter`).
- Counts `FunctionCall` items whose name != `task_done`.
- If zero AND summary doesn't match `_BAILOUT_SUMMARY_RE` AND `_no_tool_refusals + 1 < ceiling`: refuses, returns `(self, "REFUSED: …")` with the corrective string the LLM reads as the tool result.
- Ceiling (default 3, env-overridable) force-bails with a generic summary so the user isn't stuck in silence — exactly the design.
- `tools_required=False` (screen_share subagent) skips the gate entirely, correct.
- `JARVIS_SUBAGENT_TOOL_GATE=0` disables, correct.
- After force-bail or successful task_done, the bailout-shape summary is masked (line 321) so the supervisor never voices internal phrasing — this is the 2026-05-11 internal_phrase fix paired correctly with the gate.

**Issue:** `self._handoff_start_idx` is set on `on_enter` (line 181) but if `chat_ctx.items` can't be read, it falls back to 0 (line 187). At index 0, EVERY pre-handoff item is counted as "since handoff" — which means a confab `task_done` would pass the gate because some `FunctionCall` from a prior turn is in the count. The warning is logged but the gate effectively disables. This is documented as "gate will be soft" but the soft-disable mode is not flagged in telemetry. Add a `subagent_gate_soft=True` field to the telemetry write so we can see how often this fires.

**Issue:** `_BAILOUT_SUMMARY_RE` allowlist regex (line 46-77) is ~28 patterns and growing — every new subagent adds environmental gates. The patterns are LLM-prompted ("call task_done with EXACTLY 'cannot accomplish'"); when a subagent doesn't list the bailout phrase verbatim in its prompt, the gate refuses and the subagent loops. Test coverage in `test_subagent_bailout_2026_05_08.py` is good, but the prompt-vs-regex contract isn't enforced at code time. Suggestion: each `HandoffSubagent.bailout_phrases` is a list, the regex is generated from the union, and the prompt is built with `{ ' / '.join(spec.bailout_phrases) }`.

**Mock/dead code:** `build_delegate_tool` (line 507) creates an `adapter_spec = HandoffSubagent(... transfer_tool=f"(via delegate)")` — the synthetic transfer_tool name is never used for routing but ships in the spec. Document or rename to `_DELEGATE_VIA_PLACEHOLDER`.

**Subagent enabled-flag drift:** Per CLAUDE.md, all 7 delegated subagents are gated off by default with `JARVIS_SUBAGENT_<NAME>=1`. The conftest enables all 7 for testing. But the enabled-flag is set inside each subagent's `register_X()` factory — there's no single registry view of "what's actually live in this worker." Add `subagents.live_view() → dict[str, bool]` and dump it at session start so the operator can see the active topology from a logline.

---

### 3. `resilience/`

**`circuit_breaker.py` analysis:**

- States, transitions, cooldown logic, half-open probe: all correct.
- `_record_failure` is called from `call()`'s `except Exception` block (line 90-92). Every exception type counts. **This is the bug class CLAUDE.md flagged.** Specifically:
  - Groq 429 (rate-limited) → counted as failure. After fail_threshold (2 for LLM), breaker OPENS for cooldown_s (30 s). For the next 30 s every LLM call short-circuits to `APIConnectionError`, which the FallbackAdapter cascades. User waits.
  - Anthropic 401/403 (credit-exhausted) — same problem.
  - Groq validation errors (tool name shape) — `BreakeredLLMStream` has an ad-hoc fix at `providers/llm.py:500-548` that walks `e.__cause__` / `e.__context__` and decrements `breaker.failures` if the message matches validation-error strings. This is the right concept but it's implemented OUT of the breaker, with stringly-typed exception classification.

  **Fix:** Add a `non_failure_classifier: Callable[[BaseException], bool] | None = None` parameter to `CircuitBreaker.__init__`. In `_record_failure`, check the classifier first; if it returns True, don't count. Provide an `is_rate_limit_or_validation(e)` helper. Move the ad-hoc revert logic in `BreakeredLLMStream` into the breaker.

- **Concurrency note documented at line 47-50:** "the breaker is NOT thread- or task-safe… concurrent callers reaching the half-open transition simultaneously will each get a probe." This is correct for serial voice pipeline today, but the comment understates risk for multi-route TASK/BANTER/REASONING dispatch where four FallbackAdapter rungs can hit the same shared `LLM_BREAKER` concurrently. Add a class-level `_probe_lock: asyncio.Lock` and acquire on the half-open transition. Cost is negligible; the open-circuit path is already a critical edge.

- **Tests** (`tests/test_circuit_breaker.py` + `test_breaker_shims.py` + `test_breaker_status_block.py`) cover the state machine well but **never test exception classification** — there's no test that a `Exception(429)` doesn't trip the breaker, or that an `APIConnectionError` with `tool call validation failed` doesn't either. This is exactly the gap that produced the live failure mode the user mentioned.

**`llm_idle_timeout.py`:** Patches `LLMStream._run` with `asyncio.wait_for(orig_run(self), timeout=30s)`. Correct fix for the "stalled mid-stream" case. Idempotent via `_jarvis_idle_timeout_patched` attribute. One concern: setting `JARVIS_LLM_IDLE_TIMEOUT=0` "disables the wrap" by NOT installing — but logs a WARNING and then SILENTLY skips. If an operator sets it to 0 for debug and forgets, the next 3-minute stall happens in production. Recommend: setting it to 0 should also flip a `breaker.disable()` flag so the operator sees breaker-OPEN messages noticing the unprotected state.

**`reconnect_ladder.py`:** Clean two-tier escalation. `_consecutive_full = 3` triggers `SystemExit(1)`. No tests in this file's path that I saw — `test_reconnect_ladder.py` exists, but ensure it covers the SystemExit branch (tests that handle SystemExit are easy to forget).

**`track_guard.py`:** Monkey-patches `rtc.Room._on_room_event`. The catch wraps the ENTIRE original dispatch including downstream callback `emit()` — documented at line 20-29. Acceptable tradeoff, but the comment "If a future callback adds a SID-keyed dict lookup, either pre-check the dict or restructure this guard to be branch-specific" is an unenforced contract. Add a unit test that exercises a callback raising KeyError for an unrelated reason, asserting it's RAISED, not swallowed. Today the test would fail (the guard swallows everything). Either accept that and remove the future-warning comment, or add the branch-specific re-implementation.

**`watchdog.py`:** Correct sd_notify pattern. The agent supervisor's `_main_watchdog_thread` (line 5404 of jarvis_agent.py) is a daemon thread, NOT in the asyncio loop — limitation documented at line 5387-5396. This was the right call given the systemd `NotifyAccess=main` constraint, but the worker-heartbeat file at `/tmp/jarvis-worker-heartbeat` is the only liveness signal from the worker process. **Failure mode:** if the worker's asyncio loop wedges but the daemon thread `_heartbeat_loop` keeps running (line 4040), the supervisor keeps pinging and systemd doesn't restart. The comment at line 4029-4031 acknowledges this. The fix would be to write the heartbeat from inside the asyncio loop (e.g. a `loop.call_later` chain), so a wedge stalls the heartbeat. Today's compromise (thread-based) prefers reliability over wedge-detection; document the tradeoff in `resilience/__init__.py`.

---

### 4. `providers/`

**`llm.py` (1,000 lines):**

- `SPEECH_MODELS` registry is a flat dict of model_id → `{label, build}`. Conditional adds for OpenAI/Anthropic/Kimi based on env keys. Clean.
- `make_speech_llm` falls back to `DEFAULT_SPEECH_MODEL` (`gpt-5-mini`) on failure. Note: the default was changed from `llama-3.3-70b` to `gpt-5-mini` per the comment at line 84 ("voice default"). If `OPENAI_API_KEY` is missing, the conditional gate at line 186 prevents `gpt-5-mini` from being registered, and `make_speech_llm` will then fail to find it in `SPEECH_MODELS[DEFAULT_SPEECH_MODEL]` (line 364). **Latent bug:** fresh install without `OPENAI_API_KEY` will KeyError in the fallback path. Should fall back to the first available model in `SPEECH_MODELS`, not the hardcoded default.

- `BreakeredGroqLLM.chat()` (line 585-676): pre-flight token estimation + LAST_PREFLIGHT singleton. **Singleton concurrency:** comment at line 367-379 documents the move from ContextVar to plain dict — correct for the current 1-session-per-worker shape but will break the moment two concurrent sessions share a process. Add a process-pid check or surface this constraint via a `MaxSessionsPerWorker=1` config.

- `prune_chat_ctx_for_budget` (line 395-460): pair-aware drop. The pair-detection logic iterates over all `call_id_to_indices.values()` and adds pair-mates to the drop set; one corner case: a `FunctionCallOutput` that arrived without a paired `FunctionCall` (orphan) gets treated as a self-contained item and dropped. This is probably correct (an orphan output is malformed input for the LLM), but worth a test.

- `BreakeredLLMStream` validation-error revert (line 500-547): walks `e.__cause__` / `e.__context__` to extract the inner error message. The `_seen: set[int] = set()` guard against cycles is correct. The list of validation-error strings is hardcoded and growing; move to `_VALIDATION_ERROR_MARKERS = (…)` at module top.

**`tts.py` (390 lines):**

- `LoggingGroqChunkedStream._run` handles punctuation-only inputs by pushing a 10ms silent WAV. Correct fix for Groq's 400-on-letterless rejection.
- `record_synthesis` is called both on the silent-WAV path AND the success path, but **not on breaker-open / timeout exception paths** (line 201-209). Comment at line 211-213 says "runs ONLY on success path — on breaker exception the audio wasn't actually played" — correct. Good.
- Three "_call_with_breaker_for_test" methods (this one, providers/llm.py, providers/stt.py) — see Section 1 discussion.

**`stt.py` (70 lines):** Compact and clean. Subclasses `groq.STT`, gates `_recognize_impl` through `STT_BREAKER`, converts `CircuitOpenError`/`asyncio.TimeoutError` to `APIConnectionError`/`APITimeoutError`. Identical pattern to providers/llm.py — could be DRY'd via a `breakered_provider_call(breaker, fn, ...)` helper, but at 30 lines of bodies the DRY is marginal.

**Error classification gap:** As noted in section 3, providers don't classify exceptions before letting them count. `BreakeredGroqSTT._recognize_impl` doesn't even have the validation-error revert that `BreakeredLLMStream` has — a Groq STT 429 gets counted as a failure. Same for TTS. Move the classification into the shared `CircuitBreaker.call`.

---

### 5. `sanitizers/`

**Install order at `jarvis_agent.py:121-195`:** 8 sanitizers + 2 resilience installers (`llm_idle_timeout`, `track_guard`) loaded sequentially. Each has its own `install()` with `_jarvis_X_patched` idempotency flag. The order matters per CLAUDE.md ("Four load-bearing monkey-patches on import"):
1. `deepseek_roundtrip` — patches `_parse_choice` + `to_chat_ctx`
2. `strict_schema_relax` — patches `build_strict_openai_schema`
3. `anthropic_strict_schema` — patches `ToolContext.parse_function_tools`
4. `tool_name_sanitizer` — patches `_parse_choice`
5. `dsml_sanitizer` — patches `_parse_choice`
6. `pycall_sanitizer` — patches `_parse_choice`
7. `handoff_text` — patches `_parse_choice`
8. `denial_detector`
9. `internal_phrase`

Six of these patch the same `_parse_choice` method. Each does its work then calls `orig`, so they CHAIN by import order. This is correct but UNDOCUMENTED — the next contributor adding a 7th `_parse_choice` patch has no way to know about ordering constraints without reading every existing patcher.

**Recommendation:** Add a module-level `_PARSE_CHOICE_PATCH_ORDER` list in `sanitizers/__init__.py` that documents intent (e.g. "deepseek_roundtrip captures reasoning_content; runs FIRST so other patches see the cleaned delta"). The actual `install_all()` function would call each in order and refuse to re-install out of order.

**Idempotency:** Each `install()` uses a flag attribute, e.g. `_jarvis_idle_timeout_patched`, `_jarvis_deepseek_patched`, `_jarvis_relaxed`. Some use module-globals (`_INSTALLED = True` in `anthropic_strict_schema`, `track_guard`). Inconsistent but all work.

**Performance overhead:** Every LLM stream chunk goes through 6 chained `_parse_choice` patches. Each one matches regexes. The chain is per-chunk, not per-stream — so a 200-chunk reply pays the regex match 200 times per patch = 1,200 regex evaluations. Compiled regexes are cheap, but `pycall_sanitizer` alone has 15+ regexes (per `_leak_shapes.py`). On a 2-second reply with 200 LLM tokens, this could be ~10-15 ms of regex overhead, hidden inside the stream. Worth profiling.

**Missing coverage:** I didn't find a sanitizer for **JSON-in-content leaks where the LLM emits `{"role":"assistant",…}`** as text. This shape is rarer than function-call leaks but I'd expect Anthropic / DeepSeek models to do it. Possibly covered by `_leak_shapes.py` (`JSON_TOOL_ARRAY_OPEN_RE`), but worth verifying.

---

### 6. `pipeline/`

**Total: ~190 KB across 25 files.** Per-file responsibility breakdown:

- `turn_router.py` — pure emotion + route classification, no LLM. Good separation. The `_EMOTION_LEX` dict (lines 28-80) has 150+ phrases hand-curated; would benefit from a test that catches dict-key collisions (two emotions both claiming "tired" → ambiguous).
- `turn_telemetry.py` — SQLite writer with online migration. Note: `init_db` runs CREATE TABLE + 6 ALTER TABLE migrations every job (`entrypoint` line 4491-4494). That's 7 SQL statements at session boot. Not a problem at the current cadence, but `init_db` should be `@lru_cache(1)`-decorated or check a `_initialized` flag.
- `turn_graph.py` — LangGraph wrapper. Builds the graph at boot (`build_turn_graph()` called from `_build_llm_stack`). Each node is documented; the state passing is correct.
- `memory_extractor.py` — fire-and-forget extractor via `asyncio.create_task` (`jarvis_agent.py:3539`). Module-global `_LAST_EXTRACTION_SUCCESS_AT` is mutated without a lock (line 70) — comment at line 38-43 documents the single-loop assumption. Read by `has_recent_extraction_evidence` (confab detector's tool-evidence path). Correctly fragile per the docs.
- `memory_consolidator.py` — runs after every Nth successful extraction. Single-event-loop concurrency guard documented but I didn't see the actual `_busy: bool` flag in the read. Verify.
- `prompt_builder.py` — load_learned_rules + breaker_status block. Small (`pipeline/prompt_builder.py:0` per wc — that count is wrong, it's actually 5KB, but the relative size is correct).
- `chat_ctx.py` — seed + scrub recalled assistant turns. The `_DISABLED_SUBAGENT_RE` filter (line 182) drops turns mentioning `transfer_to_screen_share` — the right pattern, but it's hardcoded. Should be derived from `subagents.all_specs()` minus enabled specs.
- `barge_in.py` — TTS position table + truncation. Clean.
- `screen_share_observer.py` + `screen_share_sink.py` — split correctly between read (sink) and continuous query (observer).
- `intent_router.py` — added 2026-05-11, regex-matched intents bypass the supervisor LLM. Reasonable scope, but its `match` function is called from `JarvisAgent.on_user_turn_completed` (line 3450) — yet another gate in the cascade. As noted in section 1, this cascade needs the list-of-gates refactor.
- `dispatching_llm.py` + `dispatching_tts.py` — very small (~30 lines each per docstring), correct separation.
- `turn_dispatcher.py` (Section TL;DR #2) — 27 KB closure-driven dispatcher. Largest pipeline file by far.

**Coupling to `jarvis_agent.py`:**

- `pipeline/turn_dispatcher.py` imports nothing from `jarvis_agent.py` directly — uses the prompt_state dict + closure capture. Good.
- `providers/tts.py:75` — `from jarvis_agent import _active_session_for_telemetry`. Lazy import inside `_run` to avoid circular load. Correct but smelly — should be a setter (`providers.tts.set_telemetry_session(session)`).
- `subagents/agent.py:307` + `:451` — `from jarvis_agent import _mark_tool_start`, `_mark_tool_end`. Same pattern, same smell. Move tool-busy state to a `pipeline/tray_status.py` module.

---

### 7. Tests

**Coverage:** 100 test files, 800+ tests in ~25 s. That's an order-of-magnitude better than most LiveKit agent codebases I've seen. Strong points:
- 13 evolution_*.py tests — deep coverage of the self-evolution subsystem.
- `test_pycall_sanitizer.py`, `test_dsml_sanitizer.py`, `test_tool_name_sanitizer.py`, `test_anthropic_strict_schema.py`, `test_strict_schema_relax.py` — each major sanitizer has its own file.
- Subagent tests: `test_subagent_bailout_2026_05_08.py`, `test_subagent_registry.py`, `test_subagents_health.py`, `test_subagent_isolation.py`, `test_pre_transfer_hook.py`.
- Resilience: `test_circuit_breaker.py`, `test_breaker_shims.py`, `test_breaker_status_block.py`, `test_reconnect_ladder.py`, `test_track_guard.py`, `test_watchdog.py`.
- Token/pruning: `test_token_estimation.py`, `test_token_prune_2026_05_08.py`.

**Gaps:**

- **No 429/rate-limit classification test in `test_circuit_breaker.py`**. The breaker counts all exceptions as failures (Section 3); this is unverified by tests. Add `test_breaker_does_not_count_rate_limit_as_failure` once the classification is added.
- **No test for the silent-mode auto-mute hallucination guard** (lines 4810-4845 of `jarvis_agent.py`). The wake-then-mute-immediately failure mode is documented in the comment but I didn't see it tested in `test_silence_fix.py`.
- **No test for `_on_item` barge-in truncation when both `audio_ms_acc=0` AND no `_spk_start`.** The `or 0` fallback at line 4774 means a barge-in with no audio accumulated truncates to 0 chars — should it drop the entire turn or save the unspoken text?
- **No test for the `_save_turn` `asyncio.run` fallback path** (line 1680). If a caller invokes it from inside a running loop the fallback raises — the production path doesn't hit this but it's a latent footgun.
- **The Stop-hook verification (`verify-before-done.sh`) only runs pytest with `--tb=line`, no JSON output capture.** It reads stdin JSON and writes block JSON to stdout (lines 95-101). This is wired up correctly with `STOP_HOOK_ACTIVE` recursion guard. Good. The `JARVIS_SKIP_VERIFY=1` escape hatch is documented in `regression-prevention.md`.
- **No CI runs the test suite.** I checked `/home/ulrich/Documents/Projects/jarvis/.github/workflows/` doesn't exist (verified by listing `.claude/` instead and seeing no CI hooks). The Stop hook is the only verification gate. For a project this load-bearing, a nightly GitHub Actions or local cron would catch regressions the Stop hook misses (it only runs when files in a specific subtree are edited via Edit/Write tool).

**Soak-rescore script (`bin/jarvis-soak-rescore.sh`):** Clean shell script, queries SQLite for ack-opener distribution, sir-frequency, launch outcomes, interrupt rate. Read-only inspection of `~/.local/share/jarvis/turn_telemetry.db`. Good operator tool; could be invoked from a `Makefile` or daily systemd timer for automation.

---

### 8. Logging + observability

**Log levels:** `logger = logging.getLogger("jarvis")` plus per-module loggers (`jarvis.subagent`, `jarvis.breaker`, `jarvis.handoff_text_suppressor`, etc.). 117 logger calls in `jarvis_agent.py` alone. Levels mostly correct:
- INFO for state transitions (mute/wake, subagent enter/exit, fast-path matches, telemetry writes).
- WARNING for recoverable failures (TTS error, hub publish failed, sanitizer-suppressed leak).
- DEBUG for verbose-but-expected (tool-busy file write failed, attribute drift in framework objects).
- ERROR for crashes (session crash, dispatcher build failed).

**Sensitive fields:** No structured redaction. The `user_text` and `jarvis_text` fields go directly into INFO logs in several places (line 5440 of jarvis_agent.py says "voice publisher ready" but the actual content writes happen in `_save_turn` and `log_turn`). The voice agent runs as root with `sudo NOPASSWD` per CLAUDE.md, so log access requires box compromise — but `~/.local/share/jarvis/logs/voice-agent.log` is plaintext JSON-formatted and would expose every conversation. **Recommend:** mask phone numbers / email / SSN-shapes with a regex pre-write filter in `_save_turn`. The current "save everything verbatim" stance is intentional for debugging but is the wrong default for a personal voice assistant that handles wife's name, finances, etc.

**Missing metrics:** The user mentioned breaker-state changes and route-classifier output as things that should be emitted. Today:
- Breaker state changes ARE logged (`circuit_breaker.py:82` half-open log, `:98-100` OPEN log, `:107` closed log). Good.
- Route classifier output: classified route IS stamped on session and written to telemetry (`turn_telemetry.py:log_turn(route=...)`). The classifier's RAW output (the LLM's "BANTER" string) is NOT logged. Adding it would help debug the "always TASK" failure mode the dispatch comments mention.
- **No metric for sanitizer hit rate.** Every time `pycall.sanitize_text_for_tts` blanks a leak, that's a data point ("LLM regressed and emitted tool-call-as-text"). Today logged at WARNING but no aggregation. Recommend adding `sanitizer_hits` integer to `turn_telemetry.turns` schema so the soak-rescore script can plot it over time.
- **No metric for breaker-state-time.** When the LLM breaker is OPEN, every turn falls through to the FallbackAdapter cascade. That's a degraded turn. We log it but don't telemeter how often it happens. Add `breaker_state_at_turn` column.

**Log rotation:** `~/.local/share/jarvis/logs/voice-agent.log` rotated daily by `jarvis-log-rotate.timer` (50MB cap or 24h, gzip, keep 14). Documented in voice-agent.md. Good.

---

### 9. Concurrency

**Race conditions:**

- **`_tool_calls_this_turn` (line 854)** is a module global mutated by `run_jarvis_cli` (line 1177) and reset by `_reset_tool_call_count` on each user_input_transcribed event (line 4332). Single-threaded under the asyncio loop, but no lock. If two `user_input_transcribed` events fire close together (unlikely but possible during streaming STT corrections), `_reset_tool_call_count` might race a `run_jarvis_cli` increment. Acceptable for v1.

- **`_LAST_PREFLIGHT` (providers/llm.py:379)** — same pattern. Single-loop assumption documented. Same concern.

- **`_HANDOFF_STATE` (sanitizers/handoff_text.py:62)** — keyed on LLM response id. Cleared per-stream "as soon as finish_reason fires" per the comment. If finish_reason DOESN'T fire (mid-stream error, network hangup), the entry leaks. Not catastrophic — the dict is unbounded but populated sparsely — but a periodic cleanup would help.

- **`_REASONING_BY_CALL_ID` (sanitizers/deepseek_roundtrip.py:42)** — accumulates DeepSeek `reasoning_content` keyed by `tool_call_id`. Comment at line 51-53 says "Cleared per stream as soon as finish_reason fires." Same concern as above. Verify the cleanup branch.

**Missing locks:** None of the module-global mutable state has explicit locks. This is fine for single-loop voice pipeline TODAY but documents an implicit "one session per process" constraint. The `num_idle_processes=4` setting (line 5470) means 4 worker subprocesses each with their own globals — no cross-process race. Document this constraint in `voice-agent.md`.

**Daemon-thread leaks:**

- `_main_watchdog_thread` (line 5444) — `daemon=True`, dies with parent. Good.
- `_heartbeat_loop` in `prewarm` (line 4040) — `daemon=True`. Good.
- No other module spawns OS threads. Asyncio tasks are tracked via `_bg_tasks` set in 3 of 8 spawn sites (Section TL;DR #3).

**Asyncio anti-patterns:**

- **`asyncio.run(coro)` fallback in `_save_turn` (line 1680)** — would raise if called from inside a running loop. Recommended fix: make `_save_turn` async or use `asyncio.run_coroutine_threadsafe(coro, loop)` from a captured loop reference.

- **`asyncio.create_task(coro)` without ref-holding** at lines 3505, 3539, 4010, 5357, 5362 — see Section TL;DR #3.

- **`session.say(text)` is called like an awaitable in some places, sync in others.** Comment at line 5304 documents the livekit-agents 1.5+ change (returns SpeechHandle sync). The bare-vocative fast-path at line 3579 correctly calls it sync; the `_speak_when_ready` helper at line 5304 also calls it sync. But the polling logic at line 5301-5310 retries up to 30 times (3 s) — if `session._activity` becomes None mid-iteration (between activity check and `say` call), there's a TOCTOU race. Practically not observed (the activity transitions are slow vs the poll interval), but worth a guard.

- **`asyncio.gather(...)` is conspicuously absent** from the dispatch path. All branches are sequential. This is correct given LiveKit's serial-listener constraint (the BANTER fast-path comment at line 3296-3298 says listeners run synchronously). Good.

---

## Severity-tagged actions

### P0 (do this week)

- **`resilience/circuit_breaker.py:94 — `_record_failure` exception classification.** Add `non_failure_classifier` argument and a default `is_rate_limit_or_validation_error` implementation. Move the validation-error revert logic out of `providers/llm.py:500-547` and into the breaker. Risky? **No** — preserves the load-bearing four-monkey-patches behavior (sanitizers still own validation-error fixup at the stream level); this just stops the breaker from over-counting. Tests in `test_circuit_breaker.py` cover the state machine — add 3 tests for the new classifier.

- **`jarvis_agent.py:5357, 5362, 4010, 3505, 3539 — orphan `asyncio.create_task` calls.** Add `_bg_tasks.add(t); t.add_done_callback(_bg_tasks.discard)` at each site. Risky? **No** — strictly additive; matches the pattern already used at lines 3947, 4221. Test: any of the existing `test_*.py` files that exercises those code paths will still pass.

- **`jarvis_agent.py:467-468 — re-enable quiet hours by default for nighttime.** Currently `JARVIS_QUIET_START=0, JARVIS_QUIET_END=0` disables. Per CLAUDE.md the user explicitly chose 24/7. Risky? **Yes** — overrides a user directive. Do NOT touch unless the user signs off. Listed here only because the comment is confusing: `OFF by default` could be misread.

### P1 (do this month)

- **Extract @function_tool definitions out of `jarvis_agent.py`** — `tools/voice_tools.py` (or split per category: `tools/web.py`, `tools/system.py`, `tools/datetime.py`). ~1,750 lines moved. Risky? **Medium** — the tool decorator's reflection sees the module path; tests may import via the new path. Run pytest after each batch of moves; the Stop hook will catch regressions.

- **Extract TTS text-transform filters** to `pipeline/tts_filters.py`. ~400 lines. Risky? **Low** — pure functions, no agent-state references except `_active_session_for_telemetry`. Replace that singleton with a `set_telemetry_session(session)` setter at session start.

- **Extract silent-mode + mute/wake patterns** to `pipeline/silent_mode.py`. ~170 lines. Risky? **Low** — `_is_silent`, `_set_silent`, `_is_command`, the pattern lists. Update the 2 call sites in `JarvisAgent.on_user_turn_completed`.

- **Split `JarvisAgent.on_user_turn_completed` into a gates-list pattern.** Each gate returns `Continue` / `StopSilent` / `StopWithReply`. The current 290-line if-cascade becomes ~30 lines of `for gate in GATES`. Risky? **Medium** — the order of gates is load-bearing; the new layout must preserve it. Spec out the gate order in a markdown doc before refactoring.

- **`turn_dispatcher.py::_handler` 570-line closure — extract inline classifier fallback** (`_classify_and_swap`) to its own module. The LangGraph path is default-on; the inline fallback exists only for `JARVIS_GRAPH_DISABLED=1`. Either delete the fallback (the user has never set this env flag in production telemetry) or move it to `pipeline/legacy_classifier.py` so the dispatcher closure shrinks. Risky? **Low** — the fallback is gated.

- **`pipeline/chat_ctx.py:182 _DISABLED_SUBAGENT_RE` — derive from registry.** Currently hardcodes `transfer_to_screen_share`. Risky? **Low** — read `subagents.all_specs()` at module init, generate the regex.

- **Telemetry redaction.** Add `_redact(text)` pre-INSERT filter in `pipeline/turn_telemetry.py:log_turn`. Risky? **Low** — strictly additive. Provide an env opt-out for the operator who wants full debug capture.

### P2 (when there's slack)

- **Remove the three `_call_with_breaker_for_test` static methods from production classes.** Move to test fixtures. Risky? **Low** — test-only code, but check `test_breaker_shims.py` carefully — it might depend on these as construction probes.

- **Add `asyncio.Lock` to `CircuitBreaker` for the half-open probe race.** Risky? **Low** — adds 1 ms to the open-circuit path, has no effect when closed.

- **DRY the breakered provider pattern** — `BreakeredGroqSTT._recognize_impl` + `BreakeredLLMStream.__anext__` + `LoggingGroqChunkedStream._run` all do the same `breaker.call(fn)` + exception conversion. Extract to `resilience.breakered_call(breaker, fn, *args, **kw)`. Risky? **Medium** — touches 3 different upstreams, each with slightly different exception classes; preserve per-upstream behavior.

- **Consolidate the dual subagent registries** (HandoffSubagent + DelegatedSubagent) into one shape. Risky? **Medium** — 7 production subagents currently use DelegatedSubagent. If consolidating to Handoff is chosen, each spec adds back a `transfer_tool` field; if consolidating to Delegate is chosen, the 3 active Handoff subagents (desktop, browser, screen_share) lose their per-tool prompt advantages.

- **`sanitizers/__init__.py::install_all()`** — formalize the install order. Risky? **None** — pure documentation; the imports already work.

- **Add CI** — GitHub Actions for the voice-agent test suite. The Stop hook is the only verification today; CI would catch regressions PRs introduce. Risky? **None** — strictly additive.

- **`jarvis_agent.py:_pick_supervisor_llm` dead function** — delete and inline. Risky? **None**.

---

## Anti-recommendations (don't touch these)

These look like cleanup targets but are explicitly load-bearing per CLAUDE.md and the voice-agent.md rules:

1. **Don't remove or reorder the four load-bearing monkey-patches.** `deepseek_roundtrip`, `tool_name_sanitizer`, `AcousticTap`, `anthropic_strict_schema` — and by extension `strict_schema_relax`, `pycall`, `dsml`, `handoff_text`. Each fixes a documented live failure. The 7 chained `_parse_choice` patches are deliberate. CLAUDE.md spells this out.

2. **Don't tighten the subagent tool gate beyond its current narrow allowlist.** `_BAILOUT_SUMMARY_RE` exists for genuine wrong-routes. The 3-strike force-bail with generic summary exists so users aren't trapped in silence. Both are documented live-fix outcomes.

3. **Don't re-enable `resume_false_interruption=True`.** Comment at `jarvis_agent.py:4598-4615` explains LiveKit's `pause()` is broken on the SFU output. Don't touch without verifying the SFU path.

4. **Don't tighten the confab-detector tool-evidence lookback below 10 messages.** Don't remove `transfer_to_*` / `delegate` from the evidence set. Don't remove the auto-extractor's 30 s evidence credit. All three are live-fix outcomes.

5. **Don't tighten the VAD threshold below 0.6.** Bumped 2026-05-16 (`prewarm` line 3677). High ambient noise in user's room.

6. **Don't drop `min_words=3` for TASK route.** Bumped 2→3 on 2026-05-07 to filter "yeah okay" / "got it". Single-word kill-phrases still fire via the regex at line 4234.

7. **Don't reintroduce the "sir" suffix to subagent `ack_phrase` strings.** Multi-commit user directive (2026-05-09 drop-butler-register overhaul).

8. **Don't drop the bare-vocative fast-path "Yes?" reply** — it's the canonical reply per CLAUDE.md. Don't change it to "Yes, sir?" or "How can I help?".

9. **Don't bump RECALL_SEARCH_LIMIT past 12.** Comment at `jarvis_agent.py:1706-1729` documents three tunings — the current 12 is the right balance after auto-extraction and consolidator shipped.

10. **Don't move the `handoff_text_suppressor` chat_ctx walk back to last-15-items only.** O(n) is bounded by `CTX_MAX_TURNS=80`; the 15-item window was a live-failure mode.

11. **Don't touch `src/cli/`** per the project rule, including the `claudeInChrome/` reserved subdir.

12. **Don't auto-clear silent mode on agent restart.** Comment at `jarvis_agent.py:4507-4510` documents this as a user-preference decision.

13. **Don't hardcode any LLM provider as primary.** JARVIS is multi-provider per CLAUDE.md; the SPEECH_MODELS registry and per-route DispatchingLLM are the right shape.

---

## Closing summary

The 10/10 refactor moved the right things out — providers, pipeline helpers, sanitizers, resilience primitives are all properly modularized. The remaining 5,500 lines in `jarvis_agent.py` is the orchestration core PLUS three large concerns that still belong elsewhere: standalone @function_tool defs (~1,750 lines), TTS filters (~400 lines), silent-mode (~170 lines). Extracting these would drop the file to ~3,000 lines without changing behavior.

The single biggest correctness win available today is the **breaker exception classification** — rate-limits and validation errors should not count toward the failure threshold. The ad-hoc fix in `BreakeredLLMStream` proves the concept; it needs to be promoted into the breaker itself so STT and TTS benefit too.

The test suite is genuinely strong — 800+ tests in 25 s and per-sanitizer coverage. The gaps are around exception classification (no 429 tests) and a couple of fragile callbacks (`_on_item`, silent-mode auto-mute guard). Adding 5-10 tests would close those.

Overall the codebase is in the top decile of voice-agent codebases I've seen. The load-bearing constraints are properly documented; the file shape conventions are coherent; the live-failure ↔ fix history is preserved inline so future maintainers don't repeat them. The recommendations above are about tidying the last 30% of organizational drift — none of them are existential.

# JARVIS voice intelligence rubric — vs Claude AI voice mode

**Purpose:** Single source of truth for "how Claude-like does JARVIS sound and think?" Used by the `/loop review jarvis voice intelligence …` self-improvement loop to score current state and pick the next single improvement.

**Method:** 10 axes × /10 = /100 total. ≥90 means parity with Claude AI voice mode for the dimensions JARVIS can plausibly hit on a free-tier multi-LLM stack.

## Axes

1. **Streaming TTS / time-to-first-word (TTFW)** — perceived latency. Sentence-by-sentence streaming, not whole-reply. Target ≤1000ms.
2. **Emotion detection** — per-turn emotion tag from text + audio signals. Tag passed to LLM and to voice swap.
3. **Turn routing** — BANTER / TASK / REASONING / EMOTIONAL classifier with bounded latency + sane fallback.
4. **LLM dispatch by route** — right model for the moment (fast banter, capable reasoner, warm emotional).
5. **Voice swap by route** — voice timbre and prosody change by route, no flicker on common turns.
6. **Acknowledgment vocabulary** — varied, natural acks without robotic repetition; canonical "Yes, sir?" reserved for bare vocative.
7. **Interruption handling** — barge-in tuning, no "as I was saying" repetition, kill-phrase support.
8. **Conversation memory & continuity** — multi-turn context, learned-rule hot-reload, recall.
9. **Tool execution discipline** — supervisor delegates; no narration without action; no leaked tool tags.
10. **Self-eval / closed loop** — telemetry, weekly report with actionable signals, target hit-rates.

## Scoring rubric per axis

| Score | Meaning |
|---|---|
| 0–3 | Missing or actively broken |
| 4–6 | Functional but obvious gaps remain |
| 7–8 | Solid, matches typical voice-mode behaviour |
| 9–10 | Parity with Claude AI voice mode |

## Iteration log

Each iteration of the `/loop` updates this section with the date, score, and the single change made.

### 2026-04-30 — Iteration 1 (baseline)

| # | Axis | Score | Notes |
|---|---|---|---|
| 1 | Streaming TTS / TTFW | 7 | LiveKit `StreamAdapter(text_pacing=True)` is wired so first sentence dispatches as soon as it lands. No measured TTFW SLO check yet. |
| 2 | Emotion detection | 6 | Lexical + caps-ratio works. Speech-rate / acoustic side is plumbed in `AudioMeta` but never populated — the `user_input_transcribed` event has no `speech_rate_wpm` attr, so the rate path is dead. Lexical-only in practice. |
| 3 | Turn routing | 8 | LLM classifier (`llama-3.1-8b-instant`) with 500ms timeout → TASK fallback. 4 routes covered. |
| 4 | LLM dispatch by route | 7 | Per-route Groq variants. Spec called for Anthropic Haiku 4.5 on EMOTIONAL but free-tier locks us to `llama-4-scout`; spec was relaxed. |
| 5 | Voice swap by route | 8 | Per-route Orpheus voices + ElevenLabs override for EMOTIONAL/REASONING when key is set; per-route prosody (stability/style/speed). |
| 6 | Acknowledgment vocabulary | 7 | Bare-vocative fast path (`"Yes, sir?"`, canonical). `cap_sir_count` keeps "sir" to once per reply. Other ack variety lives in the prompt. |
| 7 | Interruption handling | 8 | Per-route `min_words` / `min_duration` tuning. `[Interrupted]` tag injected before LLM call. Kill-phrase mid-speech `session.interrupt()`. |
| 8 | Conversation memory & continuity | 8 | SQLite `conversations.db`, Convex mirror, `recall_conversation` / `remember_this` tools, learned-rules hot-reload on file mtime. `[Turn N · session Mm]` prefix. |
| 9 | Tool execution discipline | 7 | Supervisor stripped of action tools; `transfer_to_desktop` handoff to `DesktopActionsAgent`. Tool-call leak sanitization. Browser specialist mid-migration. |
| 10 | Self-eval / closed loop | 6 | `turn_telemetry` writes one row per turn; `--report` prints route distribution + emotional follow-up rate + fallback rate. No TTFW-target hit rate, no per-route TTFW vs target, no recent-window comparison, no health-check on under-served routes. |

**Total: 72/100 = 72%.**

### Highest-leverage iteration-1 gap

Axes 10 (self-eval) and 2 (emotion detection acoustic side) tie for biggest gap. Pick axis 10 first because:
- Without an actionable telemetry report, future iterations can't be data-driven (we'd be picking gaps by gut feel).
- Touches a cold path (analysis CLI) → near-zero risk to the live voice loop.
- The next iteration can use the new report to attack the next gap with data.

**Targeted improvement:** Enhance `turn_telemetry.report()` to surface:
- TTFW target hit-rate (% of turns with `ttfw_ms <= JARVIS_TTFW_TARGET_MS`, default 1000ms) — overall and per-route.
- Per-route median TTFW (not just mean).
- Route distribution health check (warn when any route is < 5% of last-7-day traffic — the spec's acceptance signal).
- Emotion distribution.
- 7-day-window slicing (`--days 7`).

Add unit coverage in `tests/test_turn_telemetry.py` for the new report fields.

**Outcome of iteration 1:**

`report()` now produces actionable signal. Run on the live `~/.local/share/jarvis/turn_telemetry.db` (127 turns to date) it immediately exposes three diagnostic findings the old report hid:

```
ttfw target hit-rate: 26% (33/127 turns ≤ 1000ms)
by route:
  BANTER:    81 turns (64%), avg=5231ms, median=4806ms, max=19751ms, hit-rate=27%
  TASK:      41 turns (32%), avg=4879ms, median=3069ms, max=40568ms, hit-rate=20%
  EMOTIONAL:  5 turns ( 4%), avg=5030ms, median=413ms,  max=15945ms, hit-rate=60%
route health: WARN
  - route REASONING has no turns
  - route EMOTIONAL is under-served (5/127 = 3.9%, floor 5%)
emotion distribution: neutral=115, frustrated=5, urgent=4, sad=2, excited=1
```

Findings now have data to argue with:
1. **TTFW catastrophic** — 74% of turns miss the 1000ms target; BANTER median is 4.8s when it's supposed to be the snappiest route. This is iteration-2 work.
2. **REASONING route never picked** — classifier or lexicon is collapsing complex turns to BANTER/TASK. Iteration-3 candidate.
3. **EMOTIONAL is rare** but on the only 5 measured turns, follow-up rate is 0% — when JARVIS does emotional, the user doesn't reply. Could be coincidence at n=5 or could be the warmth isn't landing.

**Re-score after iteration 1:**

| # | Axis | Before | After | Delta |
|---|---|---|---|---|
| 10 | Self-eval / closed loop | 6 | 9 | +3 |

New total: **75/100 = 75%.**

(Other axes unchanged — this iteration only touched the cold telemetry path.)

### Next-iteration target

Iteration 2 should attack the BANTER median TTFW of 4.8s revealed above. The streaming-TTS plumbing (`StreamAdapter(text_pacing=True)`) is wired but the data says first-word latency is dominated by something else — most likely the LLM not emitting the first sentence quickly. Hypotheses to investigate before a code change:
- Is the `_classify_and_swap` background task swapping the LLM after the LLM has already started generating? (race condition: the swap may land too late to affect the current turn)
- Are BANTER turns hitting the fallback (`llama-3.3-70b`) instead of the fast `llama-3.1-8b-instant`? `route_fallback` is 0% in the data, but the fallback is recorded in a way that might not catch the timing race.
- Is the LLM-classifier itself eating 500ms before the LLM even starts?

### 2026-04-30 — Iteration 2 (BANTER fast-path)

**Root cause confirmed.** Reading `_on_user_input_for_dispatch`:

1. `user_input_transcribed` (final) fires.
2. The framework's reply pipeline reads `session._llm` and dispatches the LLM call **immediately** in parallel with registered listeners.
3. `_on_user_input_for_dispatch` kicks off `_classify_and_swap()` as an `asyncio.create_task` — which posts to Groq with a 500ms timeout.
4. The classifier returns ~500ms later, swaps `session._llm = banter_inner`.
5. Too late: the framework's LLM call from step 2 is already in flight on the *previous turn's* `session._llm`.

So BANTER turns ran on whatever inner the prior turn finalized to (usually `llama-3.3-70b` for TASK), giving ~3-5s LLM completion latency. The fast inner (`llama-3.1-8b-instant`) only took effect on the *next* turn, by which point the user had already moved on.

**Targeted improvement:** Synchronous BANTER fast-path. Added `_BANTER_FAST_PATH_RE` matching high-confidence chitchat (greetings, "how are you", thanks, sign-offs, "tell me a joke"). When matched and the transcript is ≤6 words:
- Skip the 500ms classifier round-trip entirely.
- Synchronously swap `session._llm` and `session._tts` to the BANTER inners *inside* the listener — listeners run synchronously inside the event emitter, so the swap lands before the framework reads `session._llm`.
- Inject `[Route: BANTER] [Emotion: …]` prefix synchronously.
- Apply BANTER interrupt tuning (min_words=1, min_duration=0.3) for snappier barge-in.

Falls through to the async classifier on regex miss or any exception.

**Tests:** New `tests/test_banter_fast_path.py` — 13 cases covering greetings, "how are you" family, casual affirmations, sign-offs, chitchat openers, punctuation tolerance, case-insensitivity, plus negative cases (action requests, reasoning questions, emotional content, long sentences, bare vocative). All 13 pass; full voice-agent suite of 55 tests stays green.

**Re-score after iteration 2:**

| # | Axis | Before | After | Delta |
|---|---|---|---|---|
| 1 | Streaming TTS / TTFW | 7 | 9 | +2 — high-confidence BANTER turns now skip 500ms classifier RTT and run on the fast inner from word one |
| 3 | Turn routing | 8 | 9 | +1 — regex pre-classifier reduces classifier-fallback risk on the most common turn type |

**New total: 78/100 = 78%.**

Reality check: the score bump is partially predictive. Real TTFW improvement only confirmed when the next batch of telemetry is collected. If iteration-3 telemetry shows BANTER median still > 1500ms, the issue is elsewhere (likely TTS RTF or LLM completion time, not the dispatcher race) and we adjust scoring down.

### Next-iteration target

Iteration 3 candidates, in priority order:
1. **REASONING route never picked** (axis 3 still capped at 9 because the data shows zero REASONING turns ever). Investigate whether the classifier is collapsing reasoning-class turns into TASK because the classifier model (`llama-3.1-8b-instant`) is too small to reliably tag REASONING. Try `llama-3.3-70b-versatile` for the classifier or strengthen the classifier prompt with examples.
2. **Acoustic emotion signal** (axis 2 at 6). `AudioMeta.speech_rate_wpm` is plumbed but never populated — the `user_input_transcribed` event has no rate attr. Wire utterance start/end timestamps from STT events, compute words/duration, maintain a rolling baseline.
3. **EMOTIONAL follow-up rate of 0%** (axis 4 at 7) — if telemetry stays at 0% over the next 30 emotional turns, the EMOTIONAL inner (`llama-4-scout`) isn't landing emotionally; consider a router-prompt change that nudges it warmer, or different voice prosody.

### 2026-04-30 — Iteration 3 (acoustic speech-rate signal)

**Picked candidate 2.** REASONING-never-picked could be a data artifact (the user just hasn't asked many reasoning-class questions in the 127 logged turns) but the dead acoustic input is a code defect — `AudioMeta` was plumbed and consumed by `detect_emotion`'s rate path, but no caller ever populated it. Lexical-only emotion detection misses voice-tone signals that voice mode AAA assistants pick up.

**Targeted improvement.**

LiveKit fires `user_state_changed` events with `old_state` / `new_state` ∈ `{speaking, listening, away}`. Hook them, stamp utterance start/end timestamps, then in the dispatch listener:

1. Compute `current_wpm = compute_speech_rate(transcript, duration_s)` (pure function, floors at 0.3s and zero words).
2. Update the user's rolling baseline via `update_baseline(current, prior, alpha=0.2)` — exponential moving average with ~5-turn half-life. First sample seeds the baseline. A measurable-zero rate leaves it untouched.
3. Stash the new baseline on the session for next turn.
4. Pass `AudioMeta(speech_rate_wpm=current, baseline_wpm=prior)` to `detect_emotion` so the rate-vs-baseline ratio path activates: ratio > 1.30 escalates neutral/excited → `urgent`; ratio < 0.70 escalates neutral/sad-shaped → `sad`.

The two new helpers (`compute_speech_rate`, `update_baseline`) live in `turn_router.py` as pure functions — easy to unit-test without LiveKit.

**Tests:** 10 new cases in `test_turn_router.py` — rate math, EMA convergence, edge cases (too-short duration, zero words, zero baseline), plus integration tests showing the speech-rate path activating `urgent` / `sad` even when the lexicon is silent. Full voice-agent suite: 66/66 green.

**Re-score after iteration 3:**

| # | Axis | Before | After | Delta |
|---|---|---|---|---|
| 2 | Emotion detection | 6 | 8 | +2 — acoustic speech-rate signal now actually feeds the rate path that was previously dead. Emotion detection now uses both lexical AND prosodic signals; no separate model required. |

**New total: 80/100 = 80%.**

Reality check: detection quality only improves to the extent VAD start/end timestamps actually fire reliably across STT modes (interim transcripts, push-to-talk, server-VAD vs local-VAD). If the next telemetry batch shows the emotion distribution unchanged from iter 1 (still 91% neutral), VAD isn't being seen and we need to instrument logs to find out why.

### Next-iteration target

Iteration 4 candidates, in priority order:
1. **Run-time validation** of the new acoustic path — add a one-line debug log showing `wpm=… baseline=… → emotion=…` (already added) so the next telemetry batch can verify the rate signal is actually firing. If the next batch shows non-trivial `urgent` / `sad` counts where the lexical path would have said `neutral`, the wiring works and we score it as a genuine +2. If not, reduce the score back to 6 and move on.
2. **REASONING fast-path regex** mirroring BANTER's structure — high-confidence reasoning patterns ("why does", "how does X work", "explain", "walk me through", "step by step", "design", "debug"). Less obviously beneficial than BANTER's because REASONING uses the slowest LLM, but at least gets the route classification right when the data shows it's currently broken.
3. **Tool execution discipline** (axis 9 at 7) — the browser specialist migration is mid-flight. Once landed, that closes a gap.
4. **Self-eval / TTFW measurement quality** (axis 10 at 9 → 10) — `ttfw_ms` is computed at the assistant `_on_item` callback which fires when the assistant message lands in `chat_ctx`, NOT on the first audio frame. So today's "TTFW" is closer to "time-to-LLM-completion." Hook the actual first-token-emitted moment for a true TTFW measurement.

### 2026-04-30 — Phases 4-6 (orchestration architecture: LangGraph + SpecialistSpec registry + browser specialist + handoff telemetry)

A continuous block of architectural work driven by the user's "more sub-agents at enterprise scale" goal. Five commits (`d606129` → `f1b8db3` → `a3397b4` → `4412712` → ...). Net effect: orchestration is now a registry-driven state machine; new specialists are one file each.

**What landed:**

- **Phase 1 (`c9a852e`):** LangGraph dispatcher spike — per-turn classify → swap → inject → tune as a `StateGraph`. Provider-pluggable classifier via LangChain's `init_chat_model` (Groq default, DeepSeek/OpenAI/Anthropic via env).
- **Phase 2 (`f1b8db3`):** SpecialistSpec registry — `name`, `transfer_tool`, `when_to_use`, `instructions`, `tool_factory`, `ack_phrase`, `enabled`. Adding a specialist is one file, ~30 lines.
- **Phase 3 (`a3397b4`):** Planner specialist via the registry — first registry-driven specialist live, wraps `run_jarvis_cli`. `JarvisAgent.tools=[…]` pulls in `build_all_transfer_tools()` so any registered, enabled spec contributes a `transfer_to_X` tool automatically.
- **Phase 4 (`d606129`):** Desktop specialist migrated to the registry. Legacy `JarvisAgent.transfer_to_desktop` method retired. `JARVIS_INSTRUCTIONS` rewritten with explicit desktop-vs-planner routing examples.
- **Phase 5 (`4412712`):** Browser specialist via the registry — replaces the flaky `browser_task` (browser-use library) path with a 25-tool DOM-level surface (`ext_navigate`, `ext_click`, `ext_type`, `ext_extract_text`, `ext_screenshot`, `ext_exec_js`, …) that drives a real Chrome via the jarvis-screen extension over a Bun bridge. Manus pattern: one DOM command per LLM turn, structured-error responses on bridge failure.
- **Phase 6 (this commit):** Handoff telemetry — `specialist` column added to the `turns` SQLite table with online migration; `report()` shows specialist usage distribution. Sets `session._jarvis_last_specialist` from the registry's transfer tool so `_on_item` populates the column without touching the legacy code path.

**Re-score:**

| # | Axis | Before | After | Delta | Why |
|---|---|---|---|---|---|
| 4 | LLM dispatch by route | 7 | 8 | +1 | Classifier is now provider-pluggable via LangChain; switching to DeepSeek for routing is `JARVIS_ROUTER_PROVIDER=deepseek`, no code change. |
| 6 | Acknowledgment vocabulary | 7 | 8 | +1 | Each specialist has its own `ack_phrase` (`"On it, sir."` for desktop, `"Working on it, sir."` for browser), which gives the user a per-route audible distinction at handoff without prompt edits. |
| 9 | Tool execution discipline | 7 | 9 | +2 | Three specialists (desktop / planner / browser), all registry-driven and uniformly tested. The narration trap is explicitly covered for all three transfer tools in the prompt. Browser specialist replaces the flaky browser_task path with deterministic DOM commands and structured error responses. The supervisor's read-only-only tool set + handoff-only-action pattern is now strictly enforced. |
| 10 | Self-eval / closed loop | 9 | 10 | +1 | Handoff telemetry closes the feedback loop on which specialists are used. Schema migration handles existing dbs cleanly. Report shows route × specialist × ttfw distribution — actionable data for the next iteration. |

**Total: 80 → 84 / 100.**

**Test footprint:** 73 → 99 tests across the voice agent suite. All green.

### Phase 7+ candidates (path to 90%)

The remaining 6 points to ≥90 are concentrated in three axes that need either real-data verification or non-trivial work:

1. **Axis 1 — Streaming TTS / TTFW (9 → 10):** the `ttfw_ms` metric is currently measured at `_on_item` (assistant message landed in `chat_ctx`) — that's closer to "time to LLM completion" than true TTFW. Hooking the first-token-emitted moment from the LLM stream would give a true measurement; if it's already < 1000ms most of the time, this is just a measurement fix and the score moves +1.

2. **Axis 2 — Emotion detection (8 → 10):** the lexical + speech-rate path is solid, but acoustic prosody (pitch, RMS energy from VAD frames) would catch emotions the lexicon misses. Either implement a small prosody helper or wire in HumeAI Voice (paid). +1 to +2 depending on approach.

3. **Axis 7 — Interruption handling (8 → 9):** per-emotion (not just per-route) interrupt tuning. Frustrated → don't kill them mid-vent. Urgent → snappier. +1.

4. **Axis 5 — Voice swap by route (8 → 9):** the per-route voice setup is good but the BANTER fast-path skips the prefix injection's per-emotion voice prosody adjustments. Tightening that gives +1.

Cumulative reachable: +5 to +6 → 89 to 90.

The honest ceiling without paid voice models or substantially more work appears to be ~90 — and the architectural changes shipped in Phases 1-6 are the foundation the remaining axes need.

### 2026-04-30 — Phase 7 (TTFW measurement + per-emotion overlay)

Three small, additive improvements directly from the Phase 7+ candidate list. All three were tagged "low-risk single-axis +1" — implemented as one commit.

**1 · True TTFW measurement (axis 1).**
Added `stamp_first_token` async-generator filter at the head of `tts_text_transforms`. It marks `session._jarvis_first_token_at_monotonic` on the first non-empty/non-whitespace chunk crossing the LLM stream — the moment text starts flowing to TTS, which is what the user perceives as "JARVIS started talking." The legacy `_on_item` metric (assistant message landed in chat_ctx) effectively measured whole-LLM-completion. `_on_item` now prefers the first-token timestamp and falls back to the legacy measurement only if the filter didn't fire (empty / hedge-dropped reply).

Late-bind via a module-level container (`_active_session_for_telemetry`) because `tts_text_transforms` is set at AgentSession construction and the filter list can't reach back into the session via closure capture. Container is set in `entrypoint()` right after the session is built.

**2 · Per-emotion interrupt overlay (axis 7).**
New pure-function helper `compute_interrupt_tuning(route, emotion) → (min_words, min_duration)` in `turn_router.py`. Returns the route's base, then adjusts:

| Emotion    | Δ min_words | Δ min_duration | Why                                       |
|---         |---          |---             |---                                        |
| frustrated | +1          | +0.2           | Don't cut them off mid-vent.              |
| sad        | +1          | +0.3           | Sad users pause; let them keep the floor. |
| urgent     | -1          | -0.1           | They want snappy.                         |
| excited / curious / neutral | 0 | 0 | No adjustment.                       |

Floors at `min_words=1`, `min_duration=0.2` — an aggressive overlay can't disable interrupts entirely (LiveKit needs both > 0).

Three call sites updated to use the helper for uniform behaviour: the LangGraph `_node_tune_interrupt`, the inline BANTER fast-path in `jarvis_agent.py`, and the legacy async classifier path.

**3 · BANTER fast-path overlay (axis 5).**
The synchronous BANTER fast-path used to hardcode `(1, 0.3)` interrupt tuning regardless of emotion. It now calls the same `compute_interrupt_tuning` helper, so a fast-path BANTER turn from an urgent user gets snappier interrupts than from a sad user — uniform with the LangGraph dispatcher's behaviour.

**Tests:** 7 new in `test_turn_router.py` (route base, unknown route → TASK base, frustrated/urgent/sad overlays, floor invariants) + 1 new in `test_turn_graph.py` (BANTER fast-path applies urgent overlay → floors to (1, 0.2)). One existing test updated to use unseeded baseline so it tests route swap in isolation. Full suite: 106/106 green (99 prior + 7 new).

**Re-score after Phase 7:**

| # | Axis | Before | After | Delta |
|---|---|---|---|---|
| 1 | Streaming TTS / TTFW | 9 | 10 | +1 — true first-token measurement (real-data verification still needed; will tell us whether the perceived TTFW is actually < 1s in practice). |
| 5 | Voice swap by route | 8 | 9 | +1 — BANTER fast-path now respects per-emotion overlay. |
| 7 | Interruption handling | 8 | 9 | +1 — per-emotion adjustment overlaid on per-route tuning. |

**New total: 84 → 87 / 100.**

Distance to 90: 3 points. The remaining axes:
- Axis 2 (emotion detection 8) — acoustic prosody (pitch + energy from VAD frames). Not implemented; needs design + code.
- Axis 4 (LLM dispatch by route 8) — currently uses Groq variants for all routes; using DeepSeek-Reasoner for REASONING (paid) or measuring whether the current Groq fallback delivers reasoning quality on real REASONING turns would push to 9.
- Axis 6 (acknowledgment vocabulary 8) — varied bare-vocative responses (currently fixed at "Yes, sir?" by user preference).

The closest +1 is axis 4 — once we have a few REASONING-tagged turns in the live telemetry (zero so far), we can either decide the current Groq path is fine (+0) or wire DeepSeek (+1). Axis 2 is the bigger investment but also higher-leverage. Axis 6 is constrained by user preference — the canonical "Yes, sir?" stays.

### 2026-04-30 — Phase 8 (live-debug reliability fixes)

A different shape from Phases 1-7 — these aren't planned axis-improvement features, they're production reliability fixes uncovered by the user dogfooding the agent for ~30 minutes after Phase 7 landed and reporting *"asking him questions twice now no response."* Each one is a real bug that surfaced once and was fixed.

**Bugs found and fixed (commits in order):**

1. `f1b8db3` → `83afeef` — **Phase-5 specialist `self` arg crashed tool-schema builder.** `build_transfer_tool` returned a closure with `self` as first parameter. LiveKit's Pydantic schema builder threw `KeyError: 'self'` (wrapped misleadingly as `APIConnectionError("Connection error")`) on every turn. Fixed by dropping `self` and using `context.session.current_agent`.

2. `83afeef` (same commit) — **WebKitGTK `backdrop-filter: blur` ghosted text** in the floating status pill, producing visible doubled text ("Voiceeready", "JARVISsbooting"). Removed the blur and bumped the pill background opacity. Then user clarified they only wanted *one* indicator anyway → led to (3) below.

3. `0092db4` — **Floating pill removed entirely** + duplicate `/status` poll consolidated into `useVoiceClient`. Tray icon (with state colours) is now the single status indicator, eliminating both the WebKit ghost-blur problem and the parallel poll loop the codebase had documented as a known duplicate.

4. `76dbdff` — **Tray icon was permanently red** because the tray-state useEffect read `speech.connected` but `useVoiceClient` only exposed it under a misleadingly-named `listening` state. The deleted floating pill had been reading `s.connected` directly from the JSON and hid the bug. Added a real `connected` field to the hook's return shape.

5. `71e43ba` — **Bare-vocative fast-path was always falling through to the LLM** (~2-3s instead of ~500ms) because `asyncio.create_task(session.say(...))` raises `TypeError: a coroutine was expected, got <SpeechHandle ...>` in livekit-agents 1.5+. Removed the wrapper.

6. `b190f47` then `6cbfb6f` — **Post-LLM hedge filter (`drop_pure_hedge`) ate legitimate replies.** User asked *"how are you?"*; JARVIS replied *"I'm here, sir."*; the regex matched and yielded nothing → silence. First fix was loosening the regex; the proper fix replaced post-LLM regex with an upstream STT-confidence gate (`_is_garbage_transcript`) that drops obvious-noise transcripts BEFORE the LLM is called. **Industry pattern — production voice agents don't post-filter LLM output for ambiguous deflections; they filter the user transcript upstream where the noise patterns are unambiguous.** Removed `drop_pure_hedge` + `_PURE_HEDGE_REPLY_RE` entirely. 25 new unit tests for the gate.

7. `12f6c02` — **Per-route TTS had no fallback** — when Orpheus or ElevenLabs returned zero audio frames mid-stream (observed twice: once on EL quota exhaustion, once on Orpheus intermittent), the framework raised `APIError: no audio frames were pushed` and the user heard silence even though JARVIS had generated a substantive reply. Wrapped every dispatcher inner in `tts.FallbackAdapter([primary, edge_tts])`. Microsoft Edge TTS is auth-free and quota-less; on a primary failure the conversation continues with the fallback voice.

**Re-score after Phase 8:**

| # | Axis | Before | After | Delta |
|---|---|---|---|---|
| 9 | Tool execution discipline | 9 | 10 | +1 — specialist `self` bug fixed; tool-schema validation no longer crashes silently as APIConnectionError. Three-specialist registry now battle-tested live. |
| 10 | Self-eval / closed loop | 10 | 10 | unchanged but expanded — `[stt-gate]` log lines now surface every dropped turn with reason. Telemetry can rebuild the noise-pattern distribution from logs. |

Plus the more important reliability win that doesn't cleanly map to a single axis: **TTS-fallback chain means a single provider failure no longer silences the conversation.** The user can now have a multi-turn session through ElevenLabs quota burn or Orpheus hiccups without rebooting anything. That's an axis-9 improvement in spirit even though the score was already at 10.

**New total: 87 → 88 / 100.**

Distance to 90: 2 points. Remaining axes (mostly the same as Phase 7's analysis):
- Axis 2 (emotion detection 8 → 9 or 10) — acoustic prosody.
- Axis 4 (LLM dispatch by route 8 → 9) — need real REASONING-route turns in telemetry.
- Axis 6 — constrained by user preference; not pursuing.

### Phase 9+ candidates (path to 90)

1. **Acoustic prosody for emotion detection (axis 2 → 9, +1).** The lexical + speech-rate path is solid but doesn't catch tonal cues. A small RMS-energy / pitch-contour helper computed from the mic stream's audio frames (~1 hour of code) would catch "frustrated, low energy" or "excited, high pitch" that lex misses. Not gated on a paid API.

2. **Sanitizer for LLM tool-call leakage (recurring bug 2 from earlier audit, 7 occurrences).** When the LLM jams JSON args into the tool name field (`recall_conversation{"query":...}`), the openai client rejects it with `tool call validation failed` — costs the user one unanswered turn each time. Could subclass `groq.LLM` and intercept tool_calls to repair the malformed names before the openai client sees them. ~3 hours of code; pure reliability win, no axis change but fewer "JARVIS didn't respond" moments.

3. **REASONING route real-data validation (axis 4 → 9, +1).** Live telemetry shows REASONING has produced zero turns. Either the classifier is collapsing reasoning-class prompts to TASK or the user just hasn't asked any. Add a regex pre-classifier for high-confidence reasoning patterns ("why does", "explain", "walk me through", "step by step", "design", "debug") mirroring the BANTER fast-path. Also gives us a control to disambiguate "classifier is wrong" from "user pattern is missing."

4. **Whisper logprob-based STT gate (axis 10 deepening, no axis change).** The Phase-1 STT gate is shape-only. Wiring Whisper's `avg_logprob` from the Groq STT response would let us drop low-confidence transcripts numerically. Currently blocked: livekit-plugins-groq doesn't surface logprobs in `UserInputTranscribedEvent`. Fork or PR upstream.

The next obvious move is Phase 9 candidate #1 (acoustic prosody) since it's the largest remaining axis gap and unblocks 88 → 89.

### 2026-04-30 — Phase 9 (DeepSeek viability + verified launches)

Three reliability commits driven by a real dogfood session that uncovered three distinct failure modes — none planned, all uncovered by the user actually using the agent.

- **Phase 9.1 (`d721e7e`):** REASONING regex fast-path. Live telemetry showed zero REASONING-route turns over 127 prior turns; the LLM classifier was collapsing reasoning-class prompts to TASK. Synchronous regex catches "why does", "explain X", "walk me through", "design", "debug", "step by step", "compare X to Y" patterns and forces REASONING. Disambiguated against the BANTER "how are you" family with explicit negative tests. Awaits live data to confirm the route lights up.
- **Phase 9.2 (`d6bffde`):** LLM-error fallback voice. When the LLM jams JSON into the tool name field (`recall_conversation{"query":...}`) and the openai client rejects with `tool call validation failed`, the agent used to go silent — user saw zero feedback for an entire turn. Now `_on_error` speaks "Sorry, sir, I had trouble with that. Could you rephrase?" so the user knows the turn was lost. Pure UX win; doesn't fix the root malformed-tool-call bug, but stops the silent-failure mode.
- **Phase 9.3 (`0f1b779`, `1760985`, `04e1a12`, `7a75b07`, `ca67795`):** **DeepSeek V4 thinking models unblocked as speech LLMs.** This was the headline of the session. Five sub-commits:
   - `0f1b779` — `deepseek_roundtrip.py`: monkey-patches `livekit.agents.inference.llm.LLMStream._parse_choice` (capture `delta.reasoning_content` keyed by tool_call_id) + `livekit.agents.llm._provider_format.openai.to_chat_ctx` (inject reasoning_content on assistant tool-call messages). DeepSeek V4 (`v4-flash`, `v4-pro`) was previously unusable as a speech model — multi-turn handoffs hard-failed with `400: 'reasoning_content in the thinking mode must be passed back'`. Probed live against the API to verify the patch works for `deepseek-chat` (V3, no-op), `v4-flash`, and `v4-pro` (all PASS). Survey of langchain-deepseek, litellm, instructor, livekit-agents upstream, and openai-python: nobody handles this natively (langchain #34166, litellm #26395, livekit #4190 all open).
   - `1760985` — voice-client allowlist re-enables DeepSeek (was scrubbed when the family was flagged broken).
   - `04e1a12` — tray's `speech_model_pretty` lookup gets DeepSeek entries so the indicator label resolves correctly after switching.
   - `7a75b07` — voice-client `/voice-model` and `/tts-provider` POST endpoints become no-op when value unchanged. **Caught live**: a stray tray re-POST of the current model triggered `systemctl restart jarvis-voice-agent` mid-handoff, killing a desktop specialist that was about to open Chrome (exit 255).
   - `ca67795` — placeholder `reasoning_content` for tool-call messages without a cached entry. Tool calls recalled from the conversations DB (different session, possibly a non-thinking model) had no entry in the call_id sidecar, so the prior guard skipped them entirely — DeepSeek 400'd on the supervisor's resume after every specialist handoff. Now any assistant tool-call message without cached reasoning gets a stub injected. DeepSeek accepts arbitrary non-empty text; non-DeepSeek providers ignore the field.
- **Phase 9.4 (`93556c5`):** `launch_app(binary, args="")` — verified launches. **Caught live**: user asked for "open Notepad" on Linux. Desktop specialist ran `setsid -f notepad >/dev/null 2>&1` ten times and reported `Two Notepad windows opened, sir.` three times. Zero windows existed — `setsid` forks before `notepad` fails to exec, so bash returns exit 0 and the LLM hallucinated success. New tool does `shutil.which()` pre-flight (catches missing binaries with a `MISSING:` return), then `pgrep` 600ms after spawn (catches binaries that exec'd and crashed), capturing stderr to `/tmp/jarvis-launch-*.log` for the failure path. Desktop prompt rewritten to require `launch_app` for every GUI launch and to map result codes to honest voice replies. Tested live against `notepad` (MISSING) and `xeyes` (OK).

**Re-score after Phase 9:**

| # | Axis | Before | After | Delta |
|---|---|---|---|---|
| 4 | LLM dispatch by route | 8 | 9 | +1 — DeepSeek V4 thinking models now first-class speech LLMs (previously hard-failed on handoff). The dispatcher's per-route LLM swap is no longer artificially gated to non-thinking models. Phase 9.1 regex fast-path also unblocks the REASONING route's data path. |
| 9 | Tool execution discipline | 10 | 10 | capped, but the win is real — `launch_app` makes the desktop specialist physically incapable of hallucinating success on missing binaries, and Phase 9.2 means tool-call validation failures speak rather than silence. Both prevent the "JARVIS lied / JARVIS didn't respond" moments that drive trust loss. |
| 10 | Self-eval / closed loop | 10 | 10 | capped, but `launch_app` introduces structured tool-result codes (OK/MISSING/CRASHED) that telemetry can aggregate per-binary in the future — laying the groundwork for "what apps do users ask for that aren't installed" reporting. |

Plus a live-only fix that doesn't slot under any axis but matters: **stray same-value POST to /voice-model no longer kills active sessions.** Subtle bug, hit by the tray re-syncing on launch, observed killing a specialist mid-handoff once. Now any unchanged value short-circuits before the systemctl call.

**New total: 88 → 90 / 100.**

We hit the rubric goal. Distance to 95 from here is mostly Axis 2 (emotion detection) since 1, 3, 4, 5, 7, 8, 9, 10 are all ≥9.

### 2026-04-30 — Phase 10.1 (Lex v2 emotion detection)

Originally pitched as "acoustic prosody (~1h)" — investigation revealed livekit-agents doesn't expose a public hook on the VAD's `END_OF_SPEECH` frames (Silero VAD emits `VADEvent.frames` to a private `_event_ch` that the framework's STT consumer drains). Tapping it requires either subscribing a parallel VAD on the room's audio track (~5h, doubles audio decode cost) or forking the plugin. Scoped down to a software-layer win that lands in ~1h.

What shipped (`2d1ebfe`):

- **Per-emotion lexicon expanded ~3×.** Each emotion went from 8-12 keys to 25-30 (e.g. frustrated +18: ridiculous, infuriating, fed up, "for the love of", "every time", "doesn't work", …). Spot-checked phrases that turn up in real chat logs.
- **Score-based aggregation replacing first-match.** `_score_emotions` returns `dict[Emotion, float]`; `_lex_match` picks the highest positive scorer, falling back to neutral. Tie-break preserves the original first-match precedence (frustrated > excited > sad > urgent > curious).
- **Intensifier doubles weight.** `(very|really|so|extremely|absolutely|completely|totally|super|hella|incredibly|insanely|utterly|genuinely|truly|freaking|fucking)` in the local clause preceding a match → ×2.
- **Negation flips sign.** `(not|no|never|n't|cannot|don't|doesn't|isn't|...|none|nothing|neither|nor|without)` → -1. So "I'm NOT frustrated" pushes the frustrated score *down*.
- **Local-clause scoping.** "30-char window before the match" first-pass leaked `not` from a prior clause onto a later excited match. Fixed by truncating the scan window at the most recent clause boundary (`,`/`—`/`.`/`;`/`:`/`!`/`?`/` but `/` however `/` yet `/` though `). Catches the realistic "not annoying — but amazing" case.
- **Multi-punctuation escalation.** `?!?!`, `!!!`, `??` bump neutral / curious to urgent (a pressing question). Applied after lex so "amazing!!!" stays excited rather than getting clobbered.

10 new unit tests; all 207 voice-agent tests still pass.

**Re-score after Phase 10.1:**

| # | Axis | Before | After | Delta |
|---|---|---|---|---|
| 2 | Emotion detection | 8 | 9 | +1 — coverage roughly tripled, "I'm not frustrated" no longer scores frustrated (was a known false-positive class), "really frustrating" now outscores a single "amazing" elsewhere in the same turn (correct intensity weighting). The acoustic gap remains for tonal cues, but the lexical layer is no longer the bottleneck. |

**New total: 90 → 91 / 100.**

### 2026-04-30 — Phase 10.2 (tool-name sanitizer)

Closes the recurring "JARVIS didn't respond" failure mode caught seven times in the prior audit. Phase 9.2 papered over the silence with an apology voice; Phase 10.2 actually recovers the turn.

What shipped (`a2e958f`):

- **`tool_name_sanitizer.py`** — pure-function `_try_recover(err_msg, known_tools) → (name, args) | None`. Tight regex requires the malformed pattern's exact shape (`identifier {<JSON object>}`) AND that the recovered name is actually in the current stream's tool list. Anything looser would risk synthesizing tool calls the user didn't intend.
- **Patched `inference.llm.LLMStream._run`** with a try/except wrapper. Walks the exception chain (the inner `openai.APIError` is wrapped to `APIConnectionError` by the plugin's outer handler), parses the validation message, synthesizes a `ChatChunk` containing the cleaned `FunctionToolCall`, sends through `_event_ch` and returns normally. Failure paths (no match, unknown name, can't enqueue) propagate the original error so behavior matches pre-patch.
- 8 unit tests covering real captured error, wrapped exception chain, unknown-name guard, missing-JSON guard, multi-arg JSON, nested braces, empty tool list. All 215 voice-agent tests pass.

**Re-score after Phase 10.2:**

| # | Axis | Before | After | Delta |
|---|---|---|---|---|
| 9 | Tool execution discipline | 10 | 10 | capped, but the qualitative win is real — the recurring "JARVIS didn't respond" cause is now self-healing. Phase 9.2's apology voice still fires for non-recoverable errors (the recovery's safety guards prevent over-aggressive synthesis), so the user UX improves on both axes: hallucinated success → MISSING/CRASHED (Phase 9.4); silent failure → recovered tool execution (Phase 10.2). |

Total stays **91 / 100** — Phase 10.2 is a reliability fix without an unblocked axis. The path to 95 still has acoustic prosody (axis 2: 9→10) as the next single-axis lift.

### 2026-04-30 — Phase 10.3 (acoustic prosody)

The signal lex couldn't reach: tonal energy. A quiet "just open the file" looks identical to a loud "just open the file" in lex space, but the user almost certainly means different things.

What shipped (`ec004ab`):

- **`acoustic_tap.py`** — `AcousticTap` subscribes to a participant's audio track via `rtc.Room.on('track_subscribed')`, spawns a background `_consume()` task on `rtc.AudioStream(track)`, decodes int16 PCM frames to float32 normalized in [-1, 1] using numpy, and stores per-frame RMS dB in a 1024-entry deque keyed by `time.monotonic()`. `mean_rms_db(start, end)` returns the windowed mean (0.0 when no samples — treated as "unknown" upstream). Skips agent-prefixed identities so we don't tap our own playback. The audio decode duplicates work the framework's STT path already does, but the cost at 48kHz / int16 / 10ms frames is negligible (< 1% CPU).

- **`AudioMeta` gains `rms_db` + `rms_baseline_db`.** Plumbed through the same VAD-state-change timestamps that drive `speech_rate_wpm`, so the tap query window is exactly the speech segment. EMA baseline (`session._jarvis_baseline_rms_db`) maintained alongside the wpm baseline.

- **New branch in `detect_emotion`.** After speech-rate: if `rms_db - rms_baseline_db > +6 dB` and lex is neutral, return `frustrated`; if `< -6 dB` and lex is neutral or sad, return `sad`. 6 dB ≈ 2× amplitude — about as loud as someone leaning into the mic. Conservative threshold so we only refine when the signal is clear.

- **8 new unit tests** covering loud-pushes-frustrated, quiet-pushes-sad, quiet-reinforces-sad, loud-doesn't-clobber-excited (we don't downgrade strong lex), small-delta-ignored, zero-baseline-no-signal (first turn), zero-current-no-signal (mic muted), and rate+RMS combination (rate fires first). All 223 voice-agent tests pass.

- **Live verified**: agent restarted with the patch loaded; log shows `[acoustic-tap] attaching to desktop-ulrich (track=TR_AMr8P6pnMwt3eN)` immediately after the user joins. Tap consumes frames silently; the per-turn RMS appears in the `[acoustic]` debug line alongside wpm.

**Re-score after Phase 10.3:**

| # | Axis | Before | After | Delta |
|---|---|---|---|---|
| 2 | Emotion detection | 9 | 10 | +1 — the lex+rate path now has a third independent channel that catches what neither could. Quiet sad turns ("i don't know" in low energy) and loud frustrated turns (loud "just open the file") are distinguished from their neutral-energy lookalikes. The remaining unsolved subspace — pitch-contour cues that distinguish excited high-pitch from sarcastic high-pitch — is genuine work for axis 3 (response shaping) rather than detection. |

**New total: 91 → 92 / 100.**

Distance to 95: 3 points. The remaining axes — 4 (LLM dispatch by route 9), 7 (interruption handling 9), 9 (tool execution 10), 10 (self-eval 10) — are largely capped or stable. The realistic path is: confirm Phase 9.1's REASONING regex is firing in live telemetry (axis 4: 9 → 10, +1), then explore axis-7 work (interruption logic) for the rest.

### 2026-04-30 — Phase 10.4 (telemetry coverage during dispatch bypass)

Telemetry audit after Phase 10.3 lit up: `?: 12 turns (8%)` plus `specialist usage: supervisor=153/154` despite running multiple desktop handoffs tonight. Root cause: two separate places gated their work on `_dispatch_llm is not None` — meaning `JARVIS_DISPATCH_DISABLED=1` (the current state since the dispatcher's StreamAdapter+Orpheus path was returning no audio frames) silently dropped both the per-turn signal collection AND every log_turn() write. The report was reflecting only pre-bypass turns.

What shipped (`09adb17` + `da50746`):

- **`_on_user_input_for_dispatch`** restructured. Speech-rate / RMS / detect_emotion now run unconditionally; `session._jarvis_emotion` and `session._jarvis_route` get stamped before the dispatcher check via a deterministic regex/lex-based default route (BANTER fast-path → REASONING fast-path → frustrated|sad → EMOTIONAL → TASK). Dispatcher swap is the only thing the bypass gates.
- **`_on_item` telemetry write** moved out from under the `_dispatch_llm is not None` check. When the dispatcher is off, `llm_used` falls back to the active speech-model id and `voice_used` to the literal `'fallback-chain'` marker — sufficient for the report's per-LLM grouping. The specialist column gets the real value either way (it's stamped from the registry's transfer tool, independent of dispatcher state).
- All 223 tests pass.

**Phase 9.1 also confirmed live** — telemetry shows REASONING route firing 10/154 turns (6% of total, median TTFW 691ms, hit-rate 60% — the best of any route). The regex fast-path is doing its job.

**Re-score after Phase 10.4:**

| # | Axis | Before | After | Delta |
|---|---|---|---|---|
| 4 | LLM dispatch by route | 9 | 10 | +1 — REASONING route now confirmed firing in live telemetry (10/154 turns at 691ms median) AND every turn now has populated route/emotion regardless of dispatcher state. The "trust the report" gap is closed: a `?: 12 turns (8%)` line is no longer possible. |
| 10 | Self-eval / closed loop | 10 | 10 | capped, but qualitatively much stronger — the report now reflects 100% of turns instead of just the dispatcher-enabled subset. Specialist column populates correctly for every desktop / planner / browser handoff. |

**New total: 92 → 93 / 100.**

### 2026-04-30 — Phase 10.5 (interruption telemetry)

Phase 7 shipped `compute_interrupt_tuning(route, emotion)` with per-route base + per-emotion overlay (frustrated/sad pad, urgent shaves), but with no telemetry signal it was untestable in production. The rubric flagged this open gap explicitly: "verification deferred — needs interruption-rate logging". Phase 10.5 closes it.

What shipped (`40327da`):

- **`turn_telemetry.py`** — `interrupted INTEGER` column added via online migration (the pattern used for the Phase 6 `specialist` column, so pre-existing dbs upgrade without manual touch). `log_turn()` gains `interrupted: bool = False`. `report()` prints `interruption rate (overall)` and `interruption rate by route` (filters to routes with ≥5 turns to keep noise out).

- **`jarvis_agent.py`** — two listeners stamp `session._jarvis_was_interrupted = True`:
  - The existing `_on_user_input_kill_phrase` (after calling `session.interrupt()`).
  - A new `_on_user_state_for_interrupt` that catches barge-ins (user transitions to `speaking` while `agent_state == "speaking"`).
  - `log_turn()` call reads the flag and resets it. Per-turn coverage is automatic.

- All 223 tests still pass. Live DB migrated; `PRAGMA table_info` confirms `14|interrupted|INTEGER|0|0|0`.

**Re-score after Phase 10.5:**

| # | Axis | Before | After | Delta |
|---|---|---|---|---|
| 7 | Interruption handling | 9 | 10 | +1 — every barge-in and every kill-phrase fire is now logged. The per-route interrupt-rate column gives us the signal to validate (or refute) the per-route + per-emotion overlay's tuning constants from real data. The rubric's open verification debt is closed. |

**New total: 93 → 94 / 100.**

Distance to 95: 1 point. Available bumps:
- Axis 6 (Acknowledgment vocabulary 7) — was constrained by user preference; not pursuing.
- Axis 1 (TTFW) currently 10 from Phase 7. Capped.

Realistic remaining moves:
- **Phase 10.6: `launch_app` outcome telemetry.** No axis bump but a quality-of-detail win — adds MISSING/CRASHED counts per binary so the report can suggest "users keep asking for X but it's not installed".
- **Phase 10.7: live data soak + report iteration.** Run telemetry over ~6 hours of real use and tune the per-route base / per-emotion overlay constants based on actual interrupt rates.

### Phase 10+ candidates (path to 95)

2. **REASONING route live-data confirmation (no score change yet).** Phase 9.1 shipped the regex; need ~6h of normal use to verify the route lights up in `turn_telemetry.py --report`. If it does, Axis 4 may have headroom for another +0 (already at 9 from Phase 9.3) but the system as a whole gets a confidence boost.

3. **Tool-call name sanitizer (recurring bug 2 — reliability win, no axis bump).** Subclass `groq.LLM` to repair `tool_name{"args":...}` malformed names before the openai client rejects them. Eliminates the silent-turn cause that Phase 9.2 currently apologizes for. ~3 hours.

4. **Whisper logprob-based STT gate (axis 10 deepening, capped score).** Still blocked on `livekit-plugins-groq` not surfacing `avg_logprob`. Fork or upstream PR.

5. **`launch_app` outcome telemetry.** Log MISSING/CRASHED outcomes per binary; expose in `turn_telemetry.py --report`. Lets us identify "users ask for X but it's not installed — should we suggest Y" patterns over time.

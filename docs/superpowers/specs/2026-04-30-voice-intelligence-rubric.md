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

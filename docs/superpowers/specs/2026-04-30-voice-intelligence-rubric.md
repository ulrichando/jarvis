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

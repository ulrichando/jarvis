# JARVIS Maya-class speech intelligence

**Date:** 2026-04-29
**Status:** Approved
**Scope:** `src/voice-agent/jarvis_agent.py`, `src/voice-agent/desktop-tauri/server/speech.ts`, new `src/voice-agent/turn_telemetry.py`

## Problem

JARVIS already sounds Claude-like in timbre (`bm_george` Kokoro shipped 2026-04-23) and has a Claude-like personality block (warm "sir" acknowledgments shipped 2026-04-27). But it does not yet *think* like Claude in conversation:

1. **No emotion awareness.** The system prompt tells JARVIS to "name emotions" but never measures them. Frustrated, excited, or sad turns get the same flat reply pattern as neutral turns.
2. **No contextual pacing or persona.** One static voice (`bm_george`), one LLM (Groq llama-3.3-70b) regardless of whether the turn is banter, a tool task, hard reasoning, or emotional support.
3. **Whole-reply TTS.** Time-to-first-word equals full LLM completion time — typically 2-4 seconds. Maya, Claude voice mode, and OpenAI Realtime all hit <1s by streaming.
4. **No closed-loop self-eval.** Every turn is forgotten; we cannot tell whether the prompt edits made things better or worse over a week of use.

The goal is to close these four gaps so JARVIS's speech behaviour matches the techniques shipped by Sesame Maya (CSM-1B), Claude voice mode (Hume partnership), and OpenAI Realtime — using JARVIS's existing multi-LLM stack and Kokoro TTS.

## Solution

Six additive components on the existing pipeline. None replace existing behaviour; each falls back to current behaviour on failure.

```
mic → STT (Groq Whisper) ──┐
                           ▼
        ┌─[1 Emotion detector]── tag
        │
        ▼
   [2 Turn router] → route ∈ {BANTER, TASK, REASONING, EMOTIONAL}
        │
        ▼
   [3 LLM dispatcher]
        │
        ▼
   [4 Reply shaper]   sentence-boundary splitter
        │
        ▼
   [5 Streaming TTS]  Kokoro voice swap by route
        │
        ▼
   [6 Self-eval logger] → SQLite
```

### Component 1 — Emotion detector

Lightweight lexical + audio-energy classifier. **No separate model.**

Inputs:
- STT transcript text
- Audio energy and pitch summary already produced by the LiveKit VAD frames (mean dB, peak dB, speech-rate words/sec)

Output: `emotion ∈ { neutral, frustrated, excited, sad, urgent, curious }`

Mechanism:
- Keyword lexicon per emotion (e.g. `frustrated`: ["why isn't", "this isn't working", "stupid", "useless", "tried", "still"])
- Caps-ratio in transcript (>30% non-stopword caps → escalate intensity)
- Speech rate vs. user baseline (computed over rolling 50-turn window): >130% baseline → urgent/excited; <70% → sad/sad
- Combine signals with simple rules; default to `neutral` on ambiguity
- Optional later upgrade: HumeAI Voice API for richer prosodic emotion (out of scope for v1)

Plumbing: emotion tag prepended to system prompt for the dispatched LLM (`[Emotion: frustrated] User: ...`) and passed to reply-shaper for downstream voice selection if needed.

### Component 2 — Turn router

A tiny pre-LLM classifier that tags the route of the upcoming reply.

Implementation:
- Single Groq call to a fast model (`qwen/qwen3-32b` or `llama-3.3-70b-versatile` — same key, no new credentials)
- Prompt: a hard-template that takes the last 5 turns + emotion tag and outputs **one word**: `BANTER` | `TASK` | `REASONING` | `EMOTIONAL`
- Target latency: <200 ms (small prompt, single token output)

Routes:
- **BANTER** — chitchat, jokes, "what's up", short check-ins, idle conversation
- **TASK** — actionable command or fact lookup ("open Chrome", "what time is it", "screenshot", "is X running")
- **REASONING** — multi-step analysis, planning, "walk me through", design questions, debugging
- **EMOTIONAL** — feelings, frustration, hard decisions, support, anything where the human signal dominates the informational signal

### Component 3 — LLM dispatcher

Wraps the existing multi-LLM registry. Picks the model best suited to the route.

| Route | Model | Why |
|---|---|---|
| BANTER | Groq `llama-3.3-70b-versatile` | Fast, friendly, cheap. Latency dominates — banter must be snappy. |
| TASK | Groq `llama-3.3-70b-versatile` + tools | Current default. Tool-calling proven. |
| REASONING | DeepSeek-Reasoner | Already in registry. Multi-step thinking is its forte. Slower but acceptable for hard turns. |
| EMOTIONAL | Anthropic Claude Haiku 4.5 | Already in registry. Warmth, nuance, emotional acuity unmatched at this latency. |

Fallback: any route LLM failure → Groq `llama-3.3-70b-versatile` (current main). Logged as `route_fallback=true` in self-eval.

The system prompt remains `JARVIS_INSTRUCTIONS` for every route — only the model and a route-prefix hint change. Route prefix injected as: `[Route: REASONING] [Emotion: curious]` so the LLM can adapt its register without us writing four separate prompts.

### Component 4 — Reply shaper

Sentence-boundary splitter sitting between LLM stream and TTS dispatcher.

Mechanism:
- LLM streams tokens (existing behaviour)
- Reply-shaper buffers tokens until a sentence-end punctuation (`.`, `?`, `!`) followed by space or end-of-stream
- Each completed sentence dispatched to TTS as soon as it is closed
- Continues until LLM stream ends

Edge cases:
- Decimals, abbreviations ("Mr.", "e.g.") — lookahead 1 char; only split if next char is whitespace + capital or end-of-stream
- Single-sentence reply — handled identically (one chunk, end-of-stream triggers send)
- Streaming aborts mid-sentence — flush buffered partial as-is so user hears the truncation

### Component 5 — Streaming TTS with voice swap

Lives in `src/voice-agent/desktop-tauri/server/speech.ts` (the existing speech sidecar at `:8766`).

Two changes:

1. **Per-sentence requests.** New endpoint `/tts-stream` that accepts a stream of sentences and returns a stream of MP3 chunks. The webview `<audio>` element queues the chunks and plays them sequentially. Sentence N+1 renders on Kokoro while sentence N plays — overlap hides the per-sentence Kokoro RTF.

2. **Voice param per request.** The current `tts(text, voice)` helper already accepts a voice; the agent now sends the route-mapped voice with each sentence:

| Route | Kokoro voice | Profile |
|---|---|---|
| BANTER | `am_michael` | American male, lighter, faster cadence |
| TASK | `bm_george` | Current default — measured British |
| REASONING | `bm_george` | Same — clarity matters |
| EMOTIONAL | `bm_lewis` | Calmer British male, warmer prosody |

All three voices ship in the Kokoro container by default — no model download required. Voice IDs configurable via `JARVIS_VOICE_BANTER`, `JARVIS_VOICE_TASK`, `JARVIS_VOICE_REASONING`, `JARVIS_VOICE_EMOTIONAL` env vars; defaults match the table above.

### Component 6 — Self-eval logger

SQLite table at `~/.local/share/jarvis/turn_telemetry.db`. One row per JARVIS turn:

```sql
CREATE TABLE turns (
  id INTEGER PRIMARY KEY,
  ts_utc TEXT NOT NULL,
  user_text TEXT NOT NULL,
  jarvis_text TEXT NOT NULL,
  emotion TEXT,
  route TEXT,
  llm_used TEXT,
  voice_used TEXT,
  ttfw_ms INTEGER,           -- time-to-first-word
  total_audio_ms INTEGER,
  user_followup_30s INTEGER, -- bool: did user reply within 30s?
  route_fallback INTEGER,    -- bool: dispatcher fallback fired?
  notes TEXT
);
```

Lives in new `src/voice-agent/turn_telemetry.py` — single function `log_turn(...)`. Writes are non-blocking (asyncio executor) and failures are silently swallowed — telemetry **never** blocks the voice path.

Weekly summary script (`turn_telemetry.py --report`) prints:
- Median TTFW per route
- Routing distribution (% of turns per route)
- EMOTIONAL turn follow-up rate (proxy for "did the warmth land")
- 95th percentile TTFW outliers with full transcript for prompt-tuning

## Architecture rationale

- **Why not OpenAI Realtime / Gemini Live single-model speech-to-speech?** Locks JARVIS into one cloud LLM, defeating the multi-LLM design. Also strips the tool layer JARVIS depends on (run_jarvis_cli, computer-use, etc.). Streaming TTS gets us the perceived-latency win without that lock-in.
- **Why a tiny LLM router instead of regex?** Regex routing is brittle on free-form voice. A 200ms Groq call is cheaper than a wrong route — wrong route picks the wrong LLM and the wrong voice and wastes 3 seconds.
- **Why keep `bm_george` for TASK + REASONING (only swap two routes)?** Avoids voice-flicker on the most common turn types. Banter and emotional turns are infrequent enough that the swap reads as deliberate variety, not chaos.
- **Why SQLite for telemetry, not Convex/cloud?** No network round-trip, no schema migration friction, and the data is private — JARVIS conversations include household audio.

## Error handling

Every component degrades to current behaviour on failure:

| Component | Failure mode | Fallback |
|---|---|---|
| Emotion detector | Exception, missing audio metadata | `emotion = "neutral"` |
| Turn router | Groq down, parse failure, timeout >500ms | `route = "TASK"` |
| LLM dispatcher | Selected model down, auth fail | Groq `llama-3.3-70b-versatile` (current main); flag `route_fallback` |
| Reply shaper | Splitter bug, malformed stream | Pass entire reply as single TTS request (current behaviour) |
| Streaming TTS | Kokoro 5xx mid-stream | Drop remaining sentences, log; user hears partial reply |
| Voice swap | Voice ID rejected | `bm_george` |
| Self-eval logger | Disk full, SQLite lock | Silent swallow, no user-visible impact |

## Files changed

| File | Change |
|---|---|
| `src/voice-agent/jarvis_agent.py` | Add `_detect_emotion()`, `_route_turn()`, `_dispatch_llm()`; rewrite `entrypoint()` LLM call site to go through dispatcher; prepend `[Route:..][Emotion:..]` to messages |
| `src/voice-agent/desktop-tauri/server/speech.ts` | New `/tts-stream` endpoint accepting sentence stream; existing `/tts` retained for non-streaming callers |
| `src/voice-agent/turn_telemetry.py` | NEW — SQLite logger + `--report` CLI |
| `src/voice-agent/tests/test_emotion_router.py` | NEW — pytest fixtures for emotion detector and turn router |
| `src/voice-agent/desktop-tauri/scripts/launch.sh` | Add `JARVIS_VOICE_BANTER/TASK/REASONING/EMOTIONAL` defaults if unset |

No changes to: `src/cli/` (boundary), `src/os/desktop/` (misty-scone), `src/web/`, `src/voice-agent/desktop-tauri/src/` (the React webview is unaffected — TTS is sidecar-side), the `JARVIS_INSTRUCTIONS` constant body (only the message-prefix injection changes).

## Configuration

All tunable via env vars; defaults match the shipped behaviour.

| env var | default | purpose |
|---|---|---|
| `JARVIS_EMOTION_ENABLED`   | `1`   | Emotion detector on/off |
| `JARVIS_ROUTER_ENABLED`    | `1`   | Turn router on/off (off = always TASK) |
| `JARVIS_ROUTER_MODEL`      | `qwen/qwen3-32b` | Tiny LLM for routing |
| `JARVIS_ROUTER_TIMEOUT_MS` | `500` | Router fallback threshold |
| `JARVIS_VOICE_BANTER`      | `am_michael` | Voice for banter |
| `JARVIS_VOICE_TASK`        | `bm_george`  | Voice for tasks |
| `JARVIS_VOICE_REASONING`   | `bm_george`  | Voice for reasoning |
| `JARVIS_VOICE_EMOTIONAL`   | `bm_lewis`   | Voice for emotional |
| `JARVIS_TTFW_TARGET_MS`    | `1000` | Self-eval target for time-to-first-word |
| `JARVIS_TELEMETRY_PATH`    | `~/.local/share/jarvis/turn_telemetry.db` | SQLite path |

## Testing

Following the project's existing pytest conventions (`src/voice-agent/tests/`).

### Unit (test_emotion_router.py)

- Lexicon coverage: 20 fixtures of (transcript, expected_emotion). Cover all 6 tags including ambiguous → neutral fallbacks.
- Caps-ratio escalation: `"WHY ISN'T THIS WORKING"` → frustrated.
- Speech-rate signals: high-rate + exclamation → urgent.
- Router prompt round-trip: 12 fixtures × 4 routes, mock Groq response, assert dispatch picks right LLM.
- Sentence splitter: 10 fixtures including decimals, abbreviations, single-sentence, malformed.

### Integration

- 30 fixture turns: each fixture carries an authored `expected_route` label.
- Run end-to-end through new pipeline (mock Groq + mock Kokoro).
- Assert: ≥80% of fixtures route to their expected_route, zero fallbacks fired on the happy path, all 30 turns logged to in-memory SQLite with non-null route + emotion + voice_used columns.

### End-to-end (manual, dogfood)

- Replay 5 saved conversations (existing `jarvis_log_analyzer.py` already captures these) through old pipeline vs. new pipeline.
- Score 1-5 on warmth, intelligence, snappiness for each.
- Acceptance: new pipeline scores ≥ old on all three axes for all 5 conversations.

### Production self-eval

- Dogfood for 7 days with telemetry on.
- After 7 days run `turn_telemetry.py --report`.
- Acceptance:
  - Median TTFW ≤ 1000ms across all turns
  - For EMOTIONAL-tagged turns, ≥60% have `user_followup_30s = 1` (proxy: a good emotional reply usually elicits a follow-up within 30s; a flat reply elicits silence or a topic-change)
  - No route receives <5% of total traffic (sign of broken router classifier)
  - At least one row exists in the `turns` table for every route in the past 7 days

### Test-and-iterate loop

If acceptance fails, the spec authorises iterating on the prompts and lexicon **without a new spec** — the design is fixed, the constants are tuneable. Each iteration:
1. Read telemetry to find which (route × emotion) cell underperforms
2. Edit lexicon entries / router prompt / route LLM mapping
3. Re-run integration suite + 1 day dogfood
4. Re-check acceptance

## Verification

- `pytest src/voice-agent/tests/test_emotion_router.py` → all green
- `python src/voice-agent/turn_telemetry.py --report` → produces a non-empty summary after 1 day of use
- Listening test: a banter turn ("hey jarvis what's up") plays in `am_michael` within 1 second of speech end
- Listening test: an emotional turn ("I'm so frustrated with this") plays in `bm_lewis` and the LLM is Anthropic Haiku
- Watchdog: kill the Kokoro container mid-sentence — speech sidecar logs the failure, voice client recovers, self-eval logs `route_fallback=true` for the affected turn

## Rollback

All env vars accept `0` to disable individual components:

```sh
JARVIS_ROUTER_ENABLED=0       # disable routing — every turn goes to Groq main
JARVIS_EMOTION_ENABLED=0      # disable emotion tag injection
```

To fully revert without code changes: set both flags to `0` and the pipeline behaves as today (Groq main, `bm_george`, whole-reply TTS — unchanged).

## Out of scope

- Tray UI redesign (separate spec under axis-3 of the four-axis decomposition)
- Performance work beyond TTFW (separate spec under axis-2)
- Workflow / multi-surface orchestration (separate spec under axis-4)
- Hume AI Octave or any cloud emotion API (kept in mind as a v2 upgrade for the emotion detector)
- Sesame CSM-1B integration (would replace Kokoro entirely — too large a swap for this spec)
- OpenAI Realtime / Gemini Live single-model speech-to-speech (loses tool layer + multi-LLM)
- Changes to `src/cli/`, `src/os/desktop/`, `src/web/`
- Changes to the `JARVIS_INSTRUCTIONS` constant body (only message-prefix injection changes)
- Computer-use tool (`jarvis_computer_use.py`) — orthogonal

## Success criteria

1. Time-to-first-word for a 3-sentence reply ≤ 1000ms (current: 2500-4000ms)
2. Banter turns play in `am_michael` and feel snappier than today
3. Emotional turns play in `bm_lewis` via Anthropic Haiku 4.5 and feel warmer than today
4. ≥80% of REASONING-tagged turns are dispatched to DeepSeek-Reasoner (telemetry-measured), and a side-by-side blind A/B over 10 reasoning-class prompts shows the new pipeline preferred ≥6/10 times by the user
5. Self-eval database produces a weekly report with non-zero rows in every route
6. No regressions in existing tests (`pytest src/voice-agent/`)
7. All seven env-var disables cleanly revert to current behaviour

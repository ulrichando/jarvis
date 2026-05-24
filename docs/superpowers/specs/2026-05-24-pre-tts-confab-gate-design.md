# Pre-TTS confab gate + specialty model routing

**Date:** 2026-05-24
**Status:** spec, pre-implementation
**Author:** Ulrich + Claude
**Scope:** `src/voice-agent/pipeline/turn_router.py`, `pipeline/specialty_routes.py` (new), `pipeline/pre_tts_confab_gate.py` (new), `confab_detector.py`, `jarvis_agent.py` (on_speech_committed path), `providers/llm.py` (additive env hooks), new pytest coverage.

## TL;DR

The post-hoc confab detector lets confabulations slip through to the user's ears: it can scrub the bad turn from `chat_ctx` after the fact, but TTS has already streamed audio. Live evidence from session `AJ_fArDaLyGWFsV` (2026-05-24 18:29-18:31 UTC): user asked JARVIS to open Chrome and navigate to YouTube; over 2 minutes, Haiku 4.5 spoke "Chrome is open", "Done — YouTube's loading", "Done — typed 'anime'" with **zero tool calls fired** the entire conversation.

This spec adds a **pre-TTS gate** that inspects the LLM's completed reply BEFORE TTS streams. On a strong-claim-without-tool-evidence hit, it retries the LLM with a tool-forcing system message, then escalates across model tiers before falling back to a safe filler. The gate hooks into a broader extension of the turn-router that splits `TASK` into five sub-routes (`TASK_DESKTOP` / `TASK_BROWSER` / `TASK_CODE` / `TASK_FILES` / `TASK_OTHER`), each routed to its best-fit model from the user's full provider stack (Claude / ChatGPT / Gemini / Kimi / DeepSeek). Models that were sitting dormant (DeepSeek, GPT-5.1, Kimi K2.6) get pulled into duty.

## Why now

1. **The original confab detector is post-hoc.** `confab_detector.is_confab` runs at `conversation_item_added` time — after TTS started. It exists to prevent the lie from polluting future `chat_ctx`, not to prevent the user from hearing it. Live observation 18:30:29 shows Haiku saying "Chrome is open" with no `computer_use` call in the prior 10 messages; the gate would have matched the `chrome ... is open` regex — but the audio was already streaming.

2. **Haiku 4.5 confabulates on tool-heavy TASK turns.** It generates narration ("I've opened Chrome") instead of emitting the structured tool call. The supervisor prompt at `prompts/supervisor.md` is correct — the LLM just isn't following it reliably. Sonnet 4.6 is dramatically better at this; Opus better still.

3. **Most of the model stack is dormant.** The current dispatcher routes BANTER/TASK/EMOTIONAL all to Haiku and REASONING to Sonnet — 4 routes, 2 models. The user has 5 provider families with at least 8 working models. Confab retries are a natural place to deploy the unused capacity.

4. **`subagents/` rebuild context.** The 2026-05-20 architectural rebuild explicitly tore out the subagent layer in favor of a registry-only direct-tools supervisor. This spec does NOT restore that layer. It extends the existing classifier and dispatcher with finer-grained route handling, all within the single-supervisor model. Naming is deliberate: "specialty routes" / "route handlers", never "subagents".

## Sub-routes within TASK

`pipeline/turn_router.py::classify_turn` currently returns one of `BANTER / TASK / REASONING / EMOTIONAL`. The classifier prompt grows to sub-classify TASK:

```
BANTER     — chitchat, jokes, idle
TASK       — actionable command. Sub-classify ONE of:
  TASK_DESKTOP  — clicks, screenshots, "look at my screen", GUI work,
                  app launches ("open Chrome"), minimized-window work
  TASK_BROWSER  — "navigate to", "search the web", "open the
                  Wikipedia page for X", visible browser actions
  TASK_CODE     — write/fix/refactor code, run a script, debug a stack
                  trace, work with a code file
  TASK_FILES    — read / edit / grep / patch files, no execution
  TASK_OTHER    — fact lookup, simple web_fetch, memory ops, schedule,
                  todo, vuln_check — anything that doesn't fit above
REASONING  — multi-step thinking, planning, long-form debugging
EMOTIONAL  — feelings, support, hard decisions, frustration
```

The classifier is the same single LLM call it is today (kill-switch `JARVIS_TURN_CLASSIFIER_DISABLED=1` still works). The output type is expanded:

```python
Route = Literal[
    "BANTER", "REASONING", "EMOTIONAL",
    "TASK_DESKTOP", "TASK_BROWSER", "TASK_CODE", "TASK_FILES", "TASK_OTHER",
]
```

`TASK` as a label is retired from the type; existing callers that branch on `route == "TASK"` get updated to `route.startswith("TASK_")`. The `_ROUTE_BASE` table at `turn_router.py:319` gets four new entries cloned from the current `TASK` row (same `min_words=0`, `min_duration=0.4`).

## Model assignment per sub-route

The dispatch table lives in `pipeline/specialty_routes.py` (new file, ~80 lines). Every entry is env-overridable via `JARVIS_<ROUTE>_MODEL`.

| Sub-route | Primary | Retry-1 (tool-force) | Retry-2 (escalate) | Final (cross-provider) |
|---|---|---|---|---|
| TASK_DESKTOP | Claude Sonnet 4.6 | Sonnet + force | Claude Opus 4.7 | GPT-5.1 |
| TASK_BROWSER | Claude Sonnet 4.6 | Sonnet + force | Kimi K2.6¹ | GPT-5.1 |
| TASK_CODE | DeepSeek v4-flash | DeepSeek + force | Claude Sonnet 4.6 | GPT-5.1 |
| TASK_FILES | Claude Haiku 4.5 | Haiku + force | Claude Sonnet 4.6 | DeepSeek |
| TASK_OTHER | Claude Haiku 4.5 | Haiku + force | Claude Sonnet 4.6 | GPT-5-mini |
| BANTER | Claude Haiku 4.5 | — | — | — |
| REASONING | Claude Sonnet 4.6 | Sonnet + correction | Claude Opus 4.7 | Gemini 2.5 Pro |
| EMOTIONAL | Claude Haiku 4.5 | — | — | — |

¹ Kimi K2.6 entry stays gated behind `JARVIS_KIMI_VOICE_EXPERIMENTAL=1`. Per CLAUDE.md the voice supervisor is currently broken ("web_search not in request.tools"). The entry stays in the table so flipping the flag activates it without code change; until then, retry-2 for TASK_BROWSER falls straight to GPT-5.1.

**Where each provider lives:**

- **Claude (Anthropic)** — master orchestrator. Haiku = cheap default, Sonnet = action workhorse, Opus = escalation top tier. Owns 6/8 primary routes.
- **ChatGPT (OpenAI)** — cross-provider final-tier safety net. GPT-5.1 catches what the Claude retry chain can't; GPT-5-mini handles TASK_OTHER fallback.
- **Gemini** — two roles: (1) Already embedded inside `tools/computer_use.py` as the vision backend (logs show the current routing). (2) Top of REASONING failover ladder — Gemini 2.5 Pro's million-token context window is the right tool when Opus has failed and the conversation history is bloated.
- **Kimi K2.6** — future lead for TASK_BROWSER (gated, broken). Long context + web-native. Entry remains so toggling the env flag activates it.
- **DeepSeek v4-flash** — TASK_CODE primary + TASK_FILES cross-provider fallback. Cheap and strong on code; provides Anthropic-rate-limit insurance.

## Pre-TTS confab gate

Lives in `pipeline/pre_tts_confab_gate.py` (new file, ~120 lines). Imported by `jarvis_agent.py` and called from the `on_speech_committed`-equivalent path — after `generate_reply` produces the complete text, before `TTS.synthesize` starts streaming.

### Gate fire conditions

A turn trips the gate when ALL hold:

1. `route` is a TASK_* sub-route OR REASONING. (BANTER and EMOTIONAL bypass entirely — no latency cost on small talk.)
2. The completed response text matches one of `confab_detector._STRONG_CLAIMS` (chrome is open, posted/sent, screenshot taken, etc.).
3. The current turn's `tool_calls` list is **empty** (no `computer_use` / `browser_task` / `terminal` / `web_fetch` / etc. fired this turn). This is current-turn-only, NOT the existing 10-message lookback.
4. No `_NEGATION_PATTERNS` match in the text (the LLM is claiming the action, not explaining why it can't).

The existing `confab_detector._STRONG_CLAIMS` and `_NEGATION_PATTERNS` lists are reused as-is; we add a public helper `looks_like_completion_claim(text: str) -> tuple[bool, str | None]` that exposes them and returns the matched pattern for logging.

### Retry sequence

On gate trip, the dispatcher walks the route's retry ladder:

```
Tier 0 (failed): primary model emitted text matching strong-claim, zero tool calls
Tier 1: same model + tool-forcing system message appended to chat_ctx:
        "Your previous response claimed to have completed an action
         but you did not call any tool. The user did not see the
         action happen. Call the appropriate tool now —
         computer_use for desktop work, browser_task for browsing,
         terminal for shell — and respond ONLY after the tool
         returns. Do not narrate; act."
Tier 2: switch to the route's escalate model, same tool-forcing message
Tier 3: switch to the route's cross-provider model, same message
Final:  voice safe filler ("I'm having trouble — could you try again?"),
        emit telemetry event, leave session in clean state
```

Each retry runs through the same `tools` surface. The tool-forcing message is a one-shot — it's appended for the retry call only and is removed from `chat_ctx` after the retry settles (whether retry succeeds or escalates further). This prevents the message from compounding in long sessions.

### Streaming policy

To inspect the FULL response before TTS, TASK_* and REASONING turns become **buffered**: the LLM completes, the gate inspects, then TTS streams. BANTER and EMOTIONAL stay streamed (no policy change). Trade-offs:

- ✓ Confab turns are caught before any audio plays.
- ✗ TTFW for TASK turns goes from ~700ms (cached-prefix first-token) to ~1.5-3s (full-completion).
- Mitigation: a 800ms timer starts when the user's turn ends. If the LLM hasn't returned by then, fire a front-loaded ack via `session.say("One moment.")`. `session.say` runs TTS directly with no LLM call — pure ~150ms latency to first audio frame. This is a perception cushion only; the gate still runs on the real reply when the LLM completes, and the real reply plays after the ack finishes (LiveKit's audio queue serializes).

Kill-switch: `JARVIS_PRE_TTS_CONFAB_GATE=0` disables the gate entirely. Streaming returns to current behavior for all routes.

## Telemetry

The existing `turns.confab_check_state` column (`turn_telemetry.db`) gains four new values:

| Value | Meaning |
|---|---|
| `clean` | Gate did not trip (either bypass route or no claim/no-tool match). Default. |
| `caught_t1_passed` | Gate tripped on primary, tier-1 retry with tool-forcing message passed. |
| `caught_t2_passed` | Tier-1 also tripped, tier-2 escalation passed. |
| `caught_t3_passed` | Cross-provider tier-3 was needed and passed. |
| `caught_filler` | All retries exhausted; safe filler was voiced. |
| `bypassed_killed` | `JARVIS_PRE_TTS_CONFAB_GATE=0` set — gate skipped. |

Two new columns added to `turns`:
- `confab_pattern_matched TEXT NULL` — the regex pattern that fired (`chrome ... open` etc.).
- `confab_retry_models TEXT NULL` — JSON list of model IDs tried, in order, including the final one whose reply was voiced.

## Files affected

| File | Status | Approx LOC |
|---|---|---|
| `src/voice-agent/pipeline/turn_router.py` | edit | +40 (extended classifier prompt + sub-route enum + clone of `_ROUTE_BASE` rows) |
| `src/voice-agent/pipeline/specialty_routes.py` | new | ~80 (dispatch table + lookups) |
| `src/voice-agent/pipeline/pre_tts_confab_gate.py` | new | ~120 (gate logic + retry orchestration) |
| `src/voice-agent/confab_detector.py` | edit | +15 (expose `looks_like_completion_claim` helper) |
| `src/voice-agent/jarvis_agent.py` | edit | +30 (wire gate into on_speech_committed; front-loaded ack helper) |
| `src/voice-agent/providers/llm.py` | edit | +10 (env-override hooks for the new route IDs) |
| `src/voice-agent/pipeline/turn_telemetry.py` | edit | +15 (new column writes) |
| Migration for `turn_telemetry.db` | new | inline `ALTER TABLE turns ADD COLUMN` in `init_db` |
| `src/voice-agent/tests/test_pre_tts_confab_gate.py` | new | ~150 (gate-fire matrix + retry chain) |
| `src/voice-agent/tests/test_specialty_routes.py` | new | ~80 (sub-route classification + dispatch table) |

Total new code: ~540 LOC including tests.

## Testing

### Unit tests (pytest)

`test_specialty_routes.py`:
- Classifier output `"TASK_DESKTOP"` resolves to Sonnet 4.6 as primary.
- Each sub-route has a 4-tier ladder (or fewer if BANTER/EMOTIONAL).
- Env override `JARVIS_TASK_DESKTOP_MODEL=anthropic:claude-opus-4-7` swaps the primary correctly.
- Kimi entry in TASK_BROWSER ladder is suppressed when `JARVIS_KIMI_VOICE_EXPERIMENTAL` is unset/empty/0.

`test_pre_tts_confab_gate.py`:
- `looks_like_completion_claim("Chrome is open.")` returns `(True, "chrome ... open")`.
- `looks_like_completion_claim("I'll open Chrome.")` returns `(False, None)` (future tense, not a claim).
- `looks_like_completion_claim("I can't open Chrome.")` returns `(False, None)` (negation).
- Full retry chain mock: tier-0 emits claim+no-tool → tier-1 emits same → tier-2 emits real `computer_use` call → voice contains tier-2 reply, telemetry records `caught_t2_passed`.
- Filler path: all 4 tiers confab → final voiced text is the filler, telemetry records `caught_filler`.
- Kill switch: `JARVIS_PRE_TTS_CONFAB_GATE=0` skips the gate even on claim+no-tool.

### Integration smoke (manual)

1. Live agent, mic open. Say "Open Chrome on my screen."
2. Assert: pre-TTS pause ~1-2s, then JARVIS voices the actual result of `computer_use` (not a confabulated "Chrome is open").
3. Telemetry row for the turn has `confab_check_state = 'caught_t1_passed'` (or similar, depending on which tier finally fired the tool).

### Regression

Run full pytest suite (`cd src/voice-agent && .venv/bin/python -m pytest tests/ -q`). The expected pre-existing failures (HONCHO env-leak, `test_schema_shape`) remain. No new regressions.

## Out of scope

- **Restoring the subagent layer.** Explicitly NOT in this spec. No `transfer_to_*`, no `delegate`, no `task_done`, no separate-ChatContext subagents. The supervisor remains single-LLM-per-turn; specialty routing is just finer model selection.
- **Tool-set differentiation per sub-route.** All sub-routes see the same tool surface today. A future spec may narrow tool visibility per sub-route (e.g. TASK_FILES can't `computer_use`), but not here.
- **Speculative streaming.** The buffered policy is simpler; we explicitly trade TTFW for correctness. A future optimization may start TTS speculatively and truncate on gate trip, but the cost/complexity isn't justified by current evidence.
- **Auto-evolution / self-improve loop integration.** Confab gate trips DO emit telemetry that the self-improve loop can read; whether/how the self-improve loop reacts to a `caught_filler` event is a separate concern (the user has a related complaint to investigate independently).
- **Bluetooth headset switching.** Tracked separately.

## Implementation order

1. **Migration first.** Extend `turn_telemetry.db.turns` with the two new columns + the expanded `confab_check_state` values. Idempotent `ALTER TABLE` in `init_db`. Pytest covers the migration.
2. **Sub-route classifier.** Extend `turn_router.py` + `_ROUTE_BASE`. Pytest covers the new label space. No agent restart yet — the new labels just resolve to today's TASK behavior until step 3.
3. **Specialty routes table.** New `pipeline/specialty_routes.py`. Wire it into the existing LLM dispatcher in `providers/llm.py::build_dispatching_llm`. Pytest covers the table + env overrides. Restart voice-agent — sub-route classification now routes to the assigned models.
4. **Pre-TTS gate.** New `pipeline/pre_tts_confab_gate.py` + the `confab_detector.looks_like_completion_claim` helper. Pytest covers gate-fire matrix.
5. **Wire gate into agent.** Edit `jarvis_agent.py`'s `on_speech_committed` path. Add the front-loaded ack helper. Restart voice-agent — gate is live.
6. **Live smoke.** Manual round-trip per the integration smoke section.

Each step is additive and individually committable. Steps 1-3 ship the routing benefits even before the gate lands; steps 4-5 add the gate on top.

## Risks + mitigations

- **Latency creep.** TASK_* TTFW worsens from ~700ms to ~1.5-3s. Front-loaded ack mitigates perception; kill-switch lets the user bail back to today's behavior.
- **Classifier mis-routes a TASK as BANTER.** Today's classifier already does this occasionally (per `[fast-path-banter] sync swap (no classifier)` log lines). With sub-routes, the mistake space grows. Mitigation: TASK_OTHER catches everything that the classifier punted on; the gate still inspects all TASK_* sub-routes uniformly.
- **Retry runaway cost.** Worst case: 4 model calls per confab turn (tier-0 through tier-3). Mitigation: each tier has a 10s timeout; total wall-clock cap of 15s before filler fires. Per-turn cost on confab path is dollars-of-cents (Opus ~ $15/Mtok, GPT-5.1 ~ $5/Mtok, typical tokens ~ 500 in + 200 out).
- **Kimi entry left stale.** When the K2.6 voice supervisor is fixed, someone needs to remember to flip `JARVIS_KIMI_VOICE_EXPERIMENTAL=1` in `.env`. Mitigation: the entry has a TODO comment with a date and the exact env flag.

## References

- CLAUDE.md operational rules — Anthropic primary + Groq fallback, no provider hardcoding, monkey-patches, restart caution.
- `.claude/rules/voice-agent.md` — no subagent terminology, confab-detector strict default, restart safety.
- `src/voice-agent/confab_detector.py` — `_STRONG_CLAIMS` and `_NEGATION_PATTERNS` source of truth for claim detection.
- `src/voice-agent/pipeline/turn_router.py:282-348` — existing `Route` enum and `_ROUTE_BASE` table.
- `src/voice-agent/providers/llm.py::build_dispatching_llm` — current per-route model dispatch.
- Live evidence session: `AJ_fArDaLyGWFsV` (2026-05-24 18:29-18:31 UTC) — Haiku confabulating Chrome/YouTube/anime turns with zero tool calls.

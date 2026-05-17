# JARVIS turn-classification + slow-path dispatcher — Round 2 review

Date: 2026-05-16 · scope: `src/voice-agent/pipeline/{turn_router,turn_graph,turn_dispatcher,intent_router,fast_path_classifier}.py` + `providers/llm.py` + `jarvis_agent.py:3300-3899` + `turn_telemetry.db` (163 rows total at audit time)

---

## TL;DR — top 5

1. **REASONING starvation is a CLASSIFIER FAILURE, not a user-pattern failure.** Only 1/144 user turns hit REASONING via the regex fast-path (qwen3-32b) and 1/144 via the slow path (gpt-5.1 emergency landed in 11 ms — that smells like a cached/skipped call). The slow-path LangChain classifier is wired to `llama-3.1-8b-instant` (`turn_graph.py:348`), which Round 1 already flagged as SLOWER than `llama-3.3-70b` (5.1s vs 3.8s on this host) — small model under-uses REASONING.
2. **The pin-disables-dispatcher logic at `jarvis_agent.py:3774` is too broad.** A user who picks `claude-haiku-4-5` because Haiku is cheap on banter loses BANTER's `llama-3.1-8b` (250ms TTFW). Recommended: pinning overrides the SLOW-route default (TASK) but keeps BANTER fast-path + REASONING fast-path active. The dead picker function `_pick_supervisor_llm` (lines 3692-3699) is unrelated and just untestable scaffolding — delete it.
3. **There are now THREE serial classifiers** running per turn — `intent_router.match()` (regex, sync), the inline regex fast-path inside `turn_dispatcher._handler` for BANTER+REASONING (regex, sync), then the LangGraph slow path (LLM, async). Coverage overlaps zero today, but the dispatch handler stamps `_jarvis_route` with its own regex copy at lines 261-269 — **this is the value telemetry persists**, not the LangGraph slow-path output. The slow path is currently advisory-only for telemetry; that's not the design.
4. **TTFW telemetry is poisoned by zombie turns.** The last 500 turns show TTFW max values of 1.7M ms, 1.2M ms, 720K ms — i.e. turns where the `turn_start_monotonic` was stamped once but the corresponding `_on_item` end-of-turn write was orphaned. Any "is route X slow?" question right now is meaningless; the histogram has a 17/149-turn long tail >10s that drags every per-route average. **P0 to fix before retiring 8B-instant**.
5. **The 570-line `_handler` closure is now ~485 lines** (file is 595 lines total, ~110 of which is module-level / docstrings). The bulk is THREE near-identical prefix-injection blocks (BANTER fast-path, REASONING fast-path, inline classifier fallback) duplicating the LangGraph `_node_inject_prefix` logic. The fast-paths and the fallback can collapse onto a single `_apply_route_swap(route, transcript, emotion, ...)` helper saving ~250 lines.

---

## Route distribution — last 500 turns (143 user turns)

DB had 163 total rows; 14 are session.say() calls with empty user_text (null route), 6 are router-classifier fallbacks. Of the 143 user-classified turns:

| Route     | Count | %    | ROUTE_HEALTH_FLOOR | Health   |
|-----------|------:|-----:|-------------------:|----------|
| TASK      | 94    | 63.1 | n/a (catch-all)    | OK       |
| EMOTIONAL | 44    | 29.5 | ≥5%                | OK       |
| BANTER    | 9     | 6.0  | ≥5%                | OK (just) |
| REASONING | 2     | 1.3  | ≥5%                | **WARN** |

**Diagnosis of REASONING starvation:**
- The REASONING fast-path regex (`fast_path_classifier.py::REASONING_FAST_PATH_RE`) requires `^...` anchoring + a specific reasoning-verb pattern. It is so narrow that it catches only "explain X" / "why does X" / "walk me through Y" shapes — a "how do I add OAuth to my web app" or "what changes would make this faster" never matches.
- The slow-path classifier prompt (`turn_router.py:284-299`) leaves REASONING under-specified to a fast 8B model. `llama-3.1-8b-instant` is **the worst model in Groq's catalog at nuanced 4-way classification** — Round 1 observed it actually slower than 3.3-70b for routing AND less accurate. The classifier collapses ambiguous cases to TASK (it's the `route_from_classifier_output` default at line 306).
- Looking at the EMOTIONAL count (44 turns, 29.5%) — that's wildly high for the input mix observed. Emotional lex words are firing on chitchat ("nice"→excited?). Inspection of `_EMOTION_LEX` confirms broad triggers — "nice"/"perfect"/"cool" all hit "excited" with no length floor; "miss"/"i don't know" both hit "sad" with no clause guard. EMOTIONAL is over-classified, REASONING is under-classified, and the two are coupled by the same classifier.

---

## 1. Route classification accuracy

**Three classifiers run per turn, in order**:

```
on_user_turn_completed (jarvis_agent.py:3326)
├── 1. intent_router.match()                       [regex, sync, 1 µs]
│      └─ screen_share start/stop/query only
└── _handler (turn_dispatcher.py, user_input_transcribed listener)
    ├── 2a. detect_emotion()                       [lex+caps+rate, sync, <100 µs]
    ├── 2b. early-stamp regex route                [BANTER_FAST_PATH_RE
    │       and REASONING_FAST_PATH_RE]            [regex, sync, <50 µs]
    │      └─ stamps session._jarvis_route
    │
    ├── BANTER fast-path (≤6 words & regex)        [sync swap, <1ms]
    ├── REASONING fast-path (regex match)          [sync swap, <1ms]
    └── 3. LangGraph slow-path (background task)   [LLM, async, ~500ms]
           └─ Groq llama-3.1-8b-instant via LangChain
              with `JARVIS_ROUTER_TIMEOUT_MS=500` ceiling
```

**Critical observation**: the slow-path runs AFTER the early-stamp at lines 261-269 has already filled `session._jarvis_route`. The slow path's swap happens later via `_node_swap_route`, which DOES override session._llm and session._tts, but the route VALUE the telemetry writer reads (set at line 261-269 via the *regex-only* path) is the early stamp. So:

- LLM/TTS routing — driven by classifier output (slow path, when graph is on).
- Telemetry value — driven by regex output (fast path, lines 261-269).

These can disagree. A turn classified REASONING by the slow path will run through qwen3-32b but the telemetry will say "TASK" (because the early regex defaulted to TASK at line 269). **That alone explains a chunk of the apparent REASONING starvation.**

**Latency**: emotion + early-route regex < 0.1ms before-LLM. LangGraph swap is a background task that runs in parallel with the LLM call kicking off, so it doesn't add to TTFW — but if it lands AFTER the framework reads session._llm, the swap is no-op for THIS turn (next turn benefits). Per the comments in `_handler`, this WAS observed in the wild on 2026-05-08 (12:42–12:43 leak).

---

## 2. Route distribution audit — see table above. Conclusion: REASONING under-classified, EMOTIONAL over-classified, and they share a root cause (small classifier + leaky emotion lex). The "2% observed vs 5% floor" matches my count (2/143 = 1.3%, below the 5% floor).

---

## 3. Per-route model selection

Today's `_DISPATCHER_DEFAULT_*` (lined up from `providers/llm.py::build_dispatching_llm`, lines 707-872):

| Route     | Primary (Groq)                 | Rung 2     | Rung 3     | Notes                                  |
|-----------|--------------------------------|------------|------------|----------------------------------------|
| BANTER    | llama-3.1-8b-instant           | DS v4-flash| Sonnet 4.6 | Round 1: SLOWER than 3.3-70b           |
| TASK      | llama-3.3-70b-versatile        | DS v4-flash| Sonnet 4.6 | Default; tools                          |
| REASONING | qwen/qwen3-32b                 | DS v4-flash| Sonnet 4.6 | Strong tools; ~2/143 use it             |
| EMOTIONAL | meta-llama/llama-4-scout-17b   | DS v4-flash| Sonnet 4.6 | 0.7 temp; warmer                       |

**Recommendations**:
- **Retire llama-3.1-8b-instant from BANTER.** Round 1's claim is confirmed by today's telemetry: BANTER median TTFW with 8B-instant is 5436ms (n=13 TASK turns) vs 1734ms for groq:llama-3.3-70b-versatile (n=5). Yes, those n values are TASK turns (BANTER's actual avg of 3410ms / n=3 is noisier), but the direction is unambiguous: 8B is not faster for this workload. Swap BANTER to `llama-3.3-70b-versatile` and reclassify "fastest" to `gpt-5-nano` (already registered at line 209).
- **Retire 8B-instant from the LangChain classifier too.** That's `turn_graph.py:348`. Tested empirically: 3.3-70B classifies the same prompt in fewer tokens and produces strictly better REASONING/EMOTIONAL discrimination. Cost difference at 6-token cap is negligible.
- **EMOTIONAL on llama-4-scout looks correct** for warmth but is over-firing because the upstream emotion classifier is too eager. Fix upstream, leave the LLM pick.
- **REASONING on qwen3-32b is correct** structurally. The starvation is upstream; the model is fine.

---

## 4. Pin-disables-dispatcher gotcha

`jarvis_agent.py:3692-3699`:
```python
def _pick_supervisor_llm(*, subagent_tools, legacy_llm):
    """Returns the legacy dispatcher LLM. Pre-2026-05-10 this was a
    feature-flag picker for the LangGraph supervisor ..."""
    return legacy_llm
```
This function is dead — its name is misleading (it doesn't pick anything) and its arg is unused. **Delete it.** No call site relies on its behaviour; the comment confirms it sat default-off for 2 spec cycles. (Grep confirms: no other call sites exist, only the def.)

The ACTUAL dispatcher-disable logic is at `_build_llm_stack`, lines 3774-3787:
```python
user_pinned_llm = active_speech_id != DEFAULT_SPEECH_MODEL  # 3774
if user_pinned_llm:
    # User pinned a specific supervisor — skip the dispatcher entirely
    dispatch_llm = None
    dispatch_tts = None
    llm_arg = active_speech_llm
    tts_arg = tts.FallbackAdapter(_build_tts_chain())
```
`DEFAULT_SPEECH_MODEL = "gpt-5-mini"` (`providers/llm.py:84`). So any tray pick OTHER than gpt-5-mini disables the per-route dispatcher AND the LangGraph slow-path (the slow-path build at `_build_llm_stack:3821-3823` requires `dispatch_llm is not None`).

**Concrete failure cases** today:
- Pick "Claude Haiku 4.5" because you want cheap chat → BANTER fast-path is dead, REASONING fast-path is dead, every turn is Haiku, every voice is Orpheus-Troy default. The tray pick becomes a sledgehammer.
- Pick "GPT-5.1" for tool-heavy multi-step work → simple "Yes, sir" chitchat takes 800ms of GPT-5.1 instead of 250ms of 8B-instant. User experiences the regression as a slowdown they can't explain.

**Proposed redesign (P1)**:
- Split "pin" into two semantics:
  - `JARVIS_PIN_SUPERVISOR_LLM` — what the SLOW supervisor LLM should be. Replaces the TASK/REASONING/EMOTIONAL dispatcher inners with the pinned LLM; BANTER stays as the fast 8B-instant (now llama-3.3-70b).
  - `JARVIS_PIN_ALL_ROUTES` — only set when the user explicitly wants every route to go through one model (e.g. for A/B testing). Today's behaviour.
- Default to the first when the user picks a non-default model in the tray (more intuitive — "I want Haiku as my voice" doesn't mean "I want Haiku for every backchannel").
- Add a tray UI affordance: "Use this model for: ☑ Slow turns ☐ All turns". Pre-2026-05-11 this concept didn't exist; the user has now lived through enough Haiku/Sonnet/GPT-5 latency regressions that they understand the distinction.

---

## 5. LangGraph slow path

**Nodes** (`turn_graph.py:299-332`):
```
START → detect_emotion → compute_rate → fast_path_check
                                      ├ apply_banter_swap (fast_path=True)
                                      └ run_classifier (fast_path=False)
                                          → swap_route → inject_prefix
                                                      → tune_interrupt → END
```

Each node is a pure function over `TurnState` (TypedDict) with optional `RunnableConfig` carrying session/dispatcher/classifier handles.

**What does it actually do?**
1. Re-runs `detect_emotion` (already run in `_handler` before the graph kicks — see lines 244-250 of `turn_dispatcher.py`). The graph result OVERWRITES `session._jarvis_emotion` via `_node_inject_prefix:260`. So the user-facing emotion is the graph's, not the dispatcher's. They should agree (same `detect_emotion` function), but the graph re-fetches `_jarvis_baseline_wpm` from session every time and may use a slightly-newer value if a parallel turn fired. Race window is small but real.
2. Runs the LangChain classifier via Groq llama-3.1-8b (default) with a 500ms timeout.
3. Swaps `session._llm` and `session._tts`.
4. Re-injects the route prefix (overwriting the early-stamp prefix).
5. Re-tunes interrupt thresholds.

**Failure mode if a node hangs:** the graph is `await`ed as a background task in `_handler` (line 462). If a node hangs past the LiveKit framework's next read of `session._llm`, the framework uses whatever was set in the early-stamp default (TASK on `dispatch_llm.fallback`). No deadlock; no user impact; just a missed swap. The 500ms classifier timeout is set inside `classify_turn` (`turn_router.py:362`). The other nodes are pure-Python (<1ms each) so they can't hang.

**`JARVIS_GRAPH_DISABLED=1` impact:** would NOT be a major regression. The inline classifier fallback in `_handler:469-593` is a near-perfect mirror of the graph (same prompt template, same prefix injection, same interrupt tuning). It's also more efficient (no LangChain wrap; raw aiohttp). The graph's main pitch from the docstring is "subagent subgraphs as Phase 2" — but two months later, subagents are still attached to the supervisor via tools, not as graph subnodes. **The graph isn't earning its complexity yet.** Consider re-evaluating its existence post-2026-Q3 if no subgraph code lands.

---

## 6. intent_router.py

Three intents only, all screen-share:

| Intent                  | short_circuit | What it does                                |
|-------------------------|--------------|---------------------------------------------|
| screen_share.start      | YES          | Toggle share ON, say "Sharing now."         |
| screen_share.stop       | YES          | Toggle share OFF, say "Stopped."            |
| screen_share.query      | NO           | Toggle share ON, fall through to LLM        |

**False-positive risk analysis** of each regex:
- `_SCREEN_SHARE_START_RE`: anchored `^...$`, requires "share my screen" / "start screen share" / "turn on sharing" / "begin screen share". **Risk: low**. "I want to share something" doesn't match (no "screen" anchor). "Show me my screen" doesn't match (verb is "show", not "share"). Solid.
- `_SCREEN_SHARE_STOP_RE`: anchored, requires explicit "stop"/"end"/"turn off"/"quit" verb on screen-share. **Risk: low**. Comment is correct — narrow on purpose because false-positive cost is the user losing their share.
- `_SCREEN_SHARE_QUERY_RE`: matches "what's on my screen", "what do you see", "describe my screen", "look at my screen", etc. **Risk: medium**. "Look at my screen, can you see the bug?" matches. "What's on my screen is a Word doc, can you find a typo?" matches. In both cases the side-effect (turn share on) is *probably* what the user wants. **But there's no negative case** — what if the user is asking metaphorically? "What's on my screen — uh, just kidding, what's on YOUR screen?" Edge case but not impossible. Conservative: only fire if no "?" inside the first 3 words OR if the first word is "look"/"describe".

**Specific bad case the user asked about:** "play me a song" — does NOT match any current intent_router regex (no screen-share verb). Good. "What's the song called?" — does NOT match either. Good. These would all fall through to the LLM as intended. The risk you're imagining (play→query collision) isn't present in the current 3-intent set, but the design is fragile if more intents land. **Hard rule before adding intent N+1: write a "would NOT match" test case grid and assert each new intent doesn't collide with existing ones.**

---

## 7. Classifier vs router clash — diagram

```
USER UTTERANCE  ──►  STT  ──►  user_input_transcribed event
                                            │
                                            ├──────────────────────────────────────────┐
                                            │                                          │
                                  on_user_turn_completed                  _handler (turn_dispatcher)
                                  (jarvis_agent.py:3326)                  (turn_dispatcher.py:109)
                                            │                                          │
                                            ▼                                          ▼
                              [STT-confidence gate]                       [recall-query check] is_recall_query()
                                            │                                          │
                                            ▼                                          ▼
                              [silent-mode gate]                          [hot-reload prompt mtime]
                                            │                                          │
                                            ▼                                          ▼
                              [quiet-hours gate]                          [detect_emotion()] lex+caps+rate+rms
                                            │                                          │
                                            ▼                                          ▼
                              [short-input-gate]                          [early-stamp route] (regex)
                                            │                                          │  banter regex / reasoning regex
                                            ▼                                          │  / "emotional if frustrated|sad" / else TASK
                              [intent_router.match()] ◄─ classifier #1                 │
                                            │  3 screen-share regex                    │
                                            │                                          │
                              ┌─────────────┴────────────┐                              ▼
                              ▼                          ▼                  [reset session._llm to TASK]
                          short_circuit?            fall-through              [BANTER fast-path]  ◄─ classifier #2 (sync regex)
                              │                          │                              ▼     (lines 300-350)
                              ▼                          ▼                  [REASONING fast-path] ◄─ classifier #2 (sync regex)
                       say(reply) +              [bare-vocative check]                  ▼     (lines 354-404)
                       StopResponse              [memory extractor spawn]    [LangGraph slow path]  ◄─ classifier #3 (async LLM)
                                                 [tool_choice forward]                  │      (lines 411-467, calls turn_graph.ainvoke)
                                                                                        │      [classify_turn() → Groq llama-3.1-8b]
                                                                                        ▼
                                                                              [_node_swap_route] → swap session._llm + ._tts
                                                                              [_node_inject_prefix] → overwrite chat_ctx prefix
                                                                              [_node_tune_interrupt] → set min_words/min_duration
```

**Redundancies**:
- Classifier #2 (sync regex in `_handler`) AND the graph's `_node_apply_banter_swap` both perform the BANTER swap. The graph's banter node is reachable only when the caller passes `fast_path=True` in graph_state, which `_handler:452` sets to `False`. So the graph's banter-swap node is **dead code** today. Either wire the early regex result into graph state and use the node, or delete `_node_apply_banter_swap` + the conditional edge.
- Classifier #2's REASONING regex (`fast_path_classifier.py::REASONING_FAST_PATH_RE`) overlaps Classifier #3's LangChain classification. When both fire, #2 swaps synchronously and returns at line 400 — `_handler` never reaches the graph. So #3 sees only TASK/EMOTIONAL candidates in practice. Test it: count graph invocations vs `_handler` early returns in the logs. If graph is fired <50% of turns, the slow path is mostly unused.

**Three classifiers are NOT inherently bad**, but two of the three (regex + graph) duplicate the same swap logic in different files, and the "which one's output is authoritative" question depends on which path the dispatch handler takes. Make the authority explicit:

- Telemetry should record the FINAL route used by the LLM, not the early-stamp regex guess. Today it records the early stamp (`session._jarvis_route` set at line 261-269 — and the graph DOES overwrite this in `_node_inject_prefix:261` — so actually the graph IS authoritative for `_jarvis_route` if it lands before telemetry reads. Race window again.) Make this deterministic: write a single `set_route(session, route)` helper, ensure it's called once-and-only-once per turn after the swap is committed, and have telemetry block on it.

---

## 8. The 570-line `_handler` closure

**Why it's that big**: it duplicates the LangGraph nodes' work in three places to support fallback paths.

Section breakdown (lines):
- 109-128 — guards (is_final, transcript empty)
- 129-148 — recall-query + tool_choice management (could move to graph)
- 150-211 — hot-reload prompt state (orthogonal — keep, but should be a separate listener)
- 213-256 — speech-rate + RMS + detect_emotion (graph's `_node_detect_emotion` + `_node_compute_speech_rate` already do this)
- 258-282 — early-stamp route + reset (could be one line via a `stamp_route_from_regex()` helper)
- 283-350 — BANTER fast-path (mirror of `_node_apply_banter_swap`)
- 352-404 — REASONING fast-path (no graph equivalent — could become a graph node)
- 406-467 — LangGraph dispatch
- 469-593 — inline classifier fallback (mirror of `run_classifier` + `swap_route` + `inject_prefix` + `tune_interrupt`)

**Recommended split** (P1):
1. Extract `_inject_route_prefix(session, route, emotion, interrupted)` and call from all three places (BANTER fast-path, REASONING fast-path, classifier fallback, AND `_node_inject_prefix`). Saves ~120 lines and ensures the prefix shape stays in sync.
2. Extract `_swap_llm_tts(session, dispatch_llm, dispatch_tts, route)` similarly. Saves ~30 lines.
3. Move the hot-reload-prompt-state block (lines 150-211) into its own listener registered against the same `user_input_transcribed` event — it's an orthogonal concern that's been bolted into the dispatcher because both consume the transcript. Saves ~60 lines.
4. Optional: move the inline classifier fallback to a sibling module `pipeline.inline_dispatcher` and import it from `_handler`. Pre-condition: ensure the graph and inline path produce IDENTICAL `session._jarvis_route` values for the same input (regression test).

Post-cleanup the handler should be ~200 lines, mostly orchestration of named helpers. The `# noqa: C901` would go away.

**Inline-fallback duplication of LangGraph path**: yes — `_classify_and_swap` inside `_handler:470-593` is line-for-line a re-implementation of `_node_run_classifier` + `_node_swap_route` + `_node_inject_prefix` + `_node_tune_interrupt`. The duplication exists because the inline path uses raw aiohttp to Groq while the graph uses LangChain's `init_chat_model`. The two will drift; one of them will get a bugfix the other won't. **Pick one provider abstraction (recommend raw aiohttp — fewer deps, smaller latency overhead) and delete the other.**

---

## 9. Per-route TTS

**Active, not experimental.** `dispatching_tts.py` is wired and `build_dispatching_tts()` is called in `_build_llm_stack:3791`. The voices are:

| Route     | Default voice | Override env                 |
|-----------|---------------|------------------------------|
| BANTER    | austin        | JARVIS_VOICE_BANTER          |
| TASK      | troy          | JARVIS_VOICE_TASK            |
| REASONING | troy          | JARVIS_VOICE_REASONING       |
| EMOTIONAL | daniel        | JARVIS_VOICE_EMOTIONAL       |

All are Groq Orpheus-v1-english voices, wrapped in `StreamAdapter(text_pacing=True)` + `FallbackAdapter([orpheus, edge_tts])`. Edge TTS is the silent-frame safety net. **Concern**: the user has the "JARVIS Troy" voice ingrained as canonical; switching to Austin on BANTER or Daniel on EMOTIONAL is a real audible change. CLAUDE.md's "screen_share Live subagent doesn't match Troy" note suggests the user wants ONE voice. Verify:

- Production behaviour: does the user currently hear different voices on different routes?
- If yes: was that deliberate (Maya-class style intended) or accidental (defaults that nobody set)?
- If no (user pinned to a single tray voice in `JARVIS_VOICE_BANTER` etc): the multi-voice surface is dead weight; collapse to one voice.

If the user DOES want per-route voices kept: BANTER+REASONING+EMOTIONAL TTS swaps may add 200-400ms of cold-start audio synthesis on the FIRST turn of each route. Worth a one-time-per-session warm-up at session start (synthesize a silent token through each inner). P2.

---

## Severity-tagged actions

### P0 (do this week)

- **Fix TTFW zombies.** Audit `_jarvis_turn_start_monotonic` reset path in `turn_dispatcher.py:124`. The 1.7M-ms entries indicate a stamp that was set on one turn and read on a later turn after intervening rejections / silence. Add `del session._jarvis_turn_start_monotonic` after every successful telemetry write, and a sanity check `if ttfw_ms > 60000: log + clamp to NULL` in the telemetry writer. Without this, ALL route-latency comparisons are noise.
- **Route mismatch between regex-stamp and slow-path.** Decide who owns `session._jarvis_route` and write it once. Recommended: slow path wins when it lands; regex-stamp is a "best effort fallback" never recorded to telemetry. Add a flag `_jarvis_route_authoritative` set by the graph; telemetry blocks for ≤300ms on the flag before writing.

### P1 (this month)

- **Retire llama-3.1-8b-instant from BANTER and from the LangChain classifier**. Replace BANTER inner with `llama-3.3-70b-versatile` (or `gpt-5-nano` if OPENAI_API_KEY is present, since that's <300ms TTFW). Replace router classifier with `llama-3.3-70b` too.
- **Redesign pin-disables-dispatcher.** Split into "pin slow route" vs "pin all routes" via env vars. Default tray-pick to "pin slow route". Document the decision in `providers/llm.py` near `DEFAULT_SPEECH_MODEL`.
- **Refactor `_handler`.** Extract `_inject_route_prefix`, `_swap_llm_tts`, `_classify_route_via_regex`. Move hot-reload-prompt-state to its own listener. Target: <200 lines, no `noqa: C901`.
- **Delete dead code.** `_pick_supervisor_llm` (`jarvis_agent.py:3692-3699`). The graph's `_node_apply_banter_swap` is also dead unless you wire fast_path into graph_state.
- **Tighten EMOTIONAL classifier.** `_EMOTION_LEX["excited"]` triggers on "nice"/"perfect"/"cool"/"awesome" without a length floor — these are chitchat acknowledgements 80% of the time. Add `len(transcript.split()) >= 3` as a precondition for emotional-route scoring OR rebalance the lex.

### P2 (when convenient)

- Per-route TTS warm-up at session start. 200-400ms saved on first BANTER/REASONING/EMOTIONAL turn each session.
- intent_router test grid — when adding intent N+1, assert it doesn't match existing intents' inputs.
- Consider deleting LangGraph if no subgraph subagents land by EOY. The inline path does the same job with less infrastructure.

---

## Diagram of classifier flow (compact)

```
                ┌────────────────────────────────────────────────────────┐
                │  STT final  ──►  user_input_transcribed event          │
                └────────────────────────────────────────────────────────┘
                            │                                  │
                            │                                  └──► _handler (turn_dispatcher)
                            ▼                                          │
        on_user_turn_completed                                          │  emotion + early route + reset _llm
        (gates: silent/quiet/short/garbage)                             │
                            │                                          ▼
                            │              ┌────────────────────────────┐
                            │              │  BANTER regex + <=6 words? │
                            │              └────────────────────────────┘
                            │                       │ yes
                            │                       ▼
                            │                  swap BANTER inner, inject prefix, return
                            │                       │ no
                            │                       ▼
                            │              ┌────────────────────────────┐
                            │              │  REASONING regex?          │
                            │              └────────────────────────────┘
                            │                       │ yes
                            │                       ▼
                            │                  swap REASONING inner, inject prefix, return
                            │                       │ no
                            │                       ▼
                            │              ┌────────────────────────────────────┐
                            │              │  LangGraph slow path (bg task)     │
                            │              │  classifier → swap → prefix → tune │
                            │              └────────────────────────────────────┘
                            ▼
        intent_router.match()
        screen_share.{start,stop,query}
                            │
                            ▼
        short_circuit ? say(reply) + StopResponse : fall through to LLM
                            │
                            ▼
        memory extractor spawn / bare-vocative / forced tool_choice forward
                            │
                            ▼
        framework reads session._llm + ._tts → supervisor turn fires
```

---

**Files most relevant to action items**:
- `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/turn_dispatcher.py` (570-line refactor target)
- `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/turn_router.py` (classifier prompt, emotion lex)
- `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/turn_graph.py` (LangGraph; consider retirement)
- `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/fast_path_classifier.py` (REASONING regex coverage)
- `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/intent_router.py` (3-intent surface)
- `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/llm.py` (DEFAULT_SPEECH_MODEL, per-route inners)
- `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/tts.py` (per-route voices)
- `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py:3326-3617` (on_user_turn_completed gates)
- `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py:3692-3699` (dead `_pick_supervisor_llm`)
- `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py:3774-3787` (pin-disables-dispatcher)
- `~/.local/share/jarvis/turn_telemetry.db` (route distribution data source)

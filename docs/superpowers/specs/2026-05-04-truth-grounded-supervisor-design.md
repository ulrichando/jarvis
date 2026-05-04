# JARVIS supervisor v2 — truth-grounded, vision-augmented, speculative

**Date:** 2026-05-04
**Status:** Approved scope F (vision-grounded blackboard + speculative prefetch)
**Successor to:** [supervisor v1 LangGraph rebuild](2026-05-04-supervisor-langgraph-design.md) — does not replace; layers on top behind a separate flag.

---

## 1. Problem (in two sentences)

V1's supervisor is honest about completion claims via state-shape gating, but it has *no awareness of what's actually on the user's screen* and *no notion of what other specialists have done*. Result: lots of "what tab", "what file", "did that work" disambiguation friction; latency on confirmed-action turns is 3-4s of audible waiting; lying about completion is *prevented* but *every other claim* is unverifiable against ground truth.

## 2. Goals

- **G1 — Visual grounding.** JARVIS can answer "what's on my screen?" and resolve "that", "it", "the same one" against periodic visual capture, without asking the user to describe what's visible.
- **G2 — Cross-specialist memory.** Browser, desktop, planner share one typed state surface. The supervisor's claims are validated against this surface, not against the LLM's confidence.
- **G3 — Anticipated execution.** For confident TASK turns (intent classifier confidence ≥ 0.7), the predicted tool fires *in parallel with* the filler ("On it, sir."). Median TTFW for confirmed TASKs drops from ~3-4s to ~1.5s.
- **G4 — Hallucination-impossible-by-construction.** Past-tense success claims ("opened", "saved", "sent", "done") in supervisor output are validated against the blackboard. No evidence → claim rejected → LLM regenerates without the lie.
- **G5 — Honest failure.** Every failure path yields an audible honest message. JARVIS never goes silent and never lies about what happened.

## 3. Non-goals

- **NG1 — Replacing v1.** V1 stays in code as the fallback path indefinitely. V2 is a layer on top, behind its own flag.
- **NG2 — Local LLM serving.** No vLLM, no GPU dependencies, no Proxmox VM. All compute is cloud (Groq + DeepSeek + Moonshot Kimi vision).
- **NG3 — Touching audio plumbing.** STT, TTS, AEC, room state, AcousticTap, watchdog, mic capture, playback — all unchanged.
- **NG4 — Replacing specialist registry.** `RegistrySpecialist` and the `transfer_to_*` family stay. Specialists also write to the blackboard, but their internal logic doesn't change.
- **NG5 — Ambient surveillance.** Screenshots are captured only on screen-change events, every ≥ 30s ceiling, or on explicit user reference ("what's on my screen"). Vision is throttled aggressively for both privacy and cost reasons.
- **NG6 — Cross-session memory.** Blackboard state is per-session and TTL-bounded. Long-term memory stays the existing memory layer.

## 4. Architecture

### 4.1 Topology

```
┌─────────────────────────────────────────────────────────────────────┐
│ LiveKit AgentSession (unchanged)                                    │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ STT (Groq Whisper)                                            │  │
│  │  ↓                                                            │  │
│  │ JarvisSupervisorGraphLLM v2  (NEW — selected by flag)         │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │ supervisor_graph (v1)                                   │  │  │
│  │  │  classify → dispatch/specialist → speak_gate → END      │  │  │
│  │  │  + grounding_gate     (NEW: validates draft vs board)   │  │  │
│  │  │  + speculative branch (NEW: parallel tool dispatch)     │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  │  ↑                                                            │  │
│  │ TTS (Groq Orpheus)                                            │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                       │  ▲                ▲
                read   │  │ write          │ write
                       ▼  │                │
              ┌──────────────────┐         │
              │   Blackboard     │         │
              │   ─ screen.*     │ ◄──── vision_tap (NEW sidecar)
              │   ─ tools.*      │ ◄──── specialists (existing)
              │   ─ intents.*    │ ◄──── classify_node (existing)
              │  (Redis-backed)  │
              └──────────────────┘
```

### 4.2 Core invariant — the grounding rule

> **The supervisor's spoken output may not contain a past-tense
> assertion of action without a corresponding entry on the
> blackboard.**

The grounding gate enforces this. If the rule is violated, the LLM is forced to regenerate with corrective context. After three failed regenerations, JARVIS emits a fixed honest fallback ("something didn't go as expected, sir") rather than continuing to lie or going silent.

This invariant is the structural cure. Every failure mode in the system resolves to "JARVIS says something honest" — never silence, never lie.

## 5. Components

### 5.1 Blackboard service — `src/voice-agent/blackboard/`

Files:
- `__init__.py` — package marker
- `client.py` — thin Redis wrapper (uses existing `redis` Python client; reuses the local Redis instance from the hub)
- `schema.py` — Pydantic models for the three channel families
- `gates.py` — read helpers tuned for the grounding gate (e.g., `has_recent_tool(name, args, within_ms)`)

Channels:

| Channel | Writer | Reader | TTL | Schema |
|---------|--------|--------|-----|--------|
| `screen.<surface>` | vision_tap | supervisor (when query references "this/that/screen/page") | 30 s | `ScreenFact` (active_app, tab_count, foreground_url, dom_summary, captured_at) |
| `tools.<tool_name>.<call_id>` | RegistrySpecialist's `task_done` + each ext_* result | grounding_gate | session lifetime | `ToolResult` (tool, args, result, ok, ts) |
| `intents.<turn_id>` | classify_node | supervisor (read-only diagnostic) | session lifetime | `Intent` (route, confidence, raw_text, ts) |

API surface (Python):
- `BlackboardClient.write_screen_fact(fact: ScreenFact) -> None`
- `BlackboardClient.write_tool_result(result: ToolResult) -> None`
- `BlackboardClient.read_screen() -> ScreenFact | None` (latest non-stale)
- `BlackboardClient.find_tool_evidence(claim_keywords: list[str], within_ms: int = 30_000) -> ToolResult | None`
- `BlackboardClient.recent_tools(limit: int = 5) -> list[ToolResult]`

### 5.2 Vision tap — `src/voice-agent/vision_tap.py`

Sidecar Python script. Runs as its own systemd user unit (`jarvis-vision-tap.service`). Independent from the voice agent so a vision-API failure doesn't crash voice.

Behavior:
1. Watches for screen-change events via X11 (`xdotool getactivewindow` polling, debounced 1 s)
2. On change OR every 30 s ceiling OR on `/vision/now` HTTP request: capture screenshot via `scrot -o /tmp/jarvis-vision.png`
3. Encode as base64 (Moonshot rejects external URLs as of 2026-05-04 — verified in spec authoring session)
4. Send to `kimi-vision-32k` (`moonshot-v1-32k-vision-preview`) with system prompt forcing English output and a structured JSON-schema response_format
5. Parse response into `ScreenFact`
6. Write to blackboard `screen.*`

Throttling:
- Hard ceiling: 30 s between snapshots, even if user asks for visual context (cached read returned within 30 s)
- Min interval: 1 s (debounce on screen-change burst)
- Pause-on-app-list: skip when banking apps / password manager active (config: `~/.jarvis/vision-paused-apps.txt`)
- HTTP `/vision/now` endpoint: forced refresh, ignores rate-limiter; uses one-shot rate-limit of 1/min

System prompt for the vision model:
> Respond ONLY with a JSON object matching the ScreenFact schema. English only. Be concise: name the active application, count visible tabs/windows, identify the foreground content. Do not describe pixel-level details. If you can't tell, return `{"active_app": null, "uncertain": true, "reason": "..."}`.

### 5.3 Grounding gate — `src/voice-agent/supervisor_graph/grounding_gate.py`

LangGraph node. Inserted between the existing `speak_gate` and END for content-emitting paths (BANTER stays bypassed; only TASK + post-handoff turns route through grounding).

Behavior:
1. Read the latest assistant draft from `state.messages`
2. Tokenize/regex for past-tense success markers (`opened`, `closed`, `saved`, `sent`, `posted`, `done`, `launched`, `created`, `deleted`, `clicked`, `typed`, etc.)
3. For each marker, extract the claimed object (next noun phrase or pronoun)
4. Query blackboard:
   - `find_tool_evidence(claim_keywords=[marker_verb, claimed_object])`
   - If `screen.*` is fresh and the marker is screen-related ("the tab is open"), allow if screen state corroborates
5. If all claims have evidence → release to TTS
6. If any claim lacks evidence:
   - Increment `state.grounding_retry_count`
   - If retry count < 3: regenerate with corrective system message ("the prior draft claimed X but blackboard has no evidence; re-emit honestly without that claim")
   - If retry count ≥ 3: replace draft with fixed fallback ("Something didn't go as expected, sir.")

State additions (extends `JarvisState`):
- `grounding_retry_count: int` (default 0; reset per turn)
- `grounding_rejected_claims: list[str]` (audit trail for telemetry)

### 5.4 Speculative prefetch — extension to `dispatch.py`

Behavior:
1. After `classify_node` returns, check `state.route == "TASK"` and `state.route_confidence >= SPEC_PREFETCH_THRESHOLD` (configurable, default 0.7)
2. If both true: in parallel with the next graph node, dispatch a "speculative" version of the tool call:
   - Use a small LLM (Groq llama-3.1-8b-instant) to predict the most likely `transfer_to_*` for this query
   - Fire the handoff to the specialist via the same path AgentSession would take
   - Mark the dispatch with `speculative=True` so the specialist can run lighter validation
3. While the speculative dispatch runs, the main graph continues normally:
   - Filler emitted ("On it, sir.")
   - Real `task_dispatch_node` runs, may produce same or different tool_call
4. Reconciliation:
   - If real tool_call matches speculative: use the cached result, skip re-dispatch (saves 1-2s)
   - If real tool_call differs OR speculative failed: discard speculative result, dispatch the real one

Telemetry: every speculative attempt logs hit/miss to `turn_telemetry.db` for tuning the threshold over time.

Cap: only one speculative dispatch per turn (no concurrent prefetches). Disabled when `state.failed_providers` is non-empty (don't double-down during recovery).

### 5.5 Wiring — `JarvisSupervisorGraphLLM` (v2 path)

The existing v1 adapter handles `JARVIS_LANGGRAPH_SUPERVISOR=1`. We add a parallel v2 adapter:

- New env: `JARVIS_BLACKBOARD=1` (independent of v1's flag; can be enabled separately)
- Both flags can be on simultaneously: v2 wraps v1's graph, adding the grounding gate + speculative prefetch as additional nodes
- New helper `_pick_supervisor_llm_v2()` in `jarvis_agent.py`, called from the existing `_pick_supervisor_llm` after it picks the v1 adapter

## 6. Data flow — TASK turn happy path

```
User: "open YouTube"
  │
  ▼
STT: "open YouTube"
  │
  ▼
classify_node ─► route=TASK, conf=0.95, intent.write→blackboard
  │              │
  │              └── parallel ──► speculative_prefetch
  │                                 (predicts transfer_to_browser, fires it)
  │
  ▼
filler "On it, sir." (TTS speaking) ─── while specialist runs in parallel
  │
  ▼
task_dispatch_node ─► transfer_to_browser tool_call
  │
  ▼
specialist runs (or speculative result ready)
  │
  ▼
specialist's task_done ─► tools.write→blackboard ("tab opened: youtube.com")
  │
  ▼
vision_tap (background) ─► screen.write→blackboard ("active app: Chrome, tab: YouTube")
  │
  ▼
LLM draft: "I've opened YouTube, sir."
  │
  ▼
grounding_gate ─► finds evidence in blackboard.tools ─► RELEASE
  │
  ▼
TTS speaks: "There you are, sir."
```

If `speculative_prefetch` was wrong (e.g. user said "open YouTube" but the prediction was `transfer_to_desktop`): discard speculative, run normally. Net: 0-200 ms penalty, no false action.

If grounding_gate rejects (LLM hallucinates "I've also opened Gmail" — no evidence): regenerate with corrective context, max 3 retries, fallback to honest message.

## 7. Error handling

| Failure | Behavior |
|---------|----------|
| Blackboard (Redis) unreachable | v2 path falls through to v1 supervisor (state-shape gating only); log warning |
| Vision API down or rate-limited | `vision_tap` logs warning; supervisor responds "I can't see your screen right now, sir" if asked |
| Vision returns Chinese (Moonshot default) | System prompt enforces English; if it slips through, parser drops the field |
| Speculative prefetch wrong | Discard, run normal dispatch; net 0-200 ms penalty |
| Grounding gate rejects ≥ 3 times | Replace draft with "Something didn't go as expected, sir" — fixed honest fallback |
| Screenshot capture fails (Wayland security context) | Disable `vision_tap` for the session; log; rest of system continues |
| User in a privacy-sensitive app (banking, password manager) | `vision_tap` skips capture per `vision-paused-apps.txt`; supervisor responds "I'm not looking at your screen right now, sir" |
| Network split-brain (Groq up, Moonshot down) | Vision-grounded claims unavailable; tool-result-grounded claims still work |

## 8. Testing strategy

### 8.1 Unit tests (~70)
- Blackboard schema validation (Pydantic round-trip)
- Blackboard client read/write idempotency
- TTL expiry behavior
- Vision parser robustness (malformed JSON, Chinese leak, empty response)
- Grounding gate tokenization (15+ past-tense markers)
- Grounding gate evidence matching (positive: claim+evidence; negative: claim alone)
- Grounding gate retry budget enforcement
- Speculative prefetch threshold gating
- Speculative prefetch reconciliation logic (cache hit, cache miss, dispatch differs)
- Vision tap throttling (rate-limit, screen-change debounce, paused-app skip)

### 8.2 Integration tests (~20)
- End-to-end TASK turn through v2 path (mocked Redis, mocked specialist, mocked vision)
- Grounding gate rejects a hallucinated claim and recovers
- Grounding gate hits 3-retry limit, emits fallback
- Vision-coreference: query "what's on my screen" → blackboard.screen → response
- Speculative prefetch hit (predicted == real) saves time
- Speculative prefetch miss (predicted ≠ real) recovers without false action
- Both flags off: v0 path (no behavior change)
- v1 only: v1 behavior preserved
- v2 only: v2 features active, v1 graph used

### 8.3 Live soak (manual)
- 5 base turns from v1 soak script (must still pass)
- 5 vision-coreference turns:
  - "What's on my screen?"
  - "Close that tab."
  - "Open another like that one."
  - "What was the last email I had open?"
  - "Read the page aloud."
- 5 speculative-prefetch turns (verify <2s perceived TTFW):
  - "Open YouTube."
  - "Search Google for the weather."
  - "Open a new tab."
  - "Take a screenshot."
  - "Switch to my email."

## 9. Migration & rollback

**Phase 1 — code lands behind flag.** `JARVIS_BLACKBOARD=1` (default off). v1 still works untouched.

**Phase 2 — soak.** Flag on for 24-48 hours. Telemetry tracks:
- Confab-detector drops (target: 0)
- Grounding-gate rejects per turn (expected: > 0; we want this to catch lies)
- Grounding-gate exhausted-retry events (expected: ≤ 1 per 100 turns)
- Speculative prefetch hit rate (target: > 60%)
- Median TTFW on TASK turns (target: < 2 s)
- Vision API monthly cost (track; revisit throttle if > $20/month)

**Phase 3 — flip default.** If telemetry passes, add `Environment=JARVIS_BLACKBOARD=1` to the systemd unit.

**Phase 4 — strip dead branches.** Remove the v1-only fallback path AFTER 30 days of v2-default stability. Until then, keep v1 alive as the safety net.

**Rollback (any phase):** `unset JARVIS_BLACKBOARD; systemctl restart jarvis-voice-agent`. ~3 seconds. v1 supervisor still active.

## 10. Success criteria

- ✅ Zero `[confab-detector] dropping` per 100 normal turns (already met with v1; v2 must not regress)
- ✅ Vision-coreference turns ("close that tab") work on first attempt ≥ 90% of test set
- ✅ Median TTFW < 2 s on TASK-with-handoff turns (currently ~3-4s)
- ✅ Speculative prefetch hit rate > 60% on labeled test set
- ✅ Grounding gate rejects every synthetic-hallucination test case (forced LLM lying via prompt injection in the test)
- ✅ All v1 tests still pass (76 + the v2 test additions)
- ✅ ≥ 70 new unit + integration tests passing
- ✅ Hand-tested: 15 turns (5 base + 5 vision + 5 prefetch) with no silent drops, no lies, no false success claims

## 11. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Vision cost runs away ($-control) | Med | Med | Hard 30 s ceiling + screen-change debounce + paused-app skip; daily cost telemetry; alert at $1/day |
| Vision LLM returns Chinese, English garbage, or refusal | Med | Low | System prompt enforces English; parser drops malformed fields; fallback to "I can't see clearly right now" |
| Grounding gate over-rejects, fallback fires too often | Med | High | Telemetry-driven tuning of past-tense regex; 3-retry budget; soak gate metric "fallback fires < 1 per 100 turns" |
| Speculative prefetch creates duplicate side effects | Low | High | Speculative dispatches are marked `speculative=True`; specialist treats them as dry-run for destructive ops (browser navigation OK; sending email NOT OK) — list of "speculative-safe" tools is explicit |
| Wayland security blocks screenshots | Low | Low | Detect at startup, disable `vision_tap`; supervisor degrades gracefully |
| Redis blackboard becomes a single point of failure | Low | High | Already a dependency for the hub; agent falls through to v1 when Redis is down |
| LangGraph version churn breaks v2 nodes | Low | Med | Pin minor version; dedicated v2 test suite catches incompatibilities |

## 12. File layout

**New (~2000 LOC):**
- `src/voice-agent/blackboard/`
  - `__init__.py`
  - `client.py`
  - `schema.py`
  - `gates.py`
- `src/voice-agent/vision_tap.py`
- `src/voice-agent/supervisor_graph/grounding_gate.py`
- `src/voice-agent/supervisor_graph/speculative.py`
- Tests in `src/voice-agent/tests/`:
  - `test_blackboard_client.py`
  - `test_blackboard_schema.py`
  - `test_blackboard_gates.py`
  - `test_vision_tap.py`
  - `test_vision_parser.py`
  - `test_grounding_gate.py`
  - `test_grounding_gate_retry.py`
  - `test_speculative_prefetch.py`
  - `test_v2_assembly.py`
  - `test_v2_feature_flag.py`
- `~/.config/systemd/user/jarvis-vision-tap.service` (new unit)

**Modified (small touch points):**
- `src/voice-agent/supervisor_graph/state.py` — add `grounding_retry_count`, `grounding_rejected_claims`, `speculative_dispatch_id`
- `src/voice-agent/supervisor_graph/graph.py` — wire grounding_gate after speak_gate; wire speculative branch after classify_node
- `src/voice-agent/supervisor_graph/llm_adapter.py` — read v2 flag, layer grounding-gate output into the chunk emission path
- `src/voice-agent/jarvis_agent.py` — `_pick_supervisor_llm_v2()` helper; called from existing `_pick_supervisor_llm`
- `src/voice-agent/specialists/agent.py` — task_done writes ToolResult to blackboard
- `pyproject.toml` / `requirements.txt` — pin `redis>=5.0` (likely already present via hub), `pydantic>=2.0`

**Removed (after Phase 4 only):**
- v1 fallback branches in `_pick_supervisor_llm` — only after 30 days of v2-default stability

---

## Self-review (per brainstorming skill)

- [x] **Placeholder scan** — no TBD/TODO/incomplete sections in spec body. "Phase 4" being deferred is intentional and explicit, not a gap.
- [x] **Internal consistency** — channel names match across §5.1, §5.3, §5.4, §6 (`screen.*`, `tools.*`, `intents.*`); `JARVIS_BLACKBOARD` flag named consistently across §4, §5.5, §9; speculative prefetch threshold (`SPEC_PREFETCH_THRESHOLD`, default 0.7) cited consistently.
- [x] **Scope check** — single subsystem (the supervisor); audio plumbing, hub, bridge, browser extension, specialists' internal logic, CLI proxy, model registry all stay untouched (NG3, NG4). One implementation plan can cover this.
- [x] **Ambiguity check** — "past-tense success patterns" enumerated in §5.3 (15+ verbs); "speculative-safe tools" explicitly defined in risks §11; flag interaction (v1 + v2 simultaneously) clarified in §5.5; vision throttling rules ALL specified in §5.2 (30s ceiling, 1s debounce, paused-app skip, /vision/now bypass).

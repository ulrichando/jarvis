# JARVIS supervisor — LangGraph state-shape rebuild

**Date:** 2026-05-04
**Status:** Design (approved scope C — full enterprise rebuild)
**Topology:** A — LangGraph-as-LLM behind the existing LiveKit AgentSession
**Companion plan:** *will be written next via superpowers:writing-plans*

---

## 1. Problem (in one sentence)

The current supervisor agent emits text completion claims while specialists
are still running, mis-routes TASK turns to BANTER chitchat, lets the
fallback LLM confabulate success when the primary fails, and relies on
prompt instructions for invariants (which the LLM ignores ~30 % of the
time) — all of which produce the user-facing symptom *"I can't have a
normal conversation."*

## 2. Goals & non-goals

### Goals
- **G1 — Eliminate completion-claim lies structurally.** Speaking is
  gated on observable tool results in shared state; no prompt rule
  required.
- **G2 — Eliminate first-turn miss residue.** Routing is deterministic
  for verb-initial commands; ambiguous turns escalate to a strict-JSON
  classifier.
- **G3 — Eliminate cross-stream lies.** When the primary LLM fails and
  the fallback runs, the fallback sees the same canonical state — it
  cannot speculate about completion that hasn't happened.
- **G4 — Eliminate tool-call malformation at the source.** Force
  `tool_choice="required"` on TASK turns, swap to a tool-tuned model
  variant, leave the existing sanitizer as belt-and-braces.
- **G5 — Specialists must do real work or report failure honestly.**
  The existing programmatic `task_done` gate stays.
- **G6 — Maintain or improve TTFW** (time to first spoken word).

### Non-goals
- **NG1 — Replace the audio plumbing.** STT, TTS, AEC, room state,
  watchdog, mic capture, playback, AcousticTap, telemetry — all stay
  untouched. The bug is in orchestration, not in audio.
- **NG2 — Replace specialist registry.** `RegistrySpecialist` and the
  `transfer_to_*` tool family stay. Specialists become invocable from
  graph nodes; their internal logic doesn't change.
- **NG3 — Replace the bridge / hub / desktop / browser-extension
  integrations.** They observe events from the AgentSession; LangGraph
  emits the same events.
- **NG4 — Switch primary providers.** Stay on Groq; DeepSeek is still
  the fallback. Just stop letting either lie.

## 3. Architecture

### 3.1 Topology

```
┌────────────────────────────────────────────────────────────┐
│ LiveKit AgentSession (unchanged)                           │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ STT (Groq Whisper)                                  │   │
│  │  ↓                                                  │   │
│  │ JarvisAgent.on_user_turn_completed                  │   │
│  │  ├─ existing pre-LLM gates: bare-vocative,          │   │
│  │  │  garbage filter, silent-mode, quiet-hours        │   │
│  │  └─ if pass-through:                                │   │
│  │     ↓                                               │   │
│  │ JarvisSupervisorGraphLLM.chat(...)                  │   │  ← NEW
│  │  └── compiled LangGraph runs the turn               │   │
│  │      ├─ classify route                              │   │
│  │      ├─ dispatch (tool / specialist / speak)        │   │
│  │      ├─ tool execution (ToolNode)                   │   │
│  │      ├─ state-shape gate                            │   │
│  │      └─ emit chunks back as LLMStream               │   │
│  │  ↓                                                  │   │
│  │ TTS (Groq Orpheus)                                  │   │
│  └─────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
```

### 3.2 Graph state

```python
class JarvisState(TypedDict):
    # Conversation
    messages: Annotated[list[BaseMessage], add_messages]
    user_query: str
    audio_meta: dict        # emotion / prosody from AcousticTap

    # Routing
    route: Literal["BANTER", "TASK", "REASONING", "EMOTIONAL", "WAITING"]
    route_confidence: float

    # State-shape gate (the structural cure)
    pending_tool_calls: list[str]          # tool_call_ids without ToolMessage
    pending_specialist: Optional[str]      # name of in-flight specialist
    last_tool_result: Optional[str]
    handoff_filler_voiced: bool            # "On it, sir." emitted exactly once

    # Recovery
    failed_providers: list[str]            # ["groq"] etc — affects fallback choice
    retry_attempt: int
```

The two load-bearing channels: `pending_tool_calls` and
`pending_specialist`. The terminal `speak` node refuses to fire while
either is non-empty.

### 3.3 Node graph

```
START
  ↓
classify
  ├─ verb_initial_match? → route = TASK (skip LLM)
  └─ else → strict_json_router_llm → set route + confidence
  ↓
[branch on route]
  │
  ├─ BANTER → banter_speak (no tools, content only) → speak_gate → END
  ├─ EMOTIONAL → emotional_speak → speak_gate → END
  ├─ REASONING → reasoning_speak (qwen3-32b, optional tools) → speak_gate
  │   └─ if tool_calls emitted → tool_node → reflect → speak_gate
  ├─ TASK → task_dispatch (tool_choice="required")
  │   ├─ if transfer_to_X → set pending_specialist
  │   │     → emit filler chunk ("One moment, sir.")
  │   │     → specialist_subgraph (synchronous run)
  │   │     → on completion: clear pending_specialist
  │   │     → speak_gate
  │   └─ else (direct tool) → tool_node → reflect → speak_gate
  └─ WAITING (pending_specialist set on entry) → noop_acknowledge → END
       (rare; happens if user speaks while specialist mid-flight)
```

### 3.4 Critical nodes — contracts

**`classify`**
- Input: `state["user_query"]` + `state["messages"]` (last 4)
- Output: `route`, `route_confidence`
- Implementation: regex first; LLM fallback uses Groq llama-3.3-70b
  with `response_format: json_schema, strict: true`, schema
  `{"route": enum, "confidence": number}`. No content allowed.

**`task_dispatch`**
- Input: `state["user_query"]` + tools
- Output: `messages.append(AIMessage(tool_calls=[...]))`
- Implementation: Groq llama-3-groq-8b-tool-use (or llama-3.3-70b for
  complex tasks). `tool_choice="required"`. The model **cannot** emit
  text content.
- On emit: set `pending_tool_calls = [tc.id for tc in tool_calls]`.
- Specialist tool calls (`transfer_to_*`) set `pending_specialist`
  instead of going through ToolNode (specialists run as sub-process).

**`tool_node`** (LangGraph's prebuilt `ToolNode`)
- Input: latest AIMessage's tool_calls
- Output: ToolMessage per tool_call, with matching `tool_call_id`
- Each ToolMessage clears its id from `pending_tool_calls`.

**`speak_gate`** — the structural cure
- Precondition: `pending_tool_calls == [] and pending_specialist is None`
- If satisfied: emit content chunks → END
- If not: route back to `tool_node` (or wait_for_specialist)
- This node is a `Command` that conditionally routes; never speaks
  without observable proof of completion.

**`specialist_subgraph`**
- Wraps the existing `RegistrySpecialist` + its `task_done` gate.
- Sub-graph state: `{messages, tools, attempts, terminal}`.
- On completion: bubbles up `last_tool_result` and clears
  `pending_specialist`.
- If specialist's `task_done` is REFUSED (no real tool ran), sub-graph
  loops with a corrective ToolMessage; max 3 attempts before bubbling
  failure.

**`reflect`** (small node after tool execution)
- Reviews ToolMessage(s); decides whether the response is sufficient
  or another tool round is needed (the orchestrator-worker pattern).
- Default: 1 round is enough for voice (latency budget).
- Bypassable via env for the "agentic loop" experiment.

### 3.5 Filler-phrase strategy ("don't lie, don't go silent")

When `task_dispatch` emits a `transfer_to_*` tool call, the graph
**immediately** emits a non-committal filler chunk like:

- "One moment, sir."
- "On it."
- "Let me check."

This bridges the latency between handoff and specialist completion
**without claiming success**. The Sierra/Hamming/Vapi research
unanimously identifies non-committal fillers as the right pattern.

The `handoff_filler_voiced` flag prevents double-speaking; once the
specialist completes, its `task_done` summary is the next utterance
the user hears.

### 3.6 Fallback handling — the cross-stream lie cure

LangGraph's checkpointer is the source of truth. When Groq fails on
`task_dispatch`:

1. Catch the error in the graph node.
2. Strip the partial AIMessage from state (or never appended it).
3. Re-issue the same node with `model = deepseek` and
   `tool_choice="required"`.
4. DeepSeek sees the same `pending_tool_calls = []` (clean state) and
   the same user query; it cannot confabulate completion because
   **no completion has been claimed yet anywhere in state**.

This is the structural fix for failure mode #5 (supervisor lies after
fallback). The current LiveKit `FallbackAdapter` replays partial
assistant content to the next provider; we bypass that.

### 3.7 Checkpointing

`SqliteSaver` in `~/.local/share/jarvis/supervisor_graph.db`. Single-
user dev rig — no need for Postgres. The checkpoint enables:

- Resume after agent restart (e.g. system-sleep wake hook fires)
- Replay-on-failure (the failure handler re-issues a node from a
  checkpoint, never from a partial state)
- Time-travel debug: dump checkpoints, replay specific turns

## 4. Components & files

**New (~1500 LOC total):**
- `src/voice-agent/supervisor_graph/` (package)
  - `__init__.py`
  - `state.py` — `JarvisState` TypedDict
  - `graph.py` — `build_graph()` returning compiled `StateGraph`
  - `nodes/__init__.py`
  - `nodes/classify.py` — verb-initial regex + strict-JSON LLM
  - `nodes/dispatch.py` — task_dispatch + banter_speak + emotional_speak
  - `nodes/specialist.py` — specialist sub-graph + filler emission
  - `nodes/speak_gate.py` — the state-shape gate
  - `nodes/reflect.py` — post-tool reflection
  - `llm_adapter.py` — `JarvisSupervisorGraphLLM(LLM)` wrapping
    `compile()`'d graph behind the LiveKit LLM interface
  - `tools.py` — re-export specialist transfer tools + delegate
  - `checkpoint.py` — SqliteSaver setup
- `src/voice-agent/router_classifier.py` — verb-initial regex + LLM
  classifier (extracted from existing turn_graph.py)
- `src/voice-agent/tests/test_supervisor_graph.py` — unit + integration
  tests
- `src/voice-agent/tests/test_speak_gate.py` — focused gate tests
- `src/voice-agent/tests/test_router_classifier.py` — routing accuracy

**Modified:**
- `src/voice-agent/jarvis_agent.py` — feature-flag the new supervisor
  in `entrypoint()`. `JARVIS_LANGGRAPH_SUPERVISOR=1` switches.
- `src/voice-agent/specialists/agent.py` — keep `task_done` gate.
  Specialists become invocable from graph nodes (small adapter).
- `pyproject.toml` / `requirements.txt` — add `langgraph`,
  `langchain-core`, `langchain-groq`.

**Removed (after verification window):**
- BANTER fast-path classifier in `turn_graph.py` (replaced by graph
  routing node — we don't carry two routers).
- `_BANTER_FAST_PATH_RE` once verified the regex pre-classifier covers
  the cases.

## 5. Migration & rollback

**Phase 1 — feature flag.** New supervisor lives behind
`JARVIS_LANGGRAPH_SUPERVISOR=1`. Default off. Old supervisor untouched.

**Phase 2 — soak.** Flag flipped on for the dev rig. 24-48h soak with
the existing telemetry (`turn_telemetry.db`). Compare:

- Confab-detector drop rate (target: 0)
- TTFW p50 / p95
- Routing-misroute rate (manual labeling of 50 turns)
- Specialist `task_done REFUSED` rate
- Fallback (DeepSeek) invocation rate

**Phase 3 — flip default.** If telemetry is clean, flip
`JARVIS_LANGGRAPH_SUPERVISOR=1` as default in the systemd unit's
`Environment=`. Keep the flag for emergency rollback.

**Phase 4 — strip old code.** Once a month of stable operation, remove
the dead branches. Update memory entries.

**Rollback path:** unset the flag, restart the agent service. ~3
seconds to revert. No DB schema changes; specialist registry intact.

## 6. Tests

### Unit
- Routing accuracy: 100+ labeled utterances against `classify` node
  alone. Target: ≥95% on labeled set.
- `speak_gate` refuses while `pending_tool_calls` non-empty.
- `speak_gate` releases when ToolMessage clears all pending.
- Specialist sub-graph respects existing `task_done` gate.
- Fallback handler strips partial assistant content before retry.

### Integration
- Synthetic conversation: BANTER chitchat → quick reply, no tool calls.
- Synthetic conversation: TASK with successful specialist → handoff
  filler voiced once, then specialist's task_done summary.
- Synthetic conversation: TASK with specialist `task_done` REFUSED →
  loop with corrective message → eventual real tool call → success.
- Provider failure: simulate Groq `Failed to call a function`,
  verify graph retries with DeepSeek under `tool_choice="required"`,
  verify zero confab-detector drops in the resulting messages.

### Live (post-flip)
- Daily soak with `bin/jarvis-soak-rescore.sh` running existing rubric.
- Manual "lying-supervisor" probe: ask 5 actions in 1 turn each, count
  any `[confab-detector] dropping` lines in logs. Target: 0.

## 7. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| LangGraph + LiveKit integration surprises | Med | High | Feature flag, soak window, comprehensive tests before flip |
| Latency regression (more nodes per turn) | Med | Med | Per-node fast paths (regex-only routing for ~80% of TASK), measure TTFW continuously |
| LangGraph version churn (0.6+ breaking changes) | Low | Med | Pin minor version, regenerate lock file, dedicated test suite |
| Specialist sub-graph deadlock | Low | High | Per-node timeouts; fallback to old supervisor on graph error |
| Checkpoint DB grows unbounded | Low | Low | Daily prune of checkpoints older than 7 days |
| `tool_choice="required"` on Groq breaks chitchat-with-tools edge case | Low | Med | Only force on TASK route; BANTER/REASONING/EMOTIONAL stay free-form |

## 8. Success criteria (the "perfect" bar)

- ✅ Zero `[confab-detector] dropping` lines per 100 normal turns
- ✅ ≥95% routing accuracy on labeled dev set
- ✅ TTFW p50 ≤ 1.5 s on BANTER (vs ~1.2 s today)
- ✅ TTFW p50 ≤ 3.0 s on TASK with specialist handoff
- ✅ Zero "first-turn missed" reports for 1 week of dev use
- ✅ All existing tests pass
- ✅ ≥30 new tests passing (specifically for the new supervisor)
- ✅ Hand-tested: 20 mixed turns (chitchat, search, browser ops, tool
  failures) with no silent drops, no lies, no false success claims

---

## Self-review (per brainstorming skill — fix inline)

- [x] **Placeholder scan** — no TBDs / TODOs in the spec body. The
      "future work" / "after verification" notes are intentional, not
      gaps.
- [x] **Internal consistency** — feature flag named consistently
      (`JARVIS_LANGGRAPH_SUPERVISOR`); state field names align between
      §3.2 and §3.4 (`pending_tool_calls`, `pending_specialist`,
      `last_tool_result`); architecture diagram in §3.1 matches node
      graph in §3.3.
- [x] **Scope check** — single subsystem (supervisor); the audio
      pipeline, hub, bridge, browser extension, specialists' internal
      logic, and CLI proxy all stay untouched. One implementation plan
      can cover this.
- [x] **Ambiguity check** — `tool_choice="required"` is explicit
      (Groq parameter, not Anthropic-style `{"type":"any"}`); fallback
      "strip partial assistant content" is explicit (don't replay).
      Filler-phrase rule is explicit (emit exactly once per handoff,
      flagged in state).

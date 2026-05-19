# JARVIS Confab Defense-in-Depth — Design Spec

**Date:** 2026-05-19
**Status:** Draft (awaiting user review before writing-plans handoff)
**Authors:** Ulrich (decisions), Claude (drafting)
**Related:**
- Live failure capture: `~/.local/share/jarvis/logs/voice-agent.log` 2026-05-19T02:23:32 (turn 02:24:18, route=EMOTIONAL, subagent=desktop)
- Prior subagent gate work: [subagents/agent.py](../../src/voice-agent/subagents/agent.py), [CLAUDE.md §Active design decisions]
- Confab detector v1: [confab_detector.py](../../src/voice-agent/confab_detector.py)
- Pycall sanitizer: [sanitizers/pycall.py](../../src/voice-agent/sanitizers/pycall.py)

---

## 1. Problem statement

On 2026-05-19 at 02:24:18 UTC the voice telemetry recorded a turn where:
- User said: `"Okay."` (one word, classified EMOTIONAL route, routed to Claude Haiku 4.5)
- JARVIS voiced: `"I've opened Chrome for you. Handing back to the supervisor now."`
- Actual state: **Chrome was not running** (`pgrep -fa google-chrome` returned empty), and the launch subprocess's expected log file `/tmp/jarvis-launch-google-chrome-<ts>.log` did not exist when checked.

Trace of the failure (from voice-agent log + telemetry):

```
02:23:32  INFO    launch_app → setsid -f /usr/bin/google-chrome --profile-directory="Default" --new-window
02:23:33  WARN    [subagent:desktop] task_done REFUSED — no real tool call this handoff (items_since=1)
02:23:34  WARN    [pycall] leak suppressed (multi-chunk, pycall): 'task_done("'
02:24:13  WARN    [subagent:desktop] task_done REFUSED — no real tool call this handoff (items_since=1)
02:24:18  INFO    [tts] Orpheus rendered: "I've opened Chrome for you. Handing back..."
02:24:21  INFO    [tts] Orpheus rendered: "I already launched Chrome successfully i…"
```

### Three intertwined failures

**F1 — Tool-result loss in chat_ctx.** The desktop subagent's `launch_app` invocation produced an INFO log (the function ran) but its `FunctionCallOutput` did not land in the subagent's `chat_ctx.items` as a structured `tool_result`. The tool-call gate at [subagents/agent.py::task_done](../../src/voice-agent/subagents/agent.py) walks chat_ctx since handoff start; it saw `items_since=1` and `no real tool`, and refused `task_done` twice. The pycall sanitizer simultaneously caught `task_done("` being emitted as voiced text — i.e., the subagent LLM was emitting tool-call shapes as plain content text instead of through the structured `tool_calls` field. **Root cause:** LiveKit Agents v1.5.9 only writes `FunctionCallOutput` to chat_ctx via the structured `FunctionCall` path ([`voice/agent_activity.py:2834`](../../src/voice-agent/.venv/lib/python3.13/site-packages/livekit/agents/voice/agent_activity.py), [`voice/generation.py:746`](../../src/voice-agent/.venv/lib/python3.13/site-packages/livekit/agents/voice/generation.py)). When pycall rescues a text-shaped call, the framework's writeback path is bypassed and chat_ctx is left without the result.

**F2 — Chat_ctx pollution.** The session was seeded with 12 prior turns at startup (`[recall] seeded chat_ctx with 12 prior turns`). Those turns were from earlier sessions across the day, appended as raw `role:user` / `role:assistant` ChatMessages with no staleness markers. Haiku saw "Okay" with stale Chrome-related context and inferred the user was confirming an unresolved open-Chrome request — generating a hallucinated `transfer_to_desktop("open Chrome")` handoff.

**F3 — Confab detector permissive.** The current [confab_detector.py::has_recent_tool_evidence](../../src/voice-agent/confab_detector.py) grants evidence credit when a `transfer_to_*` appears in the last 10 messages, because the supervisor's chat_ctx doesn't see the subagent's internal tool calls and "the handoff alone proves the subagent had a chance to do work". This rule lets the supervisor voice "I've opened Chrome for you" with confidence even when the subagent's gate refused its `task_done` and no `launch_app` result lives in any chat_ctx.

The Chrome turn would have been blocked by **any one** of these layers had they been correct.

---

## 2. Goals

- **G1.** When `launch_app` (or any other tool) fires through the subagent path, the resulting `FunctionCallOutput` MUST land in `chat_ctx.items` as a structured tool_result with the same `call_id` as the originating call. The subagent gate sees `items_since=2, real_tool=<name>` and the supervisor sees the actual result.
- **G2.** When the supervisor is about to voice a success claim, the confab detector requires CORROBORATED evidence (a real `tool_result` whose output indicates success, OR a programmatic state check). A bare `transfer_to_*` no longer suffices.
- **G3.** When the supervisor receives chat_ctx recall at session start, recalled turns are wrapped in an `Instructions` content block with explicit `[STALE]` framing and an age filter — Haiku cannot treat 4-hour-old turns as live conversation.
- **G4.** Telemetry exposes `confab_check_state` per turn so the operator can monitor the fix's effectiveness over time and alert on regression.
- **G5.** Each layer (F1→G1, F3→G2, F2→G3) is independently deployable and tested. Disabling any single layer via env var preserves the others.

## 3. Non-goals

- **NG1.** Replacing the supervisor LLM. Haiku 4.5 is correct for the voice loop; the bug is in chat_ctx + framework wiring, not model selection.
- **NG2.** Adding always-on per-turn screenshot verification. Cost prohibitive and overkill. We add `pgrep`-style programmatic checks only for `launch_app`-class claims.
- **NG3.** Rewriting the subagent tool gate. Its refusal at 02:23:33 was correct; the gap is upstream (tool result didn't land in chat_ctx).
- **NG4.** Cross-session memory consolidation. Out of scope; existing memory consolidator owns long-term canonicalization. This spec addresses session-start recall hygiene only.
- **NG5.** Wholesale removal of the pycall sanitizer. It still catches genuine tool-leak protocol shapes (`task_done(`, `<function>`, etc.). G1's synthesis path is added alongside, not replacing.

---

## 4. Architecture

Three independently-deployable layers in a defense-in-depth stack. Each catches the Chrome failure on its own; together they cover each other's edge cases.

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 3 (B-lite) — Chat_ctx hygiene                         │
│   Prevent: hallucinated handoff from stale context          │
│   How: Instructions block + age filter + STALE marker       │
│   Fires: at session boot (recall seed time)                 │
└────────────────────────────┬────────────────────────────────┘
                             │ if hallucinated handoff still fires →
                             ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 1 (D) — Tool-result wiring                            │
│   Ensure: every tool call lands as FunctionCall+Output pair │
│   How: synthesize pair when pycall rescues text-shaped call │
│   Fires: at sanitizer time (provider stream parsing)        │
└────────────────────────────┬────────────────────────────────┘
                             │ if result still missing →
                             ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 2 (A) — Confab detector + post-action verification    │
│   Refuse: voiced success claim without corroborated evidence│
│   How: tighten evidence rule; add pgrep verify for launches │
│   Fires: at supervisor reply time (per-turn write)          │
└─────────────────────────────────────────────────────────────┘
```

**Composability:** Each layer is gated by its own env var. `JARVIS_RECALL_MAX_AGE_S=0` disables L3 (recall off entirely). `JARVIS_PYCALL_SYNTH_DISABLED=1` disables L1 synthesis (falls back to existing suppress-only behavior). `JARVIS_CONFAB_STRICT_DISABLED=1` reverts L2 to today's permissive rule. Defaults keep all three on.

---

## 5. Components

### 5.1 Layer 1 — Tool-result wiring

**Files modified:**
- [`src/voice-agent/sanitizers/pycall.py`](../../src/voice-agent/sanitizers/pycall.py) — when a text-shaped tool call is detected AND recognized as a known tool, append a synthetic `FunctionCall` + `FunctionCallOutput` pair to the active `chat_ctx`. Also log the raw assistant-turn shape (whether `content` had the call but `tool_calls` was empty) as a structured WARN.
- [`src/voice-agent/sanitizers/_leak_shapes.py`](../../src/voice-agent/sanitizers/_leak_shapes.py) — no change; existing dotted-form regex catches `module.tool()` patterns.
- *(possibly)* [`src/voice-agent/sanitizers/anthropic_strict_schema.py`](../../src/voice-agent/sanitizers/anthropic_strict_schema.py) — if the diagnostic log reveals that strict-schema is the reason tool calls come back as text, fix there too. This is the root-cause path; synthesis is the fallback.

**New module:** `src/voice-agent/sanitizers/_function_call_recovery.py` — pure-function helper that takes `(tool_name, raw_args_str, chat_ctx)` and returns a `(FunctionCall, FunctionCallOutput)` pair using `livekit.agents.llm.chat_context.FunctionCall` / `FunctionCallOutput` types. Generates a fresh `call_id` (UUID4) and runs the tool via the existing tool registry to obtain the output. If tool execution fails, the `FunctionCallOutput` carries the exception message — the gate still sees a `tool_result`, and the supervisor sees the failure.

**New env vars:**
- `JARVIS_PYCALL_SYNTH_DISABLED` (default `0`) — `1` skips synthesis, reverts to legacy suppress-only behavior.
- `JARVIS_PYCALL_SYNTH_LOG_RAW` (default `1`) — `1` logs the raw assistant-turn shape every time the synthesis path fires, for soak telemetry.

**Diagnostic surface:** Every synthesis adds two structured log lines:
```
[pycall.synth] text_call_rescued: tool=launch_app args_len=42 raw_content_len=120 tool_calls_present=False
[pycall.synth] chat_ctx_pair_inserted: call_id=fc-abc123 output_len=38 elapsed_ms=687
```

### 5.2 Layer 2 — Confab detector + post-action verification

**Files modified:**
- [`src/voice-agent/confab_detector.py`](../../src/voice-agent/confab_detector.py):
  - `has_recent_tool_evidence(chat_ctx, lookback=10)` — change rule:
    - REMOVE: `transfer_to_*` counts as evidence on its own.
    - KEEP: any structured `FunctionCallOutput` in the last 10 messages counts as evidence.
    - ADD: a `transfer_to_*` followed by a successful subagent `task_done` (i.e., the gate ALLOWED the bailout, didn't refuse) counts as evidence — the subagent's allowed `task_done` requires real tool evidence in its own chat_ctx, transitively.
  - New function: `verify_launched(binary_name, timeout_s=5)` — calls `pgrep -fa <binary_name>` and returns `True` if a match was created within `timeout_s` of the current time. Used for `launch_app`-class claims when the supervisor's chat_ctx doesn't have a direct `FunctionCallOutput`.

**Files modified:**
- [`src/voice-agent/prompts/supervisor.md`](../../src/voice-agent/prompts/supervisor.md):
  - New section `═══ POST-HANDOFF HONESTY ═══`: when `session._jarvis_last_handoff_refused` is true (read from the agent state), the supervisor MUST hedge in its reply. Banned: confident success claims ("I've opened…", "Done.", "<X> is now <Y>"). Required: a hedge form like "I tried but couldn't confirm — want me to check?" + offer to verify. Three concrete example pairs (WRONG / RIGHT) inline in the section.

**Files modified:**
- [`src/voice-agent/subagents/agent.py`](../../src/voice-agent/subagents/agent.py)::`task_done` gate:
  - On refusal: set `session._jarvis_last_handoff_refused = True` (single bool, cleared on next supervisor `task_done` acceptance, on `set_screen_share(start=False)`, and on any new structured `FunctionCallOutput` landing in the supervisor's chat_ctx).
  - Existing refusal-counter + bailout-allowlist logic untouched.

**New telemetry column:**
- `confab_check_state TEXT` on the `turns` table in `~/.local/share/jarvis/turn_telemetry.db`. Values: `evidence_ok | hedged_no_evidence | refused_handoff | stale_ctx_dropped | unchecked`. Written by `pipeline/turn_telemetry.py::log_turn` (new optional kwarg).

### 5.3 Layer 3 — Chat_ctx hygiene

**Files modified:**
- [`src/voice-agent/pipeline/chat_ctx.py`](../../src/voice-agent/pipeline/chat_ctx.py)::`seed_from_recall` (or equivalent recall function):
  - Filter recalled turns by age: keep only `ts_utc >= now - JARVIS_RECALL_MAX_AGE_S` (default 1800 s = 30 min).
  - Wrap kept turns in a single `Instructions` content block (LiveKit's `livekit.agents.llm.chat_context.ChatContent`'s `Instructions` shape, per [`chat_context.py:340`](../../src/voice-agent/.venv/lib/python3.13/site-packages/livekit/agents/llm/chat_context.py)) instead of separate `role:user` / `role:assistant` ChatMessages.
  - Header inside the Instructions block:
    ```
    [STALE PRIOR-SESSION CONTEXT — Do NOT treat as live conversation.
     Verify current user intent before acting on anything below.
     Recalled <N> turns from <session_id>, ages <min>-<max> minutes ago.]
    ```
  - Each turn formatted as `<memory ts="..." role="user|assistant" age="Xm">…</memory>` inside the block.

**Files modified:**
- [`src/voice-agent/prompts/supervisor.md`](../../src/voice-agent/prompts/supervisor.md):
  - New section `═══ STALE PRIOR-SESSION CONTEXT ═══`: the `<memory>` blocks inside the STALE Instructions are reference-only. Banned: inferring an active task, an unresolved request, or a pending confirmation from the stale block. Required: treat the FIRST user turn of the current session as fresh intent unless the user explicitly references prior history ("as I mentioned earlier…", "you said you'd…").

**New env vars:**
- `JARVIS_RECALL_MAX_AGE_S` (default `1800`) — recall window in seconds. `0` disables recall entirely.
- `JARVIS_RECALL_MAX_TURNS` (default `12`, unchanged) — upper bound on recalled turn count after the age filter.

### 5.4 Cross-layer

**Files modified:**
- [`src/voice-agent/pipeline/turn_telemetry.py`](../../src/voice-agent/pipeline/turn_telemetry.py):
  - Online migration: add `confab_check_state TEXT` column on `turns`.
  - `log_turn(...)` accepts new optional kwarg `confab_check_state: Optional[str] = None`.

**New script:** `bin/jarvis-confab-soak` — analogous to `bin/jarvis-haiku-soak`. Rolls up `confab_check_state` distribution over a given time window, flags any turn where supervisor voiced a success claim (`jarvis_text` matches `r'I\'?ve (opened|launched|started|done)'`) without a matching tool_result. Hard-fail if any silent confab is detected.

---

## 6. Data flow

### 6.1 Happy path — Chrome turn after fix

```
User: "Open Chrome"
  ↓
Supervisor (Haiku) emits: transfer_to_desktop("open Chrome")    [structured tool_call]
  ↓
Desktop subagent receives handoff. chat_ctx items_since=0.
  ↓
Subagent LLM emits: launch_app("google-chrome", "--profile-directory='Default' --new-window")
  ↓
[CASE A — structured tool_call path]
  LiveKit runs subprocess + pgrep at 600ms → "OK: launched 'google-chrome'"
  agent_activity.py appends FunctionCall + FunctionCallOutput to chat_ctx
  ↓
[CASE B — text-content path (the F1 bug pattern)]
  pycall.py detects the text-shape, recognizes launch_app as known tool
  _function_call_recovery synthesizes (FunctionCall, FunctionCallOutput) pair
  Executes launch_app via the tool registry → "OK: launched 'google-chrome'"
  chat_ctx.insert([fc, fco])
  ↓
[BOTH PATHS CONVERGE]
  Subagent LLM sees the OK result in its chat_ctx, calls task_done("Chrome opened.")
  Gate check: items_since=2 (call + result), real_tool=launch_app → ALLOW
  session._jarvis_last_handoff_refused = False
  ↓
Supervisor's next turn sees real FunctionCallOutput in chat_ctx
  AND flag=False  AND  verify_launched("google-chrome") returns True
  ↓
Voiced: "Chrome's open." (corroborated)
Telemetry: confab_check_state = "evidence_ok"
```

### 6.2 Failure path A — gate refuses task_done

```
... handoff happens, subagent LLM still emits text-shape ...
[Layer 1 synthesis path failed (e.g., tool registry rejected the args)]
  pycall logs [pycall.synth] failure, falls through to legacy suppress
  ↓
Subagent task_done refused (no real tool result)
  ↓
session._jarvis_last_handoff_refused = True
  ↓
Supervisor next turn: POST-HANDOFF HONESTY rule fires (Layer 2)
  ↓
Voiced: "I tried but couldn't confirm — want me to check?"  [hedged]
Telemetry: confab_check_state = "refused_handoff"
```

### 6.3 Failure path B — stale chat_ctx triggers hallucinated handoff (Layer 3 catches)

```
User: "Okay"  (one word, EMOTIONAL route)
  ↓
[Boot earlier]
  Recall seed: filter turns by ts_utc >= now - 1800s
  - 0 turns within window OR
  - 3 turns within window, wrapped in Instructions block with [STALE] header
  ↓
Haiku sees current chat_ctx with explicit STALE framing on the prior turns
  STALE handling rule (supervisor.md Layer 3): treat current input as fresh intent
  ↓
No hallucinated transfer_to_desktop fires.
  ↓
Voiced: "Understood." (no false handoff)
Telemetry: confab_check_state = "stale_ctx_dropped" (logged for soak observability)
```

### 6.4 Failure path C — Layer 1 synthesis succeeds but tool ACTUALLY fails

```
... launch_app synthesized + executed ...
launch_app subprocess starts but Chrome crashes at startup → "CRASHED: <stderr>"
FunctionCallOutput contains the CRASHED string
  ↓
Subagent LLM sees CRASHED, calls task_done("Chrome failed to start.")
Gate ALLOWS (real tool fired, result present)
  ↓
Supervisor sees the CRASHED result
  Also calls verify_launched("google-chrome") → False
  ↓
Voiced: "Chrome failed to start — want me to try a different way?" [honest]
Telemetry: confab_check_state = "evidence_ok" (the tool DID return, even if failure)
```

---

## 7. Error handling

| Failure | Layer | Behavior |
|---|---|---|
| `JARVIS_RECALL_MAX_AGE_S=0` set | L3 | Recall disabled entirely; chat_ctx starts empty. Fresh session every time. |
| `JARVIS_RECALL_MAX_AGE_S` parse error | L3 | Logged WARN; falls back to default 1800. |
| `JARVIS_PYCALL_SYNTH_DISABLED=1` | L1 | New synthesis path skipped; pycall falls back to existing suppress-only behavior. F1 returns. |
| `JARVIS_CONFAB_STRICT_DISABLED=1` | L2 | Confab detector reverts to today's permissive rule. F3 returns. |
| Synthesis: tool name not in registry | L1 | Synthesis aborts cleanly. Pycall logs `[pycall.synth] unknown_tool` WARN; falls through to suppress. Layer 2 catches on emission. |
| Synthesis: tool args invalid (JSON parse failure) | L1 | Synthesis aborts. WARN logged. Layer 2 catches on emission. |
| Synthesis: tool execution raises | L1 | FunctionCallOutput written with the exception text. The gate sees the result; supervisor sees the failure. No silent confab. |
| `verify_launched`: `pgrep` not installed | L2 | Returns `None` (unknown); detector falls back to chat_ctx-only evidence rule (more conservative than today, less than ideal). |
| `verify_launched`: timeout (5 s without match) | L2 | Returns `False`; supervisor hedges. |
| `last_handoff_refused` flag never cleared | L2 | Cleared on: (a) next successful supervisor `task_done` acceptance, (b) any new `FunctionCallOutput` in supervisor's chat_ctx, (c) `set_screen_share(start=False)`, (d) explicit `session.clear_handoff_state()` call on new session. |
| `Instructions` content type not supported by older LiveKit | L3 | Build-time check: if `chat_context.Instructions` not importable, fall back to a single `role:system` message with the same content. Same semantics, less framework integration. |
| Supervisor LLM ignores the STALE rule and still hallucinates | L3 | Layer 1 + Layer 2 catch downstream. Telemetry shows `refused_handoff`. |
| Subagent's LLM gets stuck in a refusal loop | L2 | Existing 3-strike force-bail in subagent gate fires unchanged. After 3 refusals, gate force-allows "Cannot accomplish — handing back to supervisor"; supervisor's POST-HANDOFF rule still hedges. |
| Telemetry migration fails (legacy DB without the column) | L2 | Migration wrapped in try/except `sqlite3.OperationalError`; existing turn writes proceed without the new kwarg. |
| Cold start, no prior session, no recall | L3 | `chat_ctx` starts with system prompt only. No STALE block. Layer 3 is a no-op. |

---

## 8. Acceptance criteria

A1. After this change, the exact 02:23:32 / 02:24:18 failure pattern (one-word "Okay" → hallucinated Chrome handoff → voiced "I've opened Chrome" with no tool) cannot be reproduced from a fresh boot.

A2. When the subagent LLM emits a tool call as text content (the F1 bug), pycall's synthesis path inserts a `FunctionCall` + matching `FunctionCallOutput` into the active `chat_ctx`. The subagent gate at `task_done` sees `items_since ≥ 2` with a non-`task_done` tool in the trail and allows the bailout.

A3. The confab detector rejects evidence credit for bare `transfer_to_*` calls. The supervisor's "I've opened Chrome" voiced text is replaced by the hedge form when `session._jarvis_last_handoff_refused == True`.

A4. The 12-turn recall seed at session boot uses an age filter (`JARVIS_RECALL_MAX_AGE_S`, default 1800 s) AND wraps recalled turns in a single LiveKit `Instructions` content block with a `[STALE PRIOR-SESSION CONTEXT]` header. Recalled turns no longer appear as `role:user` / `role:assistant` ChatMessages.

A5. New telemetry column `confab_check_state` (`evidence_ok` | `hedged_no_evidence` | `refused_handoff` | `stale_ctx_dropped` | `unchecked`) is queryable via SQLite on every turn written post-fix.

A6. New `bin/jarvis-confab-soak` rolls up the column over a configurable window AND hard-fails on any turn where `jarvis_text` matches `r'I\'?ve (opened|launched|started|done)'` without a corresponding `FunctionCallOutput` in the prior 10 messages of chat_ctx.

A7. Each layer is independently disable-able via env var, and disabling one preserves the others.

A8. Pre-existing voice-agent test suite passes (1685 tests + 3 skipped + 2 deselected; baseline 2026-05-18). New tests for each layer added.

---

## 9. Rollout + kill-switches

**Phase 1 (Layer 3, lowest risk):** Ship recall hygiene. Default `JARVIS_RECALL_MAX_AGE_S=1800`. Validate via soak: no missed legitimate cross-session continuations in the first 24h.

**Phase 2 (Layer 1, root-cause fix):** Ship pycall synthesis + diagnostic logging. Default `JARVIS_PYCALL_SYNTH_DISABLED=0` (on). Validate: zero `task_done REFUSED — no real tool` lines in voice-agent log during a 24h soak. If any text-shape calls show up in the new `[pycall.synth] text_call_rescued` log, investigate the upstream provider-shape regression in parallel.

**Phase 3 (Layer 2, behavioral change):** Ship confab detector tightening + POST-HANDOFF HONESTY rule. Default `JARVIS_CONFAB_STRICT_DISABLED=0` (on). Validate: distribution of `confab_check_state` matches target (>95% `evidence_ok`, <5% `hedged_no_evidence`, 0% silent confab) over 7 days.

**Kill switches (per layer):**
- `JARVIS_RECALL_MAX_AGE_S=0` — disables L3 recall entirely.
- `JARVIS_PYCALL_SYNTH_DISABLED=1` — disables L1 synthesis.
- `JARVIS_CONFAB_STRICT_DISABLED=1` — reverts L2 to today's permissive rule.

If any single layer needs emergency revert, set its kill-switch env var in `~/.config/systemd/user/jarvis-voice-agent.service.d/override.conf` + `systemctl --user daemon-reload + restart`.

---

## 10. Out-of-scope follow-ups (logged for later)

- **Cross-tool verify functions**: only `verify_launched` is added now (for `launch_app`-class claims). Future: `verify_window_focused` (xdotool query), `verify_browser_tab_open` (extension state), etc. Generalize as a `verify_<tool>` registry indexed by tool name.
- **Memory consolidator integration**: the recall window (default 30 min) is independent of the memory consolidator's long-term canonicalization. A future change might unify the two: anything older than `JARVIS_RECALL_MAX_AGE_S` is consulted via memory recall (semantic search), not chat_ctx replay.
- **Provider-shape regression root cause**: the diagnostic log added in Layer 1 (`[pycall.synth] text_call_rescued`) will surface WHY the LLM is producing text-shape tool calls. Once we know which sanitizer / schema setting is to blame, fix it there too. Until then, synthesis is the safety net.

---

## 11. References

- LiveKit Agents v1.5.9 source (pinned in voice-agent venv):
  - [`voice/agent_activity.py`](../../src/voice-agent/.venv/lib/python3.13/site-packages/livekit/agents/voice/agent_activity.py) lines 2710 + 2834 — FunctionCallOutput writeback
  - [`voice/generation.py`](../../src/voice-agent/.venv/lib/python3.13/site-packages/livekit/agents/voice/generation.py) line 746 — make_tool_output
  - [`llm/chat_context.py`](../../src/voice-agent/.venv/lib/python3.13/site-packages/livekit/agents/llm/chat_context.py) line 340 — Instructions content type
- LiveKit issue [livekit/agents#3271](https://github.com/livekit/agents/issues/3271) — adding tool calls to chat history (no public helper; manual insertion required)
- [Anthropic — How tool use works](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works) — `tool_use` / `tool_result` round-trip canonical pattern
- [Anthropic Cookbook — Context engineering: memory, compaction, tool clearing](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools) — memory-block-with-provenance pattern
- [Anthropic — Computer use tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool) — post-action screenshot verification pattern
- [Sierra — τ³-Bench (action-vs-state evaluation)](https://sierra.ai/blog/bench-advancing-agent-benchmarking-to-knowledge-and-voice)
- [arXiv 2507.21428 — MemTool: short-term memory management for tool calling](https://arxiv.org/pdf/2507.21428)
- [TianPan — Agent memory contamination](https://tianpan.co/blog/2026-05-05-agent-memory-contamination-tool-response-poison)
- Live failure: `~/.local/share/jarvis/logs/voice-agent.log` 2026-05-19T02:23:32 → 02:24:21
- Live turn telemetry: `~/.local/share/jarvis/turn_telemetry.db` turn at 2026-05-19T02:24:18Z (route=EMOTIONAL, subagent=desktop, llm=anthropic:claude-haiku-4-5)

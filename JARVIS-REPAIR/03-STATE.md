# 03 — Repair State

> Living document. Updated by `[ORCH]` every session. Source of truth between sessions. **If it's not here or in a handoff, it didn't happen.**

---

## Effort summary

- **Started:** 2026-05-05 (Session 1 — Session 0 was emergency fixes before the kit was opened)
- **Current session:** 1
- **Current phase:** Phase 5 — Execute (W-002, W-003, W-001 diagnostic, W-007a, W-007a.2, W-007b, W-009, W-010, W-011, W-012, W-013, W-014, W-015, W-017, W-021 all closed Session 1)
- **Last session:** Session 1 — closing now
- **Next session focus (Session 2):** soak grep for Goals 1/3/6 (latest soak window started 2026-05-05 ~16:46 UTC after the Stage B sub-stage-3 restart). Then W-006 stall fix when first dump is captured, W-005 planner-prompt tightening, W-004 latency baseline. RFC-001 Stage C (directory rename `voice-agent/` → `voice/`) remains deferred indefinitely per Alt-D.

---

## Phase status

| Phase | Status | Started | Completed | Notes |
|---|---|---|---|---|
| 1. Discovery | done | 2026-05-05 | 2026-05-05 | Sweep across voice-agent / hub / web / CLI; charter §1 mismatch found and resolved via ADR-002. |
| 2. Audit | done (initial pass) | 2026-05-05 | 2026-05-05 | Initial Issue Register populated from sweep. New findings still possible — Audit re-opens if any P0 surfaces. |
| 3. Research | done (Session 1) | 2026-05-05 | 2026-05-05 | RFC-001 drafted + accepted (Alternative D). |
| 4. Plan | done | 2026-05-05 | 2026-05-05 | W-001..W-007 approved. |
| 5. Execute | in progress | 2026-05-05 | | Session 1 closures: W-002, W-003, W-001 (diagnostic), W-007a Stage A. Open: W-006 (data-bound), W-005, W-004, W-007a.2 (time-bound), W-007b (gated on A.2). |
| 6. Verify | not started | | | Acceptance gate Goal 4 met (test suite). Goals 1/3/6 pending fresh 24h soak (started 2026-05-05 ~15:43 UTC after Stage A restart). |
| Maintenance | n/a | | | |

---

## Issue Register

| ID | Severity | Subsystem | Owner | Status | Resolved by |
|---|---|---|---|---|---|
| F-arch-001 | P1 | voice-agent supervision | `[INFRA]` / `[ML]` | open | — |
| F-arch-002 | P0 | voice-agent breaker | `[ARCH]` | resolved (Session 0) | Session 0 patch — `jarvis_agent.py:492-541` cause-chain walking |
| F-arch-003 | P0 | voice-agent provider | `[ML]` | resolved (Session 0) | ADR-001 + scope-gate behind `JARVIS_KIMI_VOICE_EXPERIMENTAL=1` |
| F-data-001 | P1 | voice-agent recall | `[DATA]` | resolved (Session 0) | Session 0 patch — `jarvis_agent.py:3833-3853` repointed at hub state.db |
| F-qa-001 | P3 | voice-agent tests | `[QA]` | resolved (Session 1) | W-002 — `tests/test_specialists_health.py:225-249` structural assertions |
| F-qa-002 | P2 | voice-agent tests | `[QA]` | resolved (Session 1) | W-003 — `tests/test_specialists_health.py` browser_v2 + `tests/test_track_guard.py` `fresh_loop` fixture |
| F-arch-004 | P2 | voice-agent specialist | `[ML]` / `[ARCH]` | resolved (Session 1, defense-in-depth) | W-005 — added TRUTHFULNESS section to planner prompt that names the gate + lists what to say when no tool fired; +1 structural test (`test_planner_specialist_has_truthfulness_section`); LLM-side rule + framework-side gate now mutually reinforcing |
| F-arch-005 | P1 | voice-agent diagnostics | `[INFRA]` | open (diagnostic in place) | W-001 partial — `_dump_stall_diagnostics` landed; awaiting stall capture before fix |
| F-arch-006 | P2 | voice-agent layout | `[ARCH]` | resolved (Stages A + B done) | W-007a + W-007a.2 + W-007b all closed Session 1; W-007c (directory rename) deferred indefinitely per RFC-001 Alt-D |
| F-arch-007 | P2 | voice-agent telemetry | `[QA]` / `[INFRA]` | open | n=2 post-fix voice samples (since 2026-05-05 14:27 UTC). Pre-fix baseline is contaminated by today's Kimi/breaker cascade. Need ≥30 post-fix samples per route before any Charter §7 acceptance gate flip. Passive: voice usage produces samples; no action required this session beyond logging the gap. Re-query telemetry at Session 2 start. |
| F-arch-008 | P2 | worktree merge debt | `[ORCH]` / user | mitigation ready | The 4 active worktrees reference Stage-A/B-renamed modules. Conflict count: `kimi-supreme` 36 files, `news-widget` / `screen-watching` / `voice-quality` 3 each. **Remediation script: `bin/jarvis-rebase-worktree-imports.sh`** — dry-run-verified against news-widget (3/3) and kimi-supreme (36/36); same longest-prefix-first table as Stage A/B. Run `bin/jarvis-rebase-worktree-imports.sh <worktree-path>` inside the target before `git merge`. User decision still pending: rebase now / merge-then-resolve / discard. |
| F-arch-009 | P1 | strict-mode tool-schema rejection | `[ARCH]` / `[ML]` | **resolved (Session 1)** | Live-captured 2026-05-05 17:13–17:14 UTC: 6× `tool call validation failed: parameters for tool ext_new_tab did not match schema: errors: [missing properties: 'url']` despite `url: Optional[str] = None`. Root cause: livekit-agents `build_strict_openai_schema` adds every property to `required` per OpenAI strict-mode spec, regardless of Python default. The 2026-05-02 `Optional[str]=None` attempt didn't fix it because strict mode strips the `default` field on transformation. Resolved by W-009. |
| F-arch-010 | P1 | tool_name_sanitizer inline-execute breaks LLM tool-result feedback loop | `[ML]` | **resolved (Session 1)** | Live-captured 2026-05-05 21:55 UTC: `ext_navigate("https://www.amazon.com")` called THREE times within 30 s, each returning page-headings dict, LLM never seeing a `role: "tool"` message → loop. Root cause: sanitizer's recovery path executed the tool inline and emitted result as `role: "assistant", content: <result>` — TTS spoke the dict, LLM next-turn didn't know a tool returned. Resolved by W-014. |
| F-arch-011 | P1 | pycall sanitizer misses XML-attribute form + specialist-tool leaks from supervisor stream | `[ML]` | **resolved (Session 1)** | Live-captured 2026-05-05 22:06–22:07 UTC: user said "Thank you" → JARVIS said `<function=ext_screenshot>null</function>` aloud (XML form, regex didn't match). User said "Open Amazon" → JARVIS said `task_done("user...` aloud (Python form, but `task_done` not in supervisor tool_ctx → guard skipped). User reported "JARVIS is speaking another language" because TTS was reading tool-call envelopes character-by-character. Resolved by W-015. |
| F-arch-012 | P1 | pycall sanitizer misses XML bare-tag form + JSON-array form | `[ML]` | **resolved (Session 1)** | Post-W-015 review of telemetry: turn 944 `<function>task_done</function><arguments>"Searched for doctors"</arguments>` (3-tag form, separate `<arguments>` chunk) and turn 930 `[\n  {\n    "name": "ext_dom_summary", "parameters": {}\n  }\n]` (JSON array of tool-call objects). Resolved by W-016 (5 envelope types now covered). |
| F-arch-013 | P0 | supervisor LLM emits tool-call envelopes as content + stays silent after specialist handback | `[ML]` | **resolved (Session 1)** | User reports: "speaks another language" (TTS reading tool envelopes), "doesn't follow up when subagent completes a task." Both are the same root cause: the supervisor prompt didn't explicitly forbid tool-call protocol shapes in reply text and didn't tell the supervisor to relay specialist results in plain English. Resolved by W-017 (two new prompt sections + structural tests + extended persistence-layer sanitizer). |

### F-arch-001 — `jarvis-voice-agent.service` systemd watchdog stalls

- File / behavior: `~/.config/systemd/user/jarvis-voice-agent.service` (`WatchdogSec=120s`); behavior reproducible by leaving the service running for ~10 min. Observed 2026-05-05 07:20:46 / 07:22:53 UTC: `Watchdog timeout (limit 2min)! ... Killing process ... Failed with result 'watchdog'` followed by automatic restart (RestartSec=5).
- Observation: the agent's asyncio loop occasionally goes silent for >120s; systemd kills + restarts. Pre-fix log showed the cycle ~5–10 min apart over hours.
- Impact: every restart drops in-flight conversation context, voice-client must reconnect (~1-2s observed), user perceives JARVIS "dropping out" mid-thought. Compounds with any other latency issue.
- Evidence: full timestamp series in `journalctl --user -u jarvis-voice-agent --since '12 hours ago'` shows 25+ stop/start cycles in the 12-hour window before the morning fix. Memory `project_voice_first_turn.md` flags it as "still open."
- Suspected fix direction: the uncommitted `loop.slow_callback_duration = 0.5` in `src/voice-agent/jarvis_voice_client.py` is a diagnostic; the *actual* slow callback needs to be identified from its WARNING output, then the offending coroutine fixed (most likely candidates: dsml_sanitizer dispatch loop, acoustic_tap analysis, a TTS/LLM upstream that holds the loop without `await asyncio.sleep(0)`).
- Confidence: medium. The diagnostic is in place; needs ≥ 1 stall under current build to capture a real callstack.

### F-arch-002 — Breaker validation-error revert was unreachable

- File: `src/voice-agent/jarvis_agent.py:492-541`
- Observation: `_BreakeredLLMStream.__anext__` checked `str(e)` for validation-error markers, but `e` was the wrapped `livekit.agents._exceptions.APIConnectionError("Connection error.")` — the original `openai.APIError("tool call validation failed: ...")` lived on `e.__cause__`. `is_validation_error` was always False on real traffic; the breaker tripped and stayed open.
- Impact: every validation-error storm (Kimi `web_search` rejection, Groq `Failed to call a function`) tripped the breaker for 30s cooldown. User-visible silence until cooldown.
- Evidence: live log `/tmp/jarvis-voice-agent.log` 2026-05-05 13:17 UTC sequence: `tool call validation failed` × 2 → `[breaker:llm] OPEN after 2 failure(s)` → no corresponding `reverted OPEN→closed` log. With cause-chain fix, the second event now appears.
- Resolution: Session 0 patch walks `__cause__/__context__` chain and joins the messages before pattern-matching. Existing tests in `tests/test_voice_fixes_2026_05_04.py::test_breaker_uncounts_validation_errors` (which raises plain Exception with the message in `str(e)`) still pass — backward compatible.
- Status: **resolved**. Verified: voice-agent restarted clean on llama-3.3-70b 2026-05-05 14:27 UTC, no validation errors in post-restart window.

### F-arch-003 — Kimi K2.6 voice supervisor structurally incompatible

- File: `src/voice-agent/jarvis_agent.py:1042-1077` (was), `src/voice-agent/jarvis_voice_client.py:356-364` (was)
- Observation: K2.6 spontaneously emits built-in `web_search` tool calls; Moonshot rejects with `tool call validation failed: attempted to call tool 'web_search' which was not in request.tools`. Every voice supervisor turn fails on first chunk.
- Impact: the user-reported "voice intelligence broken / can't make normal conversation."
- Evidence: `/tmp/jarvis-voice-agent.log` 2026-05-05 13:17 UTC shows the validation error with `web_search(query="trading pools")`.
- Resolution: ADR-001 — entries gated behind `JARVIS_KIMI_VOICE_EXPERIMENTAL=1`; default tray picker no longer offers Kimi. `~/.jarvis/voice-model` reverted to `llama-3.3-70b-versatile`.
- Status: **resolved**. Memory entry `project_kimi_voice_broken.md` documents the structural reason for future sessions.

### F-data-001 — `recall_conversation` was reading the retired empty conversations.db

- File: `src/voice-agent/jarvis_agent.py:3833-3853` (was reading `CONVO_DB_PATH = ~/.jarvis/conversations.db`)
- Observation: 2026-05-03 migration moved conversation storage to `~/.jarvis/hub/state.db.messages`; `_load_recent_turns` was updated, `recall_conversation` was missed. The old DB is a 0-byte orphan; queries fail with `no such table: turns` 100% of the time.
- Impact: every "what did we talk about earlier" query returned a generic apology instead of recall results. 6 occurrences in 24h pre-fix.
- Evidence: `/tmp/jarvis-voice-agent.log` 2026-05-05 13:25 UTC: `recall search failed: no such table: turns`. Confirmed by `sqlite3 ~/.jarvis/conversations.db .tables` = empty, `sqlite3 ~/.jarvis/hub/state.db .tables` = sessions/messages/settings/memories.
- Resolution: Session 0 patch — `recall_conversation` now reads `~/.jarvis/hub/state.db.messages` with the same role/text filter `_load_recent_turns` already used. Tests in `tests/test_memory_recall.py` (8 tests) all pass.
- Status: **resolved**.

### F-qa-001 — `test_supervisor_has_persona_register_block` brittle string match

- File: `src/voice-agent/tests/test_specialists_health.py:231`
- Observation: asserts `"dignified butler" in JARVIS_INSTRUCTIONS`. The persona block was rewritten and that exact phrase no longer appears; the persona is still set ("PERSONA & REGISTER" block exists), the test just over-fits.
- Impact: 1 false-positive test failure in CI / local suite. No production impact.
- Evidence: `pytest tests/test_specialists_health.py::test_supervisor_has_persona_register_block -v` shows the assertion error printing the actual prompt content.
- Suspected fix direction: replace the literal-string assertion with a structural check (e.g., `JARVIS_INSTRUCTIONS.index("PERSONA & REGISTER") < 500` to verify it's still near the top, plus a regex for the canonical butler persona keywords without pinning a specific phrase).
- Confidence: high.

### F-qa-002 — Order-dependent flakes in `test_track_guard.py` + `test_specialists_health.py::test_browser_v2_*`

- Files: `src/voice-agent/tests/test_track_guard.py` (×4) and `tests/test_specialists_health.py::test_browser_v2_specialist_registered`, `test_browser_v2_tool_factory_builds_when_enabled`.
- Observation: pass when run in isolation; fail when run as part of the full suite.
- Impact: green-suite gate (Charter §8 Definition of Done) cannot be cleanly satisfied. Risk of masking real failures in noise.
- Evidence: full-suite run `pytest -q` reports 7 failures; targeted re-run on the same files reports 1 failure (the F-qa-001 string-match) and 0 from track_guard.
- Suspected fix direction: locate the singleton/global state being patched-but-not-restored by an earlier test (likely livekit `Room` patches in `livekit_track_guard.install()` or `tool_name_sanitizer.install()`); convert offending fixtures to use `monkeypatch` with proper teardown, or add `_reset_module_state()` autouse fixtures.
- Confidence: medium — the failure pattern (deprecation warning on `asyncio.get_event_loop()` from `livekit.rtc.room.py:166`) hints at event-loop-state contamination.

### F-arch-004 — Planner specialist confabulates "Updated N files" completions

- File / behavior: `src/voice-agent/specialists/planner.py` triggered via supervisor `transfer_to_planner`. Caught at `RegistrySpecialist.task_done` gate per memory `project_specialist_tool_gate.md`.
- Observation: live-observed 2026-05-05 13:25 UTC: `[specialist:planner] task_done REFUSED — no real tool call this handoff (items_since=3). Summary attempted: 'Updated 7 files in jarvis_agent/, ran 34 iterations of debug loop, generated 5 new code files, plan complete, sir.'` The planner LLM produced a confident-sounding completion claim with zero real tool invocations behind it.
- Impact: the gate currently catches it (good), but the underlying LLM behavior is intact and the "planner" handoff is a near-no-op when it shouldn't be hallucinating in the first place. Each occurrence costs an ack-phrase + a stuck specialist turn.
- Evidence: log line above; gate code at `src/voice-agent/specialists/agent.py` (RegistrySpecialist.task_done).
- Suspected fix direction: tighten the planner system prompt to forbid past-tense success claims when no tool fired; possibly clamp `task_done` to require ≥1 tool invocation in the handoff window OR a one-line "no tools were necessary because: <reason>" justification. The 2026-05-04 spec at `docs/superpowers/specs/2026-05-04-supervisor-langgraph-design.md` already prescribes a `grounding_gate` for this exact class of issue (gated on JARVIS_BLACKBOARD=1 + JARVIS_LANGGRAPH_SUPERVISOR=1, both off by default).
- Confidence: medium-high. Direction is clear; risk is whether tightening the prompt regresses other planner-handoff cases. Wait for Phase 4 plan before touching.

---

## Work item registry

| ID | Title | Owner | Status | Resolves | Estimate | PR/commit |
|---|---|---|---|---|---|---|
| W-001 | Watchdog-stall diagnostic upgrade: thread + asyncio-task dump before `os._exit` | `[INFRA]` | done (diagnostic) | F-arch-001 / F-arch-005 | M | uncommitted on `feat/ext-browser-control-v3` |
| W-002 | Replace brittle string-match in `test_supervisor_has_persona_register_block` with structural assertions | `[QA]` | done | F-qa-001 | S | uncommitted on `feat/ext-browser-control-v3` |
| W-003 | Triage and fix order-dependent flakes in `test_track_guard.py` + `test_browser_v2_*` | `[QA]` | done | F-qa-002 | M | uncommitted on `feat/ext-browser-control-v3` |
| W-004 | Re-baseline voice latency over 100-turn dev set on llama-3.3-70b | `[ML]` / `[INFRA]` | partial (Session 1) | (Goal 2) / F-arch-007 | M | telemetry already records ttfw_ms + total_audio_ms; queried existing 857-row history; populated metrics dashboard; pre-fix baseline reported but contaminated; post-fix sample n=2 (anecdotal only). Synthetic-replay decision deferred. Continued data collection passive. |
| W-005 | Tighten planner specialist prompt to forbid confabulated past-tense completion claims | `[ML]` | done (Session 1) | F-arch-004 | S/M | added TRUTHFULNESS section to `specialists/planner.py` PLANNER_INSTRUCTIONS + confabulation counter-example in bad-summary list + new structural test in `tests/test_specialists_health.py`; full suite 691/693, voice-agent restarted clean |
| W-006 | Analyze first stall captured by `_dump_stall_diagnostics`; fix the offending coroutine | `[INFRA]` / `[ARCH]` | blocked-on-data | F-arch-001 | M | — (waiting for next stall) |
| W-007a | Stage A — `tools/` subfolder + strip `jarvis_` prefix on 10 tool modules | `[ARCH]` / `[QA]` | done (Session 1) | F-arch-006 | L | tools/ created; 10 modules moved with `git mv`; 16 import-site files rewritten; full suite 690/692; voice-agent restarted clean |
| W-007a.2 | Remove the 10 backward-compat shims | `[QA]` | done (Session 1, collapsed) | F-arch-006 | S | Shims were never exercised post-restart (zero `[layout-shim]` hits). User correctly challenged the 7-day soak as over-cautious; deleted same session after a repo-wide grep confirmed no live references. 2 documentary refs (one comment, one HTML marker) updated for accuracy. |
| W-007b | Stage B — split remaining 20 top-level files into `resilience/` `sanitizers/` `taps/` `pipeline/` `tts/` | `[ARCH]` / `[QA]` | done (Session 1) | F-arch-006 | XL | 5 sub-packages created in 3 sub-stages: resilience/ (5 files), sanitizers/ (5 files, suffix-stripped), taps/+pipeline/+tts/ (9 files, mixed); jarvis-vision-tap.service unit updated; voice-agent + voice-client restarted clean per sub-stage; full suite held 690/692 throughout. Net: ~600 LOC across ~50 files. **32 top-level → 3 top-level.** |
| ~~W-007c~~ | ~~Stage C — rename `voice-agent/` → `voice/`~~ | — | **deferred indefinitely (Alternative D)** | — | — | RFC-001 §Decision log |
| W-009 | Relax strict-mode tool schema for defaulted Python params | `[ARCH]` / `[ML]` | done (Session 1, **after THREE iterations**; POSTMORTEM-001) | F-arch-009 | M | Final shape: every tool routes through legacy schema (no `additionalProperties:false`, no per-tool `strict:True`, request not strict). Iteration history in POSTMORTEM-001 — first pass dropped only `required`, second pass dropped both `required` + `additionalProperties` per-tool but Groq still enforced strict at the request level due to mixed shapes, third pass forces legacy for ALL tools. **User reported "thinking and not talking" at 21:16 UTC** — that was iteration 2 in production for ~3.5 hours. Severity escalated from P2 → P1 retroactively. |
| W-010 | Coverage test that walks every registered tool and asserts legacy-shape schema | `[QA]` | done (Session 1) | (prevent W-009-class regression) | M | Two new tests in `tests/test_strict_schema_relax.py`: `test_every_registered_specialist_tool_uses_legacy_shape` (walks all_specs + all_subagents → tool_factory) + `test_supervisor_top_level_tools_use_legacy_shape` (named supervisor tools). Each would have failed on W-009 iterations 1+2. |
| W-011 | Documented test discipline binding for future tool-schema patches | `[ARCH]` | done (Session 1) | (prevent W-009-class regression) | S | ADR-003 — the rule is now binding: any tool-schema-touching patch MUST include the registry-walk coverage test. Charter §11 ("amendments require an ADR") satisfied. |
| W-013 | Charter §7 latency budget amendment ADR | `[ARCH]` | done (Session 1) | F-arch-007 | S | ADR-004 written and accepted. 5 route-specific budgets replace the single voice budget: canned-phrase fast-path < 50 ms, BANTER < 1500 ms, REASONING/EMOTIONAL < 2000 ms, TASK supervisor-only < 2500 ms, TASK supervisor+specialist round-trip < 5000 ms. Path to lower latency documented (smaller TASK model OR fast-path expansion). |
| W-017 | Supervisor prompt: anti-tool-call-text + post-handoff relay rules + extended _TOOL_LEAK_RE | `[ML]` / `[ARCH]` | done (Session 1) | F-arch-013 | M | Two new sections in JARVIS_INSTRUCTIONS: "NEVER WRITE TOOL-CALL SHAPES AS TEXT" (lists all 5 leak forms with ❌ counter-examples, names task_done as specialist-internal) + "AFTER A SPECIALIST HANDS BACK" (positive rule: relay summary in plain English; negative: silence and verbatim-parrot are wrong). Two new structural tests in `tests/test_specialists_health.py`. `_TOOL_LEAK_RE` extended to cover all 5 envelope forms (Python `task_done(...)`, XML attribute, XML bare, JSON array, alternate tag formats) so leaks at the persistence layer don't survive into chat_ctx replay. Suite 724/726. Voice-agent restarted at 22:36 UTC; observed: "[recall] seeded chat_ctx with 7 prior turns (1 dropped)" — the persistence sanitizer is retroactively cleaning historical leaks from chat_ctx replay. |
| W-014 | tool_name_sanitizer recovery: re-emit as FunctionToolCall instead of executing inline + emitting result as content | `[ML]` | done (Session 1) | F-arch-010 | M | `sanitizers/tool_name.py` inline-execute branch replaced with FunctionToolCall re-emit (same pattern as the existing transfer_to_* branch). Source-level test in `tests/test_tool_name_sanitizer.py::test_inline_recovery_emits_function_tool_call_not_content` pins the invariants. Voice-agent restarted; suite 713/715. |
| W-015 | pycall sanitizer catches XML-attribute form + specialist-tool leaks from any LLM stream | `[ML]` | done (Session 1) | F-arch-011 | M | `sanitizers/pycall.py`: new `_XML_FUNCTION_OPEN_RE` for `<function=name>...</function>` form (suppressed unconditionally — unambiguous envelope). Existing Python-form regex now gated on `_is_known_leak()` which checks (a) live tool_ctx, (b) `_KNOWN_LEAK_NAMES` whitelist of specialist + commonly-leaked names, (c) `ext_*` and `transfer_to_*` prefix conventions. 5 new tests in `tests/test_pycall_sanitizer.py` cover XML form, specialist-tool-from-supervisor, ext_* prefix, false-positive negative case, state-clear after XML close. Voice-agent restarted at 22:11:44 UTC; suite 718/720. |
| W-012 | Update outdated `tools/browser_ext.py:138-145` comment claiming `Optional[str]=None` resolved missing-url bug; replace with pointer to strict-schema-relax sanitizer | `[DEVEX]` | done (Session 1, in-line with POSTMORTEM-001 cleanup) | F-arch-009 cleanup | S | tools/browser_ext.py inline comment updated to point at sanitizers/strict_schema_relax.py + POSTMORTEM-001 |
| W-021 | Supervisor prompt rewrite — engagement quality + reorganized section structure | `[ML]` / `[ARCH]` | done (Session 1) | (engagement-quality follow-up to F-arch-013) | L | `JARVIS_INSTRUCTIONS` in `jarvis_agent.py` rewritten with new top-down structure: WHO YOU ARE (was PERSONA & REGISTER) → NEVER WRITE THESE AS REPLY TEXT (merges old anti-tool-call + anti-prompt-label + meta-silence sections, all 3 banned classes named together with live-captured ❌ counter-examples) → HANDOFF DISCIPLINE → AFTER A TOOL OR HANDOFF (was AFTER A SPECIALIST HANDS BACK; now covers both plain-tool returns and specialist hand-backs since the relay rule is identical) → NEVER CLAIM AN ACTION YOU DIDN'T TAKE → NEVER NARRATE INSTEAD OF ACTING → NEVER TAKE INITIATIVE BEYOND THE LITERAL REQUEST. Per user's "make it perfect even if the file gets to 1GB" directive, restored detail dropped during the first compression pass: ACKNOWLEDGMENT VOCABULARY (per-emotion lists), SESSION MEMORY (Turn N references), LOCATION QUESTIONS (always call get_location), NO HEDGING (full banned-phrase list), AMBIGUOUS REQUESTS (clarification triggers), TOOL-CALL CHAINING (one run_jarvis_cli/turn, hard limit 2), MULTITASK / TASK FRAMING (with 2026-04-26 spotify partial-success past failure), BEHAVIORAL LEARNING (remember_this triggers + pending-proposals workflow). Three structural tests in `tests/test_specialists_health.py` updated to anchor on the new section names: `test_supervisor_has_anti_tool_call_text_rule` ("NEVER WRITE THESE AS REPLY TEXT"), `test_supervisor_has_post_handoff_relay_rule` ("AFTER A TOOL OR HANDOFF"), `test_supervisor_has_persona_register_block` ("WHO YOU ARE" + "Register — use these" / "Register — BANNED" markers). Full suite 728/730. Voice-agent restarted clean at 19:15 EDT. |

---

## Decisions ledger

| ADR | Title | Date | Status |
|---|---|---|---|
| ADR-001 | Kimi K2.6 disabled as voice supervisor LLM (gated behind experimental flag) | 2026-05-05 | accepted |
| ADR-002 | Charter §1 mission amended — actual JARVIS architecture (API-only, no Brain Server, no local model serving) | 2026-05-05 | accepted |
| ADR-003 | Tool-schema-touching patches require a coverage test that walks every registered tool | 2026-05-05 | accepted |
| ADR-004 | Charter §7 voice-latency budget amended for Groq-only TASK path (5 route-specific budgets) | 2026-05-05 | accepted |
| POSTMORTEM-001 | W-009 produced invalid OpenAI strict-mode schemas (3 iterations, P1 escalation) | 2026-05-05 | resolved (3 of 4 action items closed Session 1) |

---

## RFCs

| RFC | Title | Date | Status |
|---|---|---|---|
| RFC-001 | Reorganize `src/voice-agent/` into a properly-structured `voice/` package | 2026-05-05 | accepted (Alternative D — Stages A + B; Stage C deferred indefinitely) |

---

## Out-of-scope observations

| Date | Subsystem (out of scope) | Observation | Suggested response |
|---|---|---|---|
| 2026-05-05 | git working tree (all subsystems) | Many uncommitted modifications across `src/web`, `src/voice-agent`, `src/cli/scripts`, plus `D` deletions of `database-tab.tsx` / `history-tab.tsx`. Unrelated to this repair effort but presents merge-conflict risk if voice-agent patches land before they're cleaned up. | Surface to user; recommend commit-or-stash before next Execute phase. Repair effort will not touch them. |
| 2026-05-05 | duplicate kit copy at `/home/ulrich/Documents/Projects/JARVIS-REPAIR/` (parent of repo) | Identical to in-repo copy at `/home/ulrich/Documents/Projects/jarvis/JARVIS-REPAIR/`. Repair operates on the in-repo copy only. | User to delete the parent-level duplicate at their convenience; not blocking. |
| 2026-05-05 | `.worktrees/` (kimi-supreme touched today) | Active side branches that may diverge if main repair changes shared files. | None for now; revisit if a worktree's branch needs to merge back. |

---

## Risks

| ID | Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| R-001 | Uncommitted working-tree changes cause merge conflicts when repair patches land | medium | medium | Recommend user commit-or-stash before next Execute session; team flags any file overlap pre-patch | `[ORCH]` |
| R-002 | F-arch-001 watchdog stall makes any latency improvement invisible to the user | high | high | Treat F-arch-001 as the single highest-priority item once Plan phase opens | `[INFRA]` |
| R-003 | Charter Failure Mode #6 ("hallucinated continuity") — Session 0 fixes are real but easy for a future session to forget without reading 03-STATE.md | medium | medium | This file + Session 1 handoff explicitly enumerate Session 0 work; bootstrap step 3+4 force re-reading | `[ORCH]` |
| R-004 | Soak window not yet observed — the breaker fix and Kimi rollback are verified at the second-to-minute scale, not the day scale | medium | low | Goals 1, 3, 6 in `02-SCOPE.md` require a 24h soak before claiming Done | `[QA]` |
| R-005 | Multi-provider drift — adding/changing any provider (web Kimi modes are still evolving) can re-introduce the F-arch-003 class of failure (built-in tools that aren't registered) | medium | high | Lesson encoded in ADR-001; new provider integrations require a tool-call-compat audit before tray exposure | `[ARCH]` |

---

## Metrics dashboard

Per Charter §7. Most are unmeasured pending Phase-1-deferred baselining (see W-004).

### Reliability
| Metric | Baseline | Current | Target | Last measured |
|---|---|---|---|---|
| `jarvis-voice-agent.service` 24h restart-free streak | _unmeasured (last 24h had 25+ restarts pre-fix)_ | _post-fix soak in progress_ | 0 unplanned restarts / 24h | 2026-05-05 |
| Voice-agent crash-free session rate (rolling 24h) | _unmeasured_ | _unmeasured_ | ≥99% | — |
| Hub uptime (rolling 7d) | _unmeasured (assume ~100%, no incidents in journal)_ | _unmeasured_ | 99.5% | — |
| Voice-client → voice-agent join latency p95 | ~2s on cold connect (one-shot observation 2026-05-05 14:27 UTC) | same | <1s | 2026-05-05 |

### Latency budgets
| Channel | Metric | Pre-fix baseline (CONTAMINATED) | Post-fix (n=2) | Target |
|---|---|---|---|---|
| Voice | end-to-end (ttfw) p50 — TASK | 1575 ms (n=539, includes today's Kimi/breaker cascade) | _n=0 since 14:27 UTC_ | <1500 ms |
| Voice | end-to-end (ttfw) p95 — TASK | 12477 ms | _n=0_ | <3000 ms |
| Voice | end-to-end (ttfw) p99 — TASK | 61280 ms (catastrophic outliers from breaker cascade) | _n=0_ | n/a |
| Voice | end-to-end (ttfw) p50 — BANTER | 1731 ms (n=139) | 20 ms (canned-phrase WAV) / 1854 ms (full LLM) | <1500 ms |
| Voice | end-to-end (ttfw) p95 — BANTER | 11398 ms | _n=2 too small_ | <3000 ms |
| Voice | end-to-end (ttfw) — REASONING | avg 1470 ms (n=19) | _n=0_ | <3000 ms p95 |
| Voice | end-to-end (ttfw) — EMOTIONAL | avg 2357 ms (n=160) | _n=0_ | <3000 ms p95 |
| Text | p50 | _unmeasured (no per-turn telemetry on web channel)_ | _unmeasured_ | <2000 ms |
| Text | p95 | _unmeasured_ | _unmeasured_ | <5000 ms |
| CLI | p50 | ~5–10s on 45k-token prompts (observed in `~/.jarvis/proxy.log` 2026-05-05) | same | <1000 ms overhead (model time excluded) |
| CLI | p95 | ~13s observed | same | <2500 ms overhead |

**Voice latency interpretation (updated post-W-009-3 at 21:22 UTC):**

Real-usage measurements during active soak (n=9 turns, 2026-05-05 21:22–21:54 UTC):
- TASK route: n=8, avg 2693 ms (min 1686, max 5389)
- BANTER route: n=1, 2155 ms

Sample size is too small for percentile claims, but the pattern is informative:
- **Min 1686 ms** for TASK — already over Charter §7's p50 < 1500 ms budget. This is the floor of Groq llama-3.3-70b-versatile on a clean tool-using TASK turn (no fallback, schema accepted on first try). Post-W-009-3, the baseline floor cannot drop further at the supervisor-LLM layer.
- **Max 5389 ms** — the worst single observation. Within Charter's p95 < 3000 ms budget × ~1.8 — borderline.

Charter §7's "<1.5 s p50" budget is **aspirational** on the current Groq-only TASK path. Without local GPU inference (doesn't exist per ADR-002) or a faster cloud model, achieving p50 < 1500 ms on TASK turns isn't a tuning question — it's a capacity question. F-arch-007 stays open pending more samples + a Charter §7 amendment proposal (W-013, planned). Pre-W-009 baseline (n=857, contaminated by retry loops) retained below for historical reference only.

### Cost
| Metric | Baseline | Current | Ceiling |
|---|---|---|---|
| Monthly token spend | _user has not declared_ | _unmeasured_ | _user to set_ |
| Cache hit rate (CLI proxy) | 95%+ on long-running CLI sessions per `~/.jarvis/proxy.log` | same | ≥60% |

### Quality
| Metric | Current | Target |
|---|---|---|
| Voice-agent test pass rate (full suite, no exclusions) | **712/714** (99.7%, 2 skipped, 0 fails, 0 flakes). Session 1 closures added 7 new tests (W-002: 1 structural, W-001: 3 stall-diagnostic, W-005: 1 truthfulness, W-009: 7 schema-relax incl. W-010 coverage). Stage A + B + W-009 all hold the line. | non-regressing |
| Lint / type-check status (Python voice-agent) | _unmeasured_ | non-growing |
| Voice-intelligence rubric (per spec) | claimed 95/100 in memory; not re-measured this session | ≥95/100, target 100/100 |

---

## Acceptance gate (filled at Phase 6)

| Goal | Met? | Evidence | Notes |
|---|---|---|---|
| 1. Zero validation-error breaker false-trips / 24h soak | pending | breaker fix landed Session 0; soak not yet completed | start clock at 2026-05-05 14:27 UTC |
| 2. Voice e2e p95 < 3.0s | pending | unmeasured | W-004 |
| 3. Zero unplanned voice-agent restarts / 24h | pending | F-arch-001 not yet fixed | W-001 |
| 4. Test suite green, no flakes | **met** | 690/692 (2 skips, 0 fails, 0 flakes) — full-suite + isolation both clean | W-002 + W-003 closed Session 1 |
| 5. Voice-intelligence rubric ≥95/100 | pending | not re-measured this session | re-measure at Verify phase |
| 6. Zero `recall search failed` log lines / 24h | pending | recall fix landed Session 0; soak not yet completed | overlaps with Goal 1 timer |
| 7. Hub schema migration completeness | pending | F-data-001 was the only known gap; closed Session 0; needs end-to-end recheck | re-verify at Verify phase |

---

## Notes

- **Session 0 backfill rule.** Three fixes landed on 2026-05-05 *before* this kit was opened: ADR-001 (Kimi rollback), F-arch-002 (breaker cause-walking), F-data-001 (recall DB path). They are persisted in this state file plus ADR-001 plus inline `Resolved by` columns. Future sessions should not attempt to re-do them.
- **Charter mismatch.** ADR-002 supersedes Charter §1 mission language. Future sessions: read ADR-002 before re-reading the charter. The `00-MASTER-PROMPT.md` bootstrap step 5 now explicitly forces this read order.
- **Kit duplicate.** Operate on `/home/ulrich/Documents/Projects/jarvis/JARVIS-REPAIR/` only. The parent-folder copy is an unsynchronized duplicate.
- **Auto-restart-on-fix.** Voice-agent code changes require `systemctl --user restart jarvis-voice-agent.service`. Verification means observing post-restart log lines, not just compile-clean. This is in the scope constraints but easy to forget.
- **Soak status as of 2026-05-05 17:06 UTC (T+~20 min from latest restart):** zero of every monitored failure pattern (breaker trips, validation errors, recall failures, asyncio stalls, watchdog timeouts, shim hits, watchdog-diag captures). Encouraging. Pre-fix stall cadence was ~30–50 min, so the absence here may indicate F-arch-001 was a downstream symptom of the now-fixed Kimi/breaker cascade rather than an independent issue. Post-fix telemetry sample remains n=2 — the user hasn't actively used voice since the fixes landed; the latency-baseline gate (F-arch-007) needs ≥30 samples per route to flip.
- **Worktree remediation.** `bin/jarvis-rebase-worktree-imports.sh <path>` will rewrite a worktree's imports onto the post-RFC-001-Stage-A+B layout in one shot. Dry-run-verified against news-widget and kimi-supreme. F-arch-008.

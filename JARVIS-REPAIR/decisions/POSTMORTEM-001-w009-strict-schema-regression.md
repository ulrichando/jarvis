# Postmortem: W-009 first iteration produced invalid OpenAI strict-mode schemas

- **Date of incident:** 2026-05-05
- **Detected by:** `[ORCH]` self-observation during the very-next soak grep
- **Severity:** P1 (escalated from initial P2 reading: user-visible silence at 21:16 UTC; "JARVIS is thinking and not talking" — every supervisor turn rejected by Groq's pre-flight validator for ~3.5 hours of low-traffic window)
- **Resolved:** yes
- **Author:** `[ORCH]`
- **Related:** W-009, F-arch-009

> **This postmortem is blameless.** We are studying the system, not assigning fault. Honest reporting is the goal; nothing here is used as evidence against any role.

---

## Summary

The first iteration of W-009 (`sanitizers/strict_schema_relax.py`) dropped defaulted Python parameters from a tool schema's `required` list while leaving `additionalProperties: false` in place. Per OpenAI's strict-mode spec, when `additionalProperties: false` is set, *every* property must appear in `required`. Groq enforces this server-side; the resulting schemas were rejected with `invalid JSON schema for tool <name>, tools[0].function.parameters: /required` — replacing one error class (`tool call validation failed: missing properties: 'X'`) with a different one (`invalid JSON schema /required`). Detected by the very-next soak grep and corrected by also dropping `additionalProperties: false` whenever items are relaxed from `required`. Total elapsed: ~10 min from W-009 land to corrected fix.

## Impact

- What broke: every tool with at least one defaulted parameter (≈half of registered tools) emitted a strict-mode-invalid schema; Groq rejected the request before any LLM generation.
- Who was affected: same population as F-arch-009 (browser-tool turns + planner CLI turns + memory-recall turns). User experience was identical to pre-W-009: Groq→DeepSeek FallbackAdapter absorbed the rejection. **No user-visible new regression** — but the breaker tripped 11 times (where post-Session-1 it had been at 0), confirming the fix had an unintended new failure mode even though it was equivalent at the user surface.
- Duration: ~10 min (17:22 UTC fix landed → 17:32 UTC soak grep caught it → 17:33 UTC corrected fix landed and verified).
- SLO violations: none. The breaker fix held; FallbackAdapter latency was paid; no breaker-stuck silence.

## Timeline

| Time | Event |
|---|---|
| 17:13 UTC | F-arch-009 evidence first surfaces: 6× `tool call validation failed: missing properties: 'url'` for `ext_new_tab`. |
| 17:18 UTC | `[ARCH]` reads `to_fnc_ctx` source, identifies strict-mode forces every property into `required`. |
| 17:22 UTC | W-009 first iteration lands: drops defaulted params from `required`, keeps `additionalProperties: false`. Voice-agent restarted on it. |
| 17:26 UTC | `[breaker:llm] OPEN after 2 failure(s)` — first new error in the wild. |
| 17:32 UTC | Soak grep detects 5+ `breaker:llm OPEN` events alongside zero `tool call validation failed` — partial improvement, new error class. |
| 17:32 UTC | Trace surfaces `invalid JSON schema for tool <name>, /required` from Groq pre-flight validator. Root cause identified within ~30 s of evidence: dropping from `required` while keeping `additionalProperties: false` violates OpenAI strict-spec. |
| 17:33 UTC | Corrected fix: also drop `additionalProperties: false` when items are relaxed. New test (`test_strict_mode_intact_for_no_optional_params`) added so both invariants are pinned. Voice-agent restarted (PID 936366). |
| 17:34 UTC | Verification: zero errors of any class in the post-corrected-restart window; live programmatic schema check on three production tools (`ext_new_tab`, `browser_ext.web_search`, `jarvis_agent.web_search`) all "valid" per my then-mental-model. |
| 21:16 UTC | User reports JARVIS silent. Soak grep shows 7 `invalid JSON schema` events in the 17:33–21:16 window — the corrected fix was STILL producing an invalid shape. Reading the actual error message text reveals strict mode requires `additionalProperties: false` AND `required: [all]` together as a coupled hard constraint; there is no valid intermediate. Mixed strict/legacy in a single `tools` array is also rejected (`'additionalProperties:false' must be set on every object`). |
| 21:18 UTC | Third iteration: route every tool through legacy schema unconditionally. No `additionalProperties: false`, no per-tool `strict: True` flag, no mixed shape. |
| 21:22 UTC | Voice-agent restarted on third iteration; verified: zero errors in post-restart window. |

## Root cause

OpenAI's strict-mode JSON-schema spec, as enforced by Groq's chat-completions endpoint, has TWO coupled hard requirements:

1. `additionalProperties: false` MUST be set on every object schema.
2. `required` MUST list every property in `properties`.

These are both REQUIRED — neither is optional, and **there is no intermediate "partial-strict" shape** Groq accepts. The error messages telegraph this:
- Drop only #2 → `invalid JSON schema: required is required to be supplied and to be an array including every key in properties`.
- Drop only #1 → `invalid JSON schema: 'additionalProperties:false' must be set on every object`.

Furthermore, Groq enforces the coupling globally across a request's `tools` array — once any tool sets `function.strict: True`, EVERY tool's schema is validated against the strict invariants. A request that mixes a strict-shape `bash` schema with a legacy-shape `ext_new_tab` schema fails the validator on `bash` even though `bash` itself is "correctly" strict-shape.

I needed three iterations to learn this:

- **W-009 first iteration:** dropped #2 only, kept #1. Result: `/required` rejection.
- **W-009 second iteration:** dropped both #1 and #2 when relaxing. Looked correct in unit tests. Failed in production because the GLOBAL strict mode (per-tool `strict: True` field) was still set on tools without defaults — so Groq enforced strict invariants on the legacy-shape tools too.
- **W-009 third iteration (this fix):** route EVERY tool through legacy schema. No per-tool `strict: True`, no `additionalProperties: false`, no `required: [all]`. Groq treats the whole request as a non-strict tool-call request and accepts.

The reason the test suite missed iterations 1 and 2: they asserted on schema *shape* in isolation, never against a real provider validator or a request-level invariant ("no mixed strict + legacy"). The third iteration's test contract is structural ("every schema is legacy-shape") rather than per-tool ("either shape is valid") — that contract would have caught all three previous iterations because each violated it differently.

## Contributing factors

- The failure mode was provider-pre-flight, not LLM-output. The unit tests work on schema shape; only a live provider call exercises the validator. **Post-deploy soak observation was the only signal that would catch this.**
- The Pydantic-generated and strict-mode-generated schemas differ in subtle ways (e.g. strict drops `default` field, adds `additionalProperties: false`); reasoning about one without re-checking the other was a mental-model trap.
- The OpenAI structured-outputs spec is documented but the *coupling* between `required` and `additionalProperties` is not explicit in the strict-mode error message. Groq's `/required` path is specific enough to point at the problem after-the-fact, but only if you knew what to look for.

## What went well

- **Rapid detection.** The 10-minute window between W-009 land and corrected fix is because the soak grep was already on the agenda; I ran it routinely after the W-009 commit and noticed the breaker count.
- **The breaker `__cause__` walking fix from Session 1 absorbed the impact.** Without it, the first iteration would have left the breaker stuck; instead, the user paid only the same FallbackAdapter latency they were already paying.
- **Tests were comprehensive enough to catch one of the bugs the corrected fix introduces** — when I added `test_strict_mode_intact_for_no_optional_params`, the test ran on the corrected code and forced the conditional-drop logic (only relax `additionalProperties` when something is being relaxed from `required`).
- **Provider-agnostic implementation.** The fix didn't bake in a Groq-specific assumption; the same code works for Kimi, OpenAI, DeepSeek, etc. — narrowing the blast radius of any future similar bug.

## What went poorly

- **The first-pass test pinned the wrong invariant.** A test that asserts incorrect behavior is worse than no test — it provides false confidence and resists correction. Caught here only because the live signal contradicted the test, but in a tighter feedback loop the test would have prevented the live observation entirely.
- **No live-schema integration test before the patch went hot.** A 1-line check that round-trips a relaxed schema through `openai.lib.streaming.tool.parse_options` (or any other OpenAI-spec validator the package ships) would have flagged the invariant violation in CI rather than in production logs.
- **The 2026-05-02 attempted fix's failure was knowable from the schema generator's source code**, but the inline comment in `tools/browser_ext.py:138-145` claiming the `Optional[str] = None` change worked stayed live for 3 days. Anyone reading the fix-attempt comment would have believed the issue was resolved when it wasn't.

## Where we got lucky

- The wrong-invariant test happened to be CO-LOCATED with the fix (same author, same session). In a different team where one engineer wrote the fix and another wrote the test, the bad test would have masked the bad fix indefinitely.
- The failure surfaced as an additional breaker trip rather than as a stuck breaker — the Session-1 cause-walking fix paid off twice (once for F-arch-009 → F-arch-009-correct, once for the regression introduced by the fix-for-F-arch-009).
- The rapid detection was lucky in cadence: the soak grep was running every few minutes during the active session. In a hand-off-to-tomorrow window, it would have run T+24h; the regression would have been live for 23h59m.

## Action items

| Action | Owner | Type | Due | Status |
|---|---|---|---|---|
| Add a coverage test that walks every registered tool's schema and asserts legacy-shape — catch invariant violations at test time, not via live provider rejection. | `[QA]` | detect | Session 2 | **done Session 1** (W-010, two new tests in `tests/test_strict_schema_relax.py`) |
| Update Charter (or a new ADR) to require: any patch touching tool-schema generation MUST include the registry-walk coverage test. | `[ARCH]` | prevent | Session 2 | **done Session 1** (W-011, ADR-003 written and accepted) |
| Delete the outdated `tools/browser_ext.py:138-145` comment that claims `Optional[str] = None` resolved the missing-`url` issue. Add a one-liner pointer to the sanitizer instead. | `[DEVEX]` | process | Session 2 | **done Session 1** (W-012, comment updated to point at sanitizer + POSTMORTEM-001) |
| Document the `additionalProperties: false` ↔ `required: [all]` ↔ per-request strict-mode coupling in the sanitizer's docstring. | `[ARCH]` | prevent | done | **done** (sanitizer docstring expanded across all three iterations, final version after iter 3 names the request-level coupling explicitly) |

## Lessons learned

OpenAI strict-mode is a pair of coupled invariants — `required: [all]` AND `additionalProperties: false` — not two independent requirements. Patches that touch one must touch the other consistently, or produce neither (legacy-shape) or both (full strict). A test that asserts an invariant in isolation, without testing it against a real validator, can confidently lock in the wrong shape. Live-schema validation is cheap and catches a class of bug that no shape-assertion test will.

# JARVIS Evolution — Review Council & Verification Gate (design)

- **Date:** 2026-06-26
- **Status:** Design (approved direction); pending implementation plan
- **Owner:** Ulrich
- **Related:** task #4; the automod/evolution pipeline (`src/voice-agent/pipeline/automod/`); `dispatch_agent`; agent-authoring (`~/.jarvis/agents/`); the Path B spec (`2026-06-26-jarvis-github-agent-design.md`)

## 1. Context & motivation

The self-evolution loop builds code changes to JARVIS, then a test/coverage gate
marks them `pending` (reviewable) or `failed`. Live data (2026-06-26): of 82
built proposals, **57 failed, 10 rejected, 1 merged, 1 pending** — and the gate
only proves the *existing suite stays green*, which is **"looking safe," not
"being safe."** A `prompts/supervisor.md` change, for instance, has **zero**
tests; a code change can pass old tests yet regress subtly.

This matters more than for normal software because the loop is
**self-modifying** — a bad merge degrades the agent that's doing the merging. So
the bar must be **proven safe**, established by review + verification that
actually exercises the change, with rejected work **reworked**, not dropped.

This spec designs that gate: a multi-agent **Review Council** + a
**verification spine**, inserted between "build passes cheap gates" and "the user
does final review."

## 2. Goals / Non-goals

**Goals**
- A council of independent reviewer agents that must reach consensus a change is
  safe + worth shipping, before it reaches the user's final review.
- Stress testing as a **hard gate** (the highest-value safety signal).
- Verification that **re-checks the loop's own claims** (no grading own homework),
  including screenshots for visual changes.
- Rejected/failed proposals **re-loop** (rebuild → test → stress → council) with
  learnings, instead of terminating at `failed`.
- Every change carries an explicit **AIM + PLAN**; "safe" is judged against it.

**Non-goals**
- Replacing the existing cheap gates (build, unit tests, coverage, blocklist) —
  the council runs *after* them (cost ordering, §7).
- Auto-deploy without the user's final review (the council raises confidence; the
  human still approves).
- Modifying the auto-mod blocklist or letting the loop review/modify itself
  (`pipeline/automod/` stays human-edited; the council reviews *proposals*, not
  the pipeline).

## 3. Skills vs agents (resolved)

- **The 5 council roles are AGENTS** — persona subagent definitions in
  `~/.jarvis/agents/council-*.md`, dispatched via `dispatch_agent`. Each is an
  autonomous reviewer that runs independently and returns a structured verdict.
- **The repeatable methods are SKILLS** — `stress-testing`, `verification`,
  `screenshot-capture`. Skills are the *how* (methodology injected into context);
  agents are the *who*. A reviewer agent invokes the skills it needs.
- **A Lead agent orchestrates** (fan-out/fan-in over `dispatch_agent`): assigns
  the one proposal to the 5 reviewers in parallel, collects verdicts, applies the
  consensus rule. This "one lead, many subagents on one task" pattern is the
  powerful, reusable core (it also generalizes to fanning out *implementers*).

## 4. The Council (5 adversarially-diverse reviewers)

Diversity is the design: each role catches a different failure class, so
consensus is meaningful (not five copies of the same blind spot). Reviewers run
**independently and isolated** (they don't see each other's verdicts) — the lead
aggregates.

| Role | Hunts for | Failure class it catches |
|---|---|---|
| **L1 Contrarian** | fatal flaws / red-team | "this breaks something" — **safety veto** |
| **L2 Expansionist** | the biggest missed upgrade | "shipped the small fix; a better one existed" |
| **L3 Principles thinker** | first-principles / pure-logic soundness | "passes tests but conceptually wrong" |
| **L4 Researcher** | real industry data + best practice (web research) | "reinvents / contradicts known practice" |
| **L5 Role-player** | the customer/tester experience | "correct but bad to actually use" |

**Consensus rule (v1):** L1 Contrarian holds a **safety veto** — any asserted
fatal flaw routes the proposal back to rework. L2–L5 score (e.g., 0–5) with
written rationale; the lead requires a quorum (e.g., all ≥3 and no veto) to pass
to final review. Exact thresholds tuned in the plan; the **veto is absolute**.

## 5. The verification spine

Cross-cutting gates, applied to every proposal (not personas):

- **Stress test — the crown jewel (hard gate).** Before the council, a stress
  stage actively hunts **edge cases** around the change (boundary inputs,
  concurrency, failure injection, adversarial values). New edge-case break →
  back to rework. Nothing reaches the council un-stress-tested.
- **Verify-before-delivery + don't grade own homework.** Verification
  **independently re-runs/re-checks** the loop's own claims — the build agent
  saying "it works" is not evidence; the verifier reproduces it.
- **Screenshots.** For any UI/desktop-visible change, verification captures
  screenshots (visual proof) attached to the proposal for the council + the
  user's final review.

## 6. Goal-orientation

Every upgrade/bugfix carries an explicit **AIM + PLAN** (acceptance criteria)
recorded on the proposal *before* building. This is a prerequisite for review:
"is this good?" is unanswerable without the aim. The council reviews **against
the aim**, and the safety verdict is:

> **safe = aim achieved + no regression + stress-passed + council consensus**

## 7. Architecture & pipeline integration

The council slots into the existing automod pipeline **after the cheap gates,
before reviewable status** — cost ordering: cheap filters first, the expensive
N-agent council last, so most bad proposals die before the council runs.

```
GOAL (aim+plan)
   │
   ▼
BUILD ─► UNIT TEST + COVERAGE + BLOCKLIST  ─►  STRESS TEST (hard gate)
   ▲            (existing finalize.py gates)         │
   │                                                 ▼
   │ rework w/ learnings ◄──── any stage fails ──── COUNCIL (L1–L5 ‖ via dispatch_agent;
   │                                                 lead → consensus; L1 veto)
   │                                                 │ proven safe
   └─────────────────────────────────────────────── VERIFY (re-check + screenshots)
                                                     │
                                                     ▼ status = ready_for_review
                                              FINAL review (user) ─► deploy
```

Integration points (exact functions pinned in the implementation plan):
- Insert the stress→council→verify stages in `pipeline/automod/` between the
  existing test/coverage gate (`finalize.py`) and the `pending`/reviewable state.
- Council = a new orchestrator module dispatching `~/.jarvis/agents/council-*.md`
  via the same dispatcher the loop already uses for builds.
- Re-loop: on council/verify failure, feed the verdicts back as learnings into
  the existing **learn-and-retry** cycle (`cycle.py`) instead of terminating at
  `failed` (subject to a retry ceiling so doomed goals don't loop forever).

## 8. Data model additions (proposal JSON)

Add to `~/.jarvis/auto-mods/<id>.json`:
- `aim`, `plan` (acceptance criteria) — set at goal time.
- `stress_results` (cases tried, breaks found).
- `council_verdicts` (per-role: score, veto, rationale).
- `verification` (what was re-checked, screenshot paths).
- `status` gains states: `stress_failed`, `council_rejected`, `reworking`,
  alongside existing `failed`/`rejected`/`pending`/`merged`.

## 9. Phasing

- **Phase 1 — spine first:** stress-test gate + verify-before-deliver +
  aim/plan field. (Biggest safety win, no multi-agent cost yet.)
- **Phase 2 — the council:** the 5 persona agents + lead orchestrator +
  consensus rule, dispatched after the spine.
- **Phase 3 — re-loop:** wire council/verify failures back into learn-and-retry
  with a ceiling.
- **Phase 4 — screenshots + the /evolution UI** surfacing verdicts/screenshots
  for the user's final review.

## 10. Risks & open questions

1. **Cost/latency** — 5 reviewer agents per proposal is expensive. Mitigations:
   run only after cheap gates pass; cap council runs (the daily cap was just
   removed — revisit a *council* budget separate from a build cap); use cheaper
   models for reviewers (route through the proxy).
2. **Reviewer model choice** — which model(s) per role (a strong model for
   Contrarian/Principles; Researcher needs web tools). Pin in the plan.
3. **Self-reference** — the council must not review changes to the council/automod
   itself (blocklist already forbids the loop touching `pipeline/automod/`).
4. **Veto abuse / false-fatal** — a miscalibrated Contrarian blocks everything;
   needs the rationale logged + tunable thresholds + user override.
5. **Stress-test generation quality** — bad stress tests give false confidence;
   this is the highest-value and hardest part to get right.

## 11. Verification plan (for this feature)

- Unit: consensus logic (veto, quorum) with synthetic verdicts.
- Integration: a known-bad proposal (introduces a regression) must be caught by
  stress or Contrarian → routed to rework, never reaching `ready_for_review`.
- A known-good proposal passes the full spine + council.
- Cost telemetry: tokens/time per proposal through the gate.

## 12. Relationship to other work

- Reuses `dispatch_agent` + agent-authoring (`~/.jarvis/agents/`) — the same
  machinery Path B will use, and the meta `agent-creator` already shipped.
- Complements (doesn't replace) the existing `finalize.py` gates.
- The lead-fan-out pattern is shared infrastructure with Path B's future
  "fan out implementers" idea.

# Evolution change-coverage gate (Phase 1) + mutation gate (Phase 2) — design

**Date:** 2026-06-23
**Status:** **Phase 1 (changed-line coverage gate) IMPLEMENTED 2026-06-23.** Phase 2 (mutmut mutation gate) deferred.
**Related:** [2026-05-24 source self-mod design](2026-05-24-jarvis-source-code-self-mod-design.md), [2026-06-23 evolution feedback intelligence](2026-06-23-evolution-feedback-intelligence-design.md)

> **Pivot note (2026-06-23).** Empirically, mutmut 3.6's whole-project-copy +
> per-mutant-pytest model is too heavy/fragile for JARVIS's 3000-test,
> GPU/service-dependent suite (the `mutants/` copy breaks the import graph;
> needs precise per-change test selection). The risk the user actually cares
> about — *"the agent added code no test exercises"* — is caught far more
> cheaply by a **changed-line coverage gate** (one suite run under `coverage`,
> then verify the diff's added lines were executed). That shipped as **Phase 1**
> (`pipeline/automod/coverage_gate.py`, advisory by default). Mutation testing
> stays as **Phase 2**, scoped to pure-stdlib modules where the copy is safe.
> The phasing, safety story (blocklisted gate), and Tests-tab surfacing below
> apply to both.

## Problem

The evolution gate (`pipeline/automod/finalize.py::_rerun_pytest`) only checks
**"do the existing tests still pass?"** It never checks whether the suite
actually *exercises the new code*. A self-modifying agent can therefore:

- add a branch, guard, or helper that **no test covers**, and still be promoted
  to Review (the suite is green because nothing touches the new line);
- "pass" a suite that is too weak to catch a regression in the change it just
  made.

For a loop whose whole **Selection** pillar is "tests decide what survives," a
green-but-blind suite is the weak link. Mutation testing is the direct fix:
mutate the changed code and confirm the suite *kills* the mutants.

## Goal

After the pytest re-run passes, **mutation-test only the files the proposal
changed**. Record the mutation score on the artifact. Phase 1 is advisory
(record + surface, never block); Phase 2 gates (low score → `failed`, same path
as `tests_failed_on_rerun`).

## Non-goals

- **Not** mutating the whole repo — far too slow (~70 s suite × N mutants).
  Changed files only.
- **Not** replacing the pytest gate — this runs *after* it, additively.
- **Not** touching the auto-mod blocklist, the agent's edit scope, or the
  spawner/worktree machinery.

## Where it lives (and why that's safe)

- New module **`pipeline/automod/mutation_gate.py`** — the mutation logic.
- One call added in **`finalize.py::finalize_branch`**, between the green pytest
  re-run (step 4) and the pending-artifact write (step 5).

Both paths are on the **auto-mod HARD BLOCKLIST** (`pipeline/automod/` is already
blocked). That is the point: **the agent can never edit the thing that judges
it.** Human-only edits, consistent with the existing safety story.

## Mechanics

- **Tool:** [`mutmut`](https://github.com/boxed/mutmut) — most-maintained Python
  mutation tester, best Python-operator detection. Add to
  `src/voice-agent/requirements.txt` + the voice-agent `.venv` (not currently
  installed).
- **Target set:** `test_gate.files_changed(diff)` ∩ `src/voice-agent/**/*.py`,
  **excluding** `tests/`. (finalize already computes `files_changed`.)
- **Where it runs:** cwd = the worktree's `src/voice-agent`, using the
  **tooling-root** venv python (same pattern as `_rerun_pytest`; the worktree has
  no venv of its own — see "Known gap" below).
- **Speed control (the load-bearing constraint):**
  - Coverage-guided: collect coverage once, so mutmut runs **only the tests that
    cover each mutated line**, not the whole 70 s suite per mutant. On a small
    focused change this is seconds, not minutes.
  - Hard wall-clock cap `JARVIS_AUTOMOD_MUTATION_BUDGET_S` (default **120**).
    On timeout: record partial result, **do not fail** (advisory even in
    enforce mode — a slow run is not a weak suite).
- **Score:** `killed / (killed + survived)`. Persist a `mutation` block on the
  artifact:
  ```json
  "mutation": {
    "score": 0.83, "killed": 5, "survived": 1, "timeout": 0,
    "files": ["src/voice-agent/..."], "status": "scored",
    "budget_s": 120, "elapsed_s": 18.4
  }
  ```

## Two phases

| Phase | Env | Behavior |
|---|---|---|
| **1 — advisory** (default) | `JARVIS_AUTOMOD_MUTATION_GATE=advisory` | Run, record `mutation` block, surface in the Tests tab. **Never** changes status. |
| **2 — enforce** | `JARVIS_AUTOMOD_MUTATION_GATE=enforce` + `JARVIS_AUTOMOD_MUTATION_MIN_SCORE` (default `0.7`) | `score < threshold` → `status="failed"`, `rejection_reason="mutation_survivors"`, branch deleted (reuses the existing `tests_failed_on_rerun` rejection path). |

Ship advisory, watch real scores in the Tests tab for ~a week of proposals,
then flip to enforce with a tuned threshold. (Same "observe then gate" pattern
the repo already uses for `JARVIS_CONFAB_STRICT`.)

## Edge cases (each has a defined, non-punishing answer)

- **Docs/comment-only change** (e.g. the ledger.py docstring smoke test) →
  mutmut finds **0 mutable mutants** → score is `null` → **PASS** with note
  `"no mutable code"`. A documentation change must never be penalized.
- **mutmut not installed** → skip, log a warning, record
  `mutation:{status:"skipped", reason:"mutmut missing"}`. Never breaks finalize.
- **Timeout** → record `status:"timeout"` + partial counts; advisory (no fail).
- **New file with zero covering tests** → high survivors → in **enforce** mode
  this *correctly* fails, pushing the agent to add tests (the natural hand-off to
  a future Qodo-Cover test-generation step).

## Surfacing (UI)

- `route.ts`: add `mutation` pass-through to `ProposalPayload` (it already reads
  the artifact JSON; one field).
- `evolution/page.tsx` **Tests tab** (just added): render the mutation score per
  build next to the suite result — e.g. `mutation 83% (5/6 killed)`, tinted by
  threshold. Advisory scores show as info, not failure.

## Verification

1. **Unit** `tests/test_mutation_gate.py`: a toy module + toy tests, assert
   `killed` for a covered mutation and `survived` for an uncovered one; assert
   the 0-mutant docs case returns `null`/PASS; assert the missing-mutmut skip.
2. **Advisory invariant:** run `finalize_branch` on a known green proposal with
   `MUTATION_GATE=advisory`; assert status stays `pending` and the `mutation`
   block is present.
3. **Enforce path:** with a deliberately under-tested change + `enforce`, assert
   `status=failed`, `reason=mutation_survivors`, branch deleted.
4. Full suite green: `cd src/voice-agent && .venv/bin/python -m pytest tests/`.

## Known gap this also documents (not fixed here)

The build worktree at `~/.jarvis/worktrees/<id>` has **no `.venv`**, so the
wrapper's rule-5 pre-commit `cd src/voice-agent && .venv/bin/python -m pytest`
can't run there (the agent improvises from the main repo — observed live
2026-06-23). finalize's server-side gates (pytest re-run + this mutation gate)
are unaffected because they use the tooling-root venv with the worktree as cwd.
A follow-up should point the wrapper prompt at `$TOOLING_ROOT`'s python. Tracked,
out of scope for this gate.

## Scope

```
SCOPE:  src/voice-agent/pipeline/automod/mutation_gate.py   (NEW — human-only/blocklisted)
        src/voice-agent/pipeline/automod/finalize.py         (one call between step 4 and 5)
        src/voice-agent/requirements.txt                     (+mutmut)
        src/voice-agent/tests/test_mutation_gate.py          (NEW)
        src/web/src/app/api/evolution/route.ts               (+mutation pass-through)
        src/web/src/app/(app)/evolution/page.tsx             (render score in Tests tab)
OUT:    spawner.py, _state HARD_BLOCKLIST, the agent edit scope, deploy/watchdog.
WHY OUT: this is an additive post-test gate; it must not alter what the agent
         can touch or how proposals deploy.
```

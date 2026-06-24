# JARVIS Self-Evolution Criteria Todo

Date: 2026-06-23

## Research Notes

Working definition for JARVIS: self-evolution is a bounded feedback loop where
JARVIS observes pressure from real use, proposes a variant, selects it through
fitness gates, preserves only approved survivors, and exposes the full trail for
review and rollback.

North star: JARVIS evolution is to become perfect. In engineering terms that
means asymptotic convergence toward zero repeated failures, no regressions,
lower latency, stronger truthfulness, broader capability coverage, safer
autonomy, and tighter alignment with Ulrich's corrections.

Sources:

- UC Berkeley Understanding Evolution: evolution is descent with inherited
  modification, not just any change over time.
  https://evolution.berkeley.edu/evolution-101/an-introduction-to-evolution/
- UC Berkeley Understanding Evolution: mechanisms include selection, mutation,
  genetic variation, drift, adaptation, and fitness.
  https://evolution.berkeley.edu/evolution-101/mechanisms-the-processes-of-evolution/
- Gheibi, Weyns, Quin 2021: self-adaptive systems are commonly framed around
  Monitor, Analyze, Plan, Execute feedback loops with knowledge.
  https://arxiv.org/abs/2103.04112
- Nascimento, Alencar, Cowan 2023: LLM multi-agent self-adaptation can use
  MAPE-K to monitor and adapt toward concerns of interest.
  https://arxiv.org/abs/2307.06187

## Criteria

- [x] Variation: a proposed code/prompt behavior variant exists.
- [x] Feedback: a repeated correction, telemetry signal, runtime failure, or
  explicit user request supplies pressure.
- [x] Selection: tests, diff gates, file blocklists, and review filter variants.
- [x] Inheritance: approved changes merge into source and become future baseline.
- [x] Safety: human approval, hard blocklists, watchdog health checks, and
  rollback keep self-modification reversible.
- [x] Visibility: `/evolution` shows queued, skipped, failed, and pending states.
- [x] Perfection target: every proposal carries the fitness dimensions it should
  improve, without claiming perfection before evidence.
- [x] Approval stage: human review is required for now; full autonomy is the
  target only after sustained evidence.

## JARVIS Implementation Todo

- [x] Add a code-level criteria module for the automod loop.
- [x] Stamp criteria metadata onto queued intents.
- [x] Preserve criteria metadata in finalized artifacts.
- [x] Show criteria and recent activity on `/evolution`.
- [x] Surface periodic-scan skips such as `user-active`.
- [x] Show lifecycle time frames for each evolution run: started, latest event,
  duration, event count, and final/current status.
- [ ] Add a future fitness trend graph from `evolution_ledger.db`.
- [ ] Add grouped root-cause buckets for repeated user corrections.
- [ ] Add notification polish for new reviewable proposals.

## Web Evolution Workflow

Manual mode is the default. In Manual mode, the periodic timer exits with
`manual-mode`; Ulrich drives the loop from `/evolution` with Run now or Build it.

Auto mode is explicit. Enabling Auto creates
`~/.jarvis/auto-mods/.evolution-auto`; the periodic timer may then run the same
cycle the web button runs: detect signals, run self-assessment, rank the queue
P0 -> P3, build one lineage at a time, and leave passing diffs in Review. Auto
mode still never deploys code. Deployment remains a human Review action backed
by the external watchdog and rollback marker.

Budget: at most 5 evolution builds per UTC day (`JARVIS_AUTOMOD_DAILY_CAP=5` by
default). When the cap is reached, queued/ranked work stays queued for the next
day instead of being dropped.

Failure policy: P0-P3 failures are not abandoned after a fixed attempt count.
Each failed artifact is marked retried, the same lineage is requeued with the
failure lesson and original priority, and the loop keeps narrowing the approach
across cycles/days until a functional reviewable proposal exists.

## Autonomy Ladder

Current stage: human-reviewed self-evolution. JARVIS may detect, queue, and
draft changes autonomously, but Ulrich approves deployment.

Graduation target: fully autonomous self-evolution without human review. Do not
enable until the system has evidence of:

- sustained green proposal test history
- no watchdog rollbacks over a long window
- no safety/blocklist violations
- measurable reliability, latency, truthfulness, and capability improvement
- approval history showing consistently correct changes

## Non-Negotiables

- No autonomous deploy yet.
- No edits to safety gates, watchdog, automod internals, persona source, or memory
  stores by the automod worker.
- No proposal counts as evolution unless it carries feedback, selection,
  inheritance, and safety metadata.

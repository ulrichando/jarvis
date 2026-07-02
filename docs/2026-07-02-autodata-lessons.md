# AutoData lessons applied to the evolution loop (2026-07-02)

Source: Meta FAIR's AutoData (arXiv:2606.25996, June 2026) — agentic
training-data creation with a meta-optimized scaffold. Reviewed + fact-checked
in-session; the mechanics transfer to JARVIS's automod loop even though JARVIS
trains no models. Nine lessons total: five implemented, four recorded here.

## Implemented (pipeline/automod, tests in test_automod_autodata_lessons.py)

1. **Learnability beats gap size** — `fitness_feedback.weak_axis` now prefers
   the OSCILLATING weak axis (variance = responds to change) over the lowest
   flat one; a flat-at-floor axis is flagged `flat` and its proposal demands a
   structurally different approach instead of another incremental tweak.
   (AutoData: CoT legal data had a BIGGER weak/strong gap but trained worse —
   weak scores piled at zero; their win was reshaping questions into the
   learnable band, weak-rollout std 7.93 → 12.63.)
2. **Feedback-threaded retries** — `patterns.build_retry_intent` now threads
   the stress-gate summary and the review council's block/concern findings
   into the retry brief (`GATE FEEDBACK` section), the analog of their
   `suggestion_for_challenger`. Attempt caps already existed
   (`cycle.MAX_RETRY_ATTEMPTS=2`, never-retry-blocklist).
3. **Structure evidence before reasoning** — `introspection.gather_failure_digest`
   reads failed artifacts + build-log tails into a structured digest (failure
   classes, repeated target paths, self-loop-target count, sample error lines)
   injected as `evidence["failure_digest"]`. Outcome codes alone hid patterns
   like "the builder keeps targeting blocklisted paths".
4. **Queue admission** (`patterns.queue_admission`, shared by patterns emit,
   cycle enqueue, introspection improvements):
   - **self-loop filter** — goals targeting the automod pipeline itself are
     unbuildable (HARD_BLOCKLIST) yet burned six real builds in June; they now
     route to `~/.jarvis/auto-mods/needs-human.jsonl` + an audit event.
   - **paraphrase dedup** — token-set Jaccard ≥0.6 on normalized first lines,
     same-kind only (`self_improvement`/`correction`/`fitness`/`confab`;
     `error` intents are signature-deduped upstream and share boilerplate, so
     they skip it). Exact-text dedup stays as the universal layer. Retries are
     exempt (bounded upstream).
5. **Timeout-vs-quality attribution** — a weak latency axis proposal now
   includes how many slow turns (ttfw>3s, 14d) involved a route fallback vs
   were first-try slow, plus the slow-turn models — "stopped timing out" and
   "got faster" are different work. (AutoData: 54.8% of their math-task gains
   were truncation fixes, not smarter reasoning.)

## Recorded as principles (deliberately not built)

6. **Strong solver = same model + more compute/scaffolding/privileged info.**
   Before escalating to a pricier model, retry the same model with more
   budget/tools/context; give verifiers privileged info the claimant lacked
   (the confab detector already embodies this). Relevant when touching the
   router fallback ladder.
7. **Most of meta-optimization's win was four judge-prompt rules** (positive-only
   rubrics, weight caps, structured JSON, context-leak checks) — hand-banked
   into review_council + skill_review on 2026-07-01. A live outer meta-loop
   would buy little beyond this; don't build one.
8. **Verify agent-GENERATED rubrics** ("tests reasoning, not format") if JARVIS
   ever lets an agent author its own eval rubrics. Their agent literally
   rewrote the weak solver's prompt to make it fail — rigid loop structure
   (who may edit what) is the mitigation, i.e. the HARD_BLOCKLIST design.
9. **Human co-improvement is the destination, not a waypoint.** FAIR's stated
   main direction is co-improvement with human feedback. Endorses keeping the
   human merge gate permanently; deprioritizes the graduation-to-autonomous
   ladder.

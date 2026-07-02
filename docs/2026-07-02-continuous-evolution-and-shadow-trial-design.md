# Continuous evolution + lived-experience shadow trial (2026-07-02)

## 1. What's off in the workflow (the user's instinct, confirmed)

The evolution loop is meant to be a *cycle* — always running unless switched to
manual. It isn't, for three compounding reasons found by reading the runtime:

1. **Stalled in manual.** Building is gated on `is_auto_mode()` (reads
   `~/.jarvis/auto-mods/.evolution-auto`). That file is absent, so BOTH drivers —
   the in-process `_automod_tick` (jarvis_agent.py:6310, every 2h or on an
   experience-signal bump) and the `jarvis-evolution-nightly.timer` — only
   *scan/queue*, never *build*. `JARVIS_AUTOMOD_SPAWN_LIVE=1` is set, which makes
   it look armed, but spawn-live only permits draining; auto-mode is what
   triggers it. Net: 26 queued intents, 0 built for days, four timers burning.
2. **Two drivers, one flag.** The in-process 2h loop and the systemd nightly
   timer both do the same detect→(maybe)build, both gated on the same flag —
   redundant and confusing about "who actually runs the cycle."
3. **Alive looks identical to dead.** Even in AUTO, `throttle.admit_intent`
   gates on idle (no turn 10 min), cost ($6/day), and a 60-min cooldown. So a
   perfectly healthy AUTO loop does nothing visible for long stretches — and
   there is **no heartbeat** telling you "alive, waiting: cooldown 34m, then
   builds X." Silence reads as broken.

The deepest reason it can't just be left ON continuously, though, is trust:
**there is no automatic "is this change actually better?" gate.** That's why a
human must approve every deploy. Remove that bottleneck and continuous autonomy
becomes safe. That is what the shadow trial does.

## 2. The innovation — lived-experience shadow trial (BUILT)

`src/voice-agent/pipeline/automod/shadow_trial.py` (+ 21 tests, all green).

Every self-improving-agent system validates a self-written variant on a FIXED
external benchmark — Darwin-Gödel Machine (arXiv:2505.22954), Huxley-Gödel
(2510.21614), SICA, DARWIN (2602.05848) all use SWE-bench / Polyglot. Shadow
deployment + traffic replay (MLOps) tests a new version on mirrored traffic,
separately. **This fuses them and points them at something neither uses:
JARVIS's own lived experience.**

Before a proposal earns auto-deploy, replay a curated sample of REAL recent
conversation turns (`turn_telemetry.db`) through the CHANGED decision path and
ask a judge, per turn, whether the variant's reply beats what ACTUALLY shipped.
The baseline is free — it's history; only the variant costs a call. Promote only
if it does **not regress on real conversations**.

Why it's novel: not a fixed benchmark but a **personalized, self-refreshing**
one — the agent is judged against its own life, which grows every day. Turn
selection uses AutoData's (2606.25996) insight that **boundary cases
discriminate**: a correction / confab flag / fallback / tool-error tells you far
more than a trivially-healthy turn. Proven on real data: of 500 recent turns, 53
are informative; the selector picks those (scores 6/4/4/3/3) over trivia like
"OK, start one second" (score 0).

Design principles kept from the rest of the loop: pure functions + injectable
seams (turn source, variant runner, judge) → fully unit-tested without an LLM;
never raises → `skipped` on any gap; OFF by default; conservative (ANY regression
blocks auto-promote — a human can still approve manually).

## 3. The vision — how this makes "constantly running unless manual" real + safe

Three layers, each safe-by-default and independently shippable:

- **A. Heartbeat (fixes "alive looks dead").** `_automod_tick` writes
  `heartbeat.json` each pass: `{ts, mode, gate_reason, idle_s, cooldown_left,
  budget_left, next_intent}`. The /evolution KPI band shows "Loop alive · auto ·
  waiting: cooldown 34m" instead of silence. Small, no risk.
- **B. Shadow-trial gate (BUILT core).** Wire `trial_proposal` into
  `finalize.py` after the stress gate / council, advisory first
  (`JARVIS_AUTOMOD_SHADOW_TRIAL=advisory|enforce`), surfaced on the proposal
  card next to the council verdict. In `enforce`, a `regressed` verdict routes
  the proposal back to rework; a `pass` is the green light.
- **C. Continuous autonomy budget (the unlock).** With B trustworthy, replace
  the "human approves every deploy" default with a self-allocated nightly *risk
  budget*: each candidate costs points by risk tier (prompt-only = low, code =
  high, blocklist-adjacent = ∞/blocked); the loop auto-deploys only shadow-trial
  `pass` candidates within budget, watchdog-guarded, and leaves a morning digest.
  This is "constantly running unless manual" with bounded, earned autonomy —
  matching AutoData's stated destination (human *co-improvement*, not removal).

## 4. Built vs designed (honest)

- BUILT + tested + demoed on real data: `shadow_trial.py` (selection, judge,
  trial, decision, IO builders) + `test_shadow_trial.py` (21 tests).
- DESIGNED, not yet wired (each a clean follow-on, needs your go): heartbeat;
  `finalize.py` advisory wiring; the /evolution UI verdict surface; the autonomy
  budget + mode consolidation. Wiring `finalize.py` touches the auto-mod
  HARD_BLOCKLIST (human-edit only) and going live needs a voice-agent restart
  (in-flight check first) — so it's deliberately gated behind your review.

## 5. Verification

`shadow_trial.py`: 21 unit tests green; real-data selection demo run against the
live `turn_telemetry.db` (53/500 informative; correct ranking). Full voice-agent
suite re-run after adding the module.

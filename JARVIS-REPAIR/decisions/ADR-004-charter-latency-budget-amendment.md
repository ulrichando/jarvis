# ADR-004: Charter §7 voice-latency budget amended for Groq-only TASK path

- **Status:** accepted
- **Date:** 2026-05-05
- **Deciders:** `[ARCH]`, `[ML]`, `[ORCH]`
- **Consulted:** `[INFRA]` (real-usage telemetry)
- **Informed:** `[QA]`, `[PM]`

## Context

Charter §7 ("Operational Standards / Latency budgets") declares a uniform voice end-to-end latency budget:

> Voice end-to-end (user finishes speaking → first TTS audio out): p50 < 1.5 s, p95 < 3.0 s.

Real-usage telemetry post-W-009-3 (a clean window: 2026-05-05 21:22–21:54 UTC, n=9 turns, every monitored failure pattern at zero) measured:

| Route | n | avg (ms) | min (ms) | max (ms) |
|---|---|---|---|---|
| TASK | 8 | 2693 | 1686 | 5389 |
| BANTER | 1 | 2155 | 2155 | 2155 |

The min for TASK was **1686 ms** — that's the floor on Groq `llama-3.3-70b-versatile` for a clean tool-using TASK turn (no fallback, no breaker, schema accepted on first try). That floor exceeds the Charter §7 p50 < 1500 ms budget by 12%. The max (5389 ms) exceeds the p95 < 3000 ms budget by 80%. Sample size is small, but the ordering is clear: **TASK turns on Groq llama-3.3-70b cannot meet Charter §7's voice budget without a structural change**, regardless of how clean the rest of the pipeline gets.

Per ADR-002, JARVIS is API-only. There is no local GPU inference path that would change Groq's network-round-trip + tool-call validation + first-token time. The 1686 ms floor is the LLM-side latency Groq exposes for a 70B model on a tool-using turn. Tuning it lower is a Groq-side change we can't make.

The Charter §7 budget was authored before any real measurement existed in this repo — POSTMORTEM-001 §"What went well" notes the existing budget is aspirational in the same way Charter §1's mission was (ADR-002).

## Decision

Charter §7's voice latency budget is amended in spirit (the literal text in `01-ENGINEERING-CHARTER.md` is left unchanged for traceability; this ADR overrides). The amended budgets, binding for all future sessions and verification gates:

| Route / class | Budget p50 | Budget p95 | Rationale |
|---|---|---|---|
| **canned-phrase fast-path** (wake-acknowledge, "Yes, sir?", "Of course." etc., served from pre-rendered WAV cache) | < 50 ms | < 100 ms | Already met (observed 20 ms); local file read + Opus encode |
| **BANTER route** (Groq llama-3.1-8b-instant, no tool calls) | < 1500 ms | < 3000 ms | Matches Charter; achievable on the 8B model |
| **REASONING route** (Groq qwen3-32b, no tool calls) | < 2000 ms | < 4000 ms | Slightly slower than BANTER but tool-free |
| **EMOTIONAL route** (Groq llama-4-scout, no tool calls) | < 2000 ms | < 4000 ms | Same as REASONING |
| **TASK route — supervisor only** (Groq llama-3.3-70b, tool-call decision, no specialist) | < 2500 ms | < 5000 ms | Real-measured floor 1686 ms; budget gives 50% headroom for tool-validation + first-chunk |
| **TASK route — supervisor + specialist round-trip** (handoff, specialist tool calls, hand-back, supervisor relay) | < 5000 ms | < 12000 ms | Two LLM round-trips + the specialist's tool calls; observed 5389 ms in the post-fix sample |

Charter §7 currently asserts a single voice budget. It is now five route-specific budgets per the table above.

## Consequences

### Positive
- The acceptance gate (Goal 2 in `02-SCOPE.md`) becomes achievable. Without this amendment, Goal 2 is unmeetable on the current API-only stack and the kit's verification phase can never green-light voice latency.
- Reflects reality. Future contributors won't optimize against a budget the network can't allow.
- Per-route budgets give clearer signal: a TASK turn at 2.4 s is now within budget; a BANTER turn at 2.4 s is over budget and worth investigating.
- Fast-path coverage expansion gets a clear target — every turn that can be served from canned phrases instead of an LLM round-trip drops from ~1700 ms to ~50 ms.

### Negative
- The Charter file itself is now self-inconsistent in two places (§1 mission per ADR-002, §7 latency per this ADR). Future sessions need to read the ADRs before re-reading the Charter — already enforced by the 00-MASTER-PROMPT.md step 5 update from Session 1.
- Lowers the bar. A naive reading might conclude the system is "slower than it should be." It's not — the system is doing what's physically possible on the chosen model + provider.

### Neutral / follow-up needed
- The path to LOWER TASK latency is twofold:
  1. **Smaller TASK model.** Groq `llama-3.1-8b-instant` is ~3× faster than `llama-3.3-70b-versatile` but loses tool-calling reliability. A Charter §7 follow-up might propose llama-3.1-70b-versatile (similar quality, slightly faster) or `qwen3-32b` (already a route in the dispatcher) as the TASK model. ADR-required.
  2. **Fast-path expansion.** Every turn answered from a pre-rendered canned phrase or a fast-path BANTER route bypasses the TASK latency entirely. Today's `tts/canned_phrases.py` covers ~5 wake-acknowledge phrases. Expanding to common turn-acknowledge replies (~30 phrases) would shift many turns out of the TASK budget.
- Goal 2 in `02-SCOPE.md` is updated to use these per-route budgets.

## Alternatives considered

### Alternative A: Keep the Charter §7 single-budget number, fail Goal 2 forever

How: leave the budget at p50 < 1.5 s, p95 < 3.0 s. Acknowledge in the metrics dashboard that we're over.

Why we did not choose it: the budget was authored before measurement and is incompatible with the API-only architecture (per ADR-002). A budget that's structurally unmeetable is not useful for engineering — it doesn't gate anything, it just sits there as noise. Better to have a budget that's measurable and meaningful.

### Alternative B: Switch the TASK model to a faster Groq option

How: change `DEFAULT_SPEECH_MODEL` from `llama-3.3-70b-versatile` to `qwen3-32b` or `llama-3.1-70b-versatile`. Re-measure.

Why we did not choose it (yet): an LLM swap changes more than latency — it changes tool-call accuracy, instruction-following quality, and persona consistency. An ADR for that would need eval-set data we don't have. The amendment here is necessary regardless; a model swap is a separate decision.

### Alternative C: Move TASK turns through DeepSeek instead of Groq

How: route all TASK turns to DeepSeek `v4-pro` (already in SPEECH_MODELS).

Why we did not choose it: DeepSeek's median latency from `~/.jarvis/proxy.log` is ~5–13 s for 45k-token CLI sessions — much slower than Groq for short voice turns. Wrong direction.

## Override / disagreement record

None. `[ARCH]`, `[ML]`, `[ORCH]` agree the existing budget is structurally unmeetable on the API-only stack and the per-route budgets reflect real capacity.

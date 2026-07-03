# Computer-use design review — plans

Advisor review of JARVIS's `computer_use` tool (voice path + web `/computer-use` sidecar)
against Anthropic's current computer-use guidance
(`platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool`, fetched
2026-07-02) and the "best practices for computer and browser use" resource.

**Written against commit:** `8bede503`.
Isolated in `advisor-plans/` (not `docs/plans/`) so it does not collide with the unrelated
audit already numbered there.

## Verdict

The design is fundamentally sound and largely tracks current best practice: SOM
element-mode targeting (coordinate-free, provider-uniform), auto-capture after every
mutating action, `region` re-capture for small text, keyboard-over-fiddly-clicks and
verify-each-step guidance in the tool prompt, a three-layer safety model, redacted audit
trail, and — on the **voice** path — model-aware screenshot downscaling with a coordinate
scale-note. The gaps are on the **web sidecar** path and are cost/robustness/security
polish, not a broken core.

## Plans (execute in this order)

| # | Plan | Category | Effort | Risk | Confidence |
|---|------|----------|--------|------|------------|
| 001 | [Sidecar history trim splits Anthropic tool_use/tool_result pairs → 400](001-cu-sidecar-history-trim-orphan-tool-result.md) | Correctness | S | LOW | MED-HIGH |
| 002 | [Anthropic CU step: prompt caching + effort + in-run image cap](002-cu-anthropic-adapter-caching-effort-image-cap.md) | Performance/cost | M | MED | HIGH (caching/images), MED (effort) |

No dependency between them; 001 is the higher-priority (a live wedge on long sessions), 002
is the larger cost win. Both are self-contained and testable without a service restart.

## Findings surfaced in the review but NOT yet turned into plans

(See the review summary for detail; each can become a plan on request.)

- **Sidecar approval/session state never resets** (`_APPROVED_KINDS` / `_SESSIONS` in
  `computer_use_service.py`): "approve for session" grants persist for the sidecar process
  lifetime, keyed by a client-supplied `session_id` that defaults to `"default"` → a later
  run can inherit a prior grant; dicts also grow unbounded. The voice tool has
  `reset_session_approval()`; the sidecar has no equivalent. Security + leak, MED.
- **Sidecar screenshots lack per-frame untrusted-content framing / injection scan** that the
  voice path has (`_SCREEN_INJECTION_RE` + "UNTRUSTED screen content" label). The sidecar is
  the higher injection-exposure surface (it drives the browser) yet feeds raw frames back
  with only a one-line system-prompt caution. Security, MED.
- **Direction — native `computer_20251124` for the Anthropic path:** the custom SOM tool is
  a deliberate multi-provider choice, but it forgoes Anthropic's server-side screenshot
  prompt-injection classifier, the model's native training on the tool, and native
  `zoom`/2576px hi-res coordinate accuracy. Worth weighing a per-provider native path for
  Claude while keeping the custom tool for OpenAI/Gemini.
- **Sidecar downscales to a hardcoded 1280px**, ignoring the model's real ceiling (Opus
  4.7/4.8 read up to 2576px). Minor small-text quality gap, mitigated by `region` recapture.
- **Doc-truth:** `CLAUDE.md` still says the voice `computer_use` uses "Anthropic's
  `computer_20251124` tool surface" — it is a custom SOM tool. `CLAUDE.md` is on the
  auto-mod hard blocklist / load-bearing, so the user must amend it; the runbook
  (`docs/runbook/computer-use.md`) already documents the truth.

---

# Whole-system subtraction review (2026-07-02)

Separate, broader review — full JARVIS system design, not just computer-use.
Planned against commit `9861fd11`. Maintainer confirmed direction: **JARVIS is a
single-user personal assistant**, so every plan below biases toward *removing
surface*, not adding it. Numbering continues from the computer-use plans above to
keep this directory's index monotonic.

## Plans (independent; recommended order by leverage-per-risk)

| # | Plan | Category | Effort | Risk |
|---|------|----------|--------|------|
| 003 | [Delete the dead `handoff_text` sanitizer](003-delete-dead-handoff-text-sanitizer.md) | tech-debt | S | LOW | **DONE** (3850 passed, 3 skipped) |
| 004 | [Extract the Android app to its own repo](004-extract-android-to-own-repo.md) | tech-debt | M | LOW |
| 005 | [Shelve the evolution / automod loop](005-shelve-evolution-automod-loop.md) | tech-debt | L | MED |
| 006 | [First `jarvis_agent.py` extraction wave (thinking-heartbeat cluster)](006-jarvis-agent-extraction-wave-session-handlers.md) | tech-debt | L | MED-HIGH |

All four are independent. Do 003 first (10-minute clean win), then 004 (removes
~40% of the repo's file count at near-zero risk to daily-used code), then 005
(biggest cut but spans voice/systemd/bin/web/docs — needs the full voice + web
suites), then 006 last and alone (hardest, least reversible, needs a live smoke).

## Direction context (applies to 003–006)

- Single-user personal assistant. No multi-user / "product" hardening required —
  when a plan offers "keep for future product use" vs "cut it," cut it.
- The accepted **mic→root** risk (passwordless sudo, recorded in `CLAUDE.local.md`)
  STAYS — documented decision, out of scope.

## Whole-system findings considered and rejected (so nobody re-audits them)

- **Gut `pycall`**: most of it is LIVE (generic tool-call-as-text leak guard);
  only `handoff_text` is fully dead (→ 003). Optional one-token cleanup folded
  into 003's Step 4.
- **Drop honcho self-hosted memory**: valid but it's an ops decision
  (`docker compose down` at `~/honcho` + unset `JARVIS_MEMORY_PROVIDER`), not a
  code plan. File-backed memory already covers recall.
- **Collapse the 5-provider LLM cascade**: documented design decision (prompt-cache
  latency). Auditing actually-used providers is investigation, not a mechanical
  plan — revisit under `improve next`.

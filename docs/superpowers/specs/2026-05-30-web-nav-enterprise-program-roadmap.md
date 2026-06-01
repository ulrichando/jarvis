# JARVIS Web-Navigation — Enterprise Upgrade Program (Roadmap + Phase 1 design)

**Date:** 2026-05-30
**Status:** proposed (design) — awaiting review
**Shape:** a multi-phase PROGRAM, decomposed. This doc holds the roadmap (P1/P2/P3 + target
architecture) **and the fully-specified Phase 1**. P2/P3 get their own spec→plan→build when reached.
**Research basis:** `docs/superpowers/` landscape survey 2026-05-30 (browser-use / Stagehand /
Skyvern / Playwright-MCP / Anthropic+Gemini computer-use / Laminar / WebVoyager / ST-WebAgentBench).

## Key insight (why this is tractable)

JARVIS already runs the **best open-source web agent** — `browser-use` (~89.1% WebVoyager, the
current OSS leader; Skyvern 2.0 ~85.8%, Stagehand ~75%). The problem is not the framework, it's that
JARVIS **under-uses** it and routes web work to the wrong tool:

1. **Routing fork** — the model can't tell `computer_use` (drives *visible* X11 Chrome by raw pixels,
   blind after each click) from `browser_task` (*headless* browser-use, DOM-aware, reports back), so
   it blind-clicks web tasks that browser-use would do reliably. (Confirmed in code + by the frontier
   "tool-priority ladder": API > structured browser > raw clicking-as-last-resort.)
2. **`runner.py` ignores browser-use's reliability/security knobs** — no `fallback_llm`, `max_failures`,
   `use_vision`, `sensitive_data`, `allowed_domains`, `calculate_cost`; text-summary-only return.
3. **No web observability or eval** — `browser_task` returns only final text; there's no measured
   notion of "navigates well."

**Migration is NOT the answer** (browser-use is the leader). The work is: route correctly, turn on the
knobs, close the `computer_use` loop, observe + evaluate + secure.

## Target architecture (north star)

Keep `browser-use` as the `browser_task` core and make the **structured CDP/DOM path the preferred
route for web work**, with raw pixel-clicking demoted to last resort (Anthropic's tool ladder). Close
the `computer_use` loop by feeding the post-action screenshot back to the vision-capable supervisor as
a multimodal `tool_result` (the one deferred piece — plumbing, not a new model). Harden `runner.py`
with the params it ignores (`use_vision='auto'`, `max_failures`, timeouts, `fallback_llm` cascade,
`sensitive_data` redaction, `allowed_domains`) — **verified against the installed browser-use 0.12.6,
not the newer docs.** Make it observable (per-step traces into `turn_telemetry.db`; Laminar in the
browser venv) and measurable (a WebVoyager-style screenshot LLM-judge as the web analog of the voice
rubric). Add an AT-SPI2 + Set-of-Marks backend for *native* GUI element precision (live-verified
available on this box) while routing in-page web precision through CDP. Add TOCTOU re-verification +
HITL confirmation before irreversible actions — the highest-value safety adds given this box's
blanket-root.

## Phases (each = its own spec → plan → build)

### Phase 1 — Routing + reliability + observability  *(this doc specifies it; build first)*
The fast, low-risk, high-coverage wins. No new deps, no model changes, no re-architecture. See the
**Phase 1 design** section below.

### Phase 2 — Capability (the nav-quality jump)  *(own spec later)*
- **Close the `computer_use` vision-feedback loop** — feed the post-action screenshot back as a
  multimodal `tool_result` to the supervisor (`computer_use.py` docstring L26-38 + shaping fn ~L528
  already flag this as the missing piece). Add recent-action history alongside (Gemini's refinement).
  *Highest-leverage capability change.*
- **AT-SPI2 backend + Set-of-Marks** in `computer_use_backend.py` — walk `Atspi.get_desktop(0)`, filter
  actionable roles, return role+name+SCREEN extents (exact coords, no pixel-guessing) for **native
  GTK/Qt apps**; overlay numbered marks on the screenshot. Fix the stale "no a11y tree" comments
  (`computer_use.py:125,159`, `computer_use_backend.py:16,346`). *Honest limit: Chrome does NOT expose
  in-page web content to AT-SPI without `--force-renderer-accessibility`; for web, route through CDP.*
- **Bounded self-verification** — after a mutating action, re-capture + confirm the expected change
  before claiming success (Anthropic's "screenshot+evaluate"), with a formal step/retry cap +
  Sonnet→Opus escalation; dovetails with the confab-detector.

### Phase 3 — Enterprise hardening  *(own spec later)*
- **Eval harness** — WebVoyager-style screenshot LLM-judge (task + final screenshots → temp-0 binary
  pass/fail; ~85% human agreement) over recorded `browser_task`/`computer_use` scenarios, tracked over
  time + a CuP-style "completed-without-policy-violation" metric. The web analog of the voice rubric.
- **Security** — TOCTOU re-verify before blind clicks; `sensitive_data` credential redaction (value
  never enters prompt/trace); `allowed_domains` allow/deny (mirror `JARVIS_XAI_ALLOWED_DOMAINS`); HITL
  confirmation before irreversible actions (delete/send/pay/post); re-evaluate `chromium_sandbox=False`
  given root.
- **Cost/latency + caching** — `calculate_cost` into telemetry; a Stagehand-style act/observe **cache**
  for repeated voice flows ("open my email") so they replay without a full LLM loop (pattern, not the TS lib).

## Honest caveats / non-goals
- **Do NOT migrate off browser-use** (no payoff — it's the leader). **Skyvern** = selective vision-first
  *fallback* for canvas/DOM-hostile sites only (AGPL + heavy server → not a base). **Playwright-MCP** =
  mine its a11y-snapshot *pattern*, don't add the dependency.
- Benchmark numbers (89/86/75) are vendor/blog-reported — directional, not precise.
- **Verify every browser-use param against the installed 0.12.6**, not the docs (newer release;
  `fallback_llm`/`sensitive_data` shapes may differ or be absent). Trust the package.
- The multimodal `tool_result` plumbing (P2) is "easy in concept, fiddly in practice" — budget for it.
- None of this makes JARVIS a *reliable general* web agent on arbitrary sites; it makes it reliable on
  common flows, measured, and safe. Be honest about that ceiling.

---

# Phase 1 design — Routing + reliability + observability

**Scope:** `bin/jarvis-gemini-tools`, `bin/jarvis-gpt-tools`, `src/voice-agent/prompts/supervisor.md`,
`src/voice-agent/tools/browser.py`, `src/voice-agent/tools/computer_use.py` (schema/description only),
`src/voice-agent/browser_use_bridge/runner.py`, `src/voice-agent/pipeline/turn_telemetry.py` (+ a new
telemetry table). **No model changes, no new deps, no re-architecture.**

### P1.A — Fix the routing fork (prompt/schema only; highest-frequency win)
- Rewrite `browser_task`'s schema description to **lead with "headless / invisible / browses in the
  background and reports back"** + 2-3 concrete exemplars ("check the top HN stories", "find the price
  on nvidia.com and report back"). (`tools/browser.py` description.)
- Add a **"When NOT to use"** line to `computer_use`'s schema: "for web lookups/navigation with no
  on-screen requirement, prefer `browser_task`." (`tools/computer_use.py` description.)
- Inject the **tool-priority ladder + visible-vs-headless routing rules** into the direct-mode
  (Gemini/GPT) system prompts, and **delete the contradictory "computer_use is a web fallback" line**
  in `jarvis-gpt-tools`. Source the rules from `supervisor.md` (add/clarify the ladder there too).
- **Acceptance:** "open YouTube on my screen" → `computer_use`; "check the top Hacker News stories" →
  `browser_task`; "find X on my browser and tell me" → `browser_task`. Verify in both supervisor and
  direct modes.

### P1.B — Harden `browser_task` via browser-use's own knobs (verify vs 0.12.6 FIRST)
In `runner.py` (the only place that constructs the `browser_use.Agent`), wire the params it currently
ignores — **but first introspect the installed 0.12.6 to confirm each exists / its shape**
(`~/.jarvis/browser-use-venv/bin/python -c "import browser_use, inspect; ..."`); skip/adapt any absent:
- `use_vision='auto'` (vision only when the DOM index fails — matches JARVIS's instinct + saves tokens).
- `max_failures` + `llm_timeout`/`step_timeout` (bounded, no infinite loops / silent hangs).
- `fallback_llm` cascade (give the browser path the provider cascade the voice path already has;
  Anthropic→OpenAI→Google by available key).
- `calculate_cost` (record per-task $ — feeds P3 cost telemetry).
- **Adaptive `max_steps`** in `browser.py::_handle_browser_task`: scale ~15 (lookup) → ~50 (multi-page
  flow) from the task string, keeping the explicit override. Fixes silent under-budget failures.
- **Task validation**: reject/repair a destination-less task before spawning the subprocess.
- **Legible failures**: on failure, return the **stderr tail** (browser-use's discarded step log) in
  the tool result instead of a generic "Browser task failed" (`runner.py` stderr redirect + `browser.py`
  error path).

### P1.C — Observability: surface the step trace
- `runner.py` already returns `{ok, result, steps}` — extend to emit a **per-step action log**
  (action + target + optional screenshot path), and surface it from `browser.py` into a new
  `browser_task_steps` table in `turn_telemetry.db` (mirroring the `computer_use_actions` pattern), so
  failures are debuggable post-mortem. (Full Laminar span-replay is deferred to P3 — keep P1 dependency-free.)

### Testability / verification (P1)
- Unit-test the pure bits: adaptive-`max_steps` heuristic, task-validation, the routing-rule text
  presence. `py_compile` the bins.
- A 0.12.6 **param-introspection** step (a tiny script asserting which params exist) gates P1.B so we
  never pass an unsupported kwarg.
- Full voice-agent `pytest` stays green.
- **Live:** the three routing phrasings route correctly; a real `browser_task` ("find the price of X on
  nvidia.com and report back") completes and returns a useful summary with a step trace in telemetry.

### Risks (P1)
- **Param drift vs docs** — mitigated by the mandatory 0.12.6 introspection gate.
- **Routing regression** — mitigated by the 3-phrasing acceptance check in both modes.
- **`anthropic_strict_schema` patch** — P1.A changes description STRINGS only, not schema shapes, so the
  load-bearing schema patch is untouched.

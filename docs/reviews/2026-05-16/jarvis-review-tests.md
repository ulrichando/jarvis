# JARVIS Test Infrastructure — QA / SRE Review (Round 2)

**Date:** 2026-05-16 · **Scope:** `/home/ulrich/Documents/Projects/jarvis`
**Author hat:** QA / SRE engineer · **Previous round:** found ~800 tests, claimed "no CI"

---

## TL;DR — top 5

1. **Round 1 was wrong about "no CI."** Three workflows DO exist: `.github/workflows/voice-agent-tests.yml`, `desktop-tauri-smoke.yml`, `security-audit.yml`. Round 1 missed them. The CI is actually fairly sensible — what's missing is lint/format on Python, type checking, and a check that the Stop hook will block what CI blocks.
2. **Round 1 also undercounted tests by a wide margin.** Pytest collects **1518 tests** in `src/voice-agent/`, not ~800. Plus ~100 in `src/web/` (vitest). Real number of test cases across the repo is ~1620.
3. **Master is RED right now.** Two tests fail when run locally with the documented command:
   - `tests/test_voice_fixes_2026_05_04.py::test_vad_prewarm_uses_production_thresholds` (test pins 0.5/0.25, code has 0.6/0.3 — the source was retuned without updating the test)
   - `tests/test_screen_share_subagent.py::TestLiveConfigShape::test_realtime_model_called_with_full_config` (imports `livekit.plugins.google`; missing from voice-agent venv)
   And **6 hub tests fail at collection time** because `fakeredis` is not in `requirements.txt`. CI won't catch most of this because its env diverges from local (DUMMY env vars, but at least `livekit-plugins-google` is implicitly pulled by `livekit-agents[silero,openai,groq,anthropic]~=1.5` — and even then, *no marker tests fakeredis*). **P0.**
4. **The CI ignore-list references two non-existent files** (`test_browser_ext_contract.py`, `test_supervisor_vision.py`). They were renamed/deleted but the workflow still passes `--ignore=` for ghosts. Harmless but a smell. The third ignored file (`test_github_subagent.py`) DOES exist and is correctly skipped.
5. **No integration / e2e test exists.** Every "integration" test (`test_pipeline_integration.py`, `test_subagents_health.py`, `test_turn_graph.py`) is a pure-Python unit test with `MagicMock` stubs. The actual happy path — fake LiveKit room → real STT → real-ish LLM → DispatchingTTS → audio sink — is not exercised in CI. The 25-second suite time is suspicious only because nothing is end-to-end.

---

## 1. Test coverage map

Full breakdown of the 114 test files in `src/voice-agent/tests/` (1067 raw test-functions, 1518 pytest cases after parametrize expansion):

| Feature group | # files | # tests (raw) | What's NOT tested |
|---|---|---|---|
| **Sanitizers** (`pycall`, `dsml`, `tool_name`, `deepseek_roundtrip`, `strict_schema_relax`, `anthropic_strict_schema`, `internal_phrase`, `denial_detector`, `truncation_gate`, `stt_garbage_gate`, `short_input_gate`, `sir_cap`, `handoff_text` implicit) | 13 | 137 | No fuzz / hypothesis tests on regex sanitizers; no test for monkey-patch idempotency across re-imports |
| **Subagents** (`browser_subagent`, `browser_pre_transfer`, `browser_ext_*`, `screen_share_*`, `code_reviewer`, `validator`, `github`, `subagents_health`, `subagent_registry`, `subagent_isolation`, `subagent_bailout`, `subagent_env_aliasing`, `pre_transfer_hook`) | 14 | 138 | No end-to-end "supervisor decides → handoff → subagent runs a real tool → task_done" sequence. Most tests stub the LLM and verify schema/prompt shape. |
| **Pipeline / routing** (`turn_router`, `turn_graph`, `turn_telemetry`, `dispatching_llm`, `dispatching_tts`, `intent_router`, `recall_router`, `banter_fast_path`, `reasoning_fast_path`, `pipeline_integration`) | 10 | 117 | No load test on classifier (latency under load), no real-LLM smoke against router prompt to verify it still emits one of 4 valid labels |
| **Memory layer** (`memory_layer`, `memory_extractor`, `memory_recall`, `memory_consolidator_2026_05_08`, `memory_anchor`, `recall_consumer`, `chat_ctx_recall_truncate`, `extractor_meta_paraphrase_2026_05_08`, `token_prune_2026_05_08`, `token_estimation`) | 10 | 121 | Cross-process memory hub event flow not tested (extractor → hub event → SSE consumer); confab-detector tests live separately |
| **Resilience** (`circuit_breaker`, `breaker_shims`, `breaker_status_block`, `reconnect_ladder`, `track_guard`, `watchdog`, `watchdog_stall_diagnostics`, `silence_fix`, `barge_in_truncation_e2e`, `acoustic_tap`) | 10 | 50 | No chaos test — kill provider mid-stream, verify breaker → fallback → recovery. Watchdog tests are unit-level on the diagnostic dump, not the systemd notify ping flow |
| **Direct tools / computer-use** (`direct_tools_and_plan_mode`, `bash_internals`, `computer_use`, `tasks`, `launch_app`, `capture_trigger`, `code_search`, `skill_runner`, `skills_loader`, `get_location`, `worktree`, `hooks`, `monitor`, `ask_user_question`) | 14 | 215 | No sandbox-escape / privilege-boundary test on the bash tool (especially salient given JARVIS has `NOPASSWD`); plan-mode side-effects not tested cross-tool |
| **Evolution / self-learning** (`evolution_*` × 17, `learned_rules_v2`, `jarvis_rules_cli`) | 19 | 88 | Golden-eval set is only 50 lines and is tested with a stubbed `_render_with_rules` and `_judge_quality` — neither real LLM nor real rule renderer is in the loop |
| **Hub / IPC** (`hub_client_*`, `hub_consume`, `hub_memory_*`, `hub_migrate`, `hub_schema`, `hub_settings_*`, `hub_core_sync`) | 14 | 56 | 6 of 14 fail at COLLECTION because `fakeredis` isn't installed. The hub-side Redis Streams events:conversation flow only "works" if `fakeredis` is added to deps. |
| **Confab detector** (`confab_detector`, `confab_extractor_evidence_2026_05_08`) | 2 | 33 | Good coverage of tool-evidence lookback; no test of cross-session false-confab where supervisor's `chat_ctx` was just pruned |
| **Provider integration** (`deepseek_roundtrip`, `anthropic_strict_schema`, `strict_schema_relax`, `tool_name_sanitizer`, `vision_backend_dispatch`) | 5 | 69 | All mocked — no live API contract test. When Groq/DeepSeek/Anthropic change their schema requirements, CI cannot catch it. |
| **Voice fixes / regression markers** (`voice_fixes_2026_05_04`, `tts_position_table`) | 2 | 15 | The "regression marker" naming convention is good — but only 2 such marker files exist for ~16 documented regression-fix dates in CLAUDE.md |
| **Imports / smoke** (`imports`) | 1 | 6 | Verifies top-level imports don't crash — minimal but cheap |

**Total: 114 files · 1067 raw test functions · 1518 collected tests · ~20 seconds wall-clock.**

The coverage by area looks GOOD — every major load-bearing subsystem from CLAUDE.md has at least one dedicated suite. The qualitative gaps are at the boundaries: no real-LLM contract tests, no chaos / fault-injection, no cross-tree (voice→hub→web) flow.

---

## 2. Test types audit — unit vs integration vs e2e

| Type | Count (approx) | Examples |
|---|---|---|
| Unit (pure-function, mocked deps) | ~1450 | `test_sir_cap.py`, `test_pycall_sanitizer.py`, `test_turn_router.py` |
| "Integration" (multiple modules wired, but mocked I/O) | ~50 | `test_pipeline_integration.py` (Dispatching LLM + TTS + router with MagicMock inners), `test_subagents_health.py` (registry + tool shape), `test_turn_graph.py` |
| Smoke (import / config) | ~6 | `test_imports.py` |
| True e2e (network, services, real APIs) | **0** | — |
| Property / fuzz | **0** | — |
| Load / latency | **0** | — |

`test_pipeline_integration.py` is the closest thing to an integration test. It runs 30 fixture turns through `detect_emotion → classify_turn → DispatchingLLM.pick → DispatchingTTS.pick` — but with stubbed Groq, stubbed LLM/TTS inners. There is no test that actually:

- spins up a fake LiveKit room (livekit-server is installable in CI),
- feeds a wav file through Silero VAD + Whisper,
- gates through the full agent pipeline with at least ONE real LLM call,
- asserts the audio sink receives a non-empty PCM stream within an SLO.

**Cost to add a baseline e2e:**

- Spin up `livekit-server` container in a CI job (free image, ~30s startup).
- Use `python-livekit`'s `Room.connect` from a test client; agent worker connects too.
- Stub LLM with a local FastAPI mock that returns canned chat-completions.
- Stub TTS with a 1-second silent WAV writer.
- Assert: `data_received` event fires, turn-row written to a tmp telemetry DB, agent terminates cleanly.

Effort: **2-3 days** to author + harden. Runs in ~60-90 seconds per case. Catches: configuration drift, monkey-patch ordering, livekit-agents version skews, dispatcher wiring regressions. Value: **high** — every reported live failure (2026-05-15 first-install, the screen-share crash, the heartbeat race) would have surfaced in a baseline e2e before reaching the user.

**Recommendation: P1.** Don't gate PRs on it (flaky early on), but run nightly on `main`.

---

## 3. CI absence — REVISED, CI exists, design gaps

Round 1's "no CI" claim is **wrong**. Three workflows live in `.github/workflows/`:

| Workflow | Trigger | What it runs | Time | Issues |
|---|---|---|---|---|
| `voice-agent-tests.yml` | push/PR touching `src/voice-agent/**` | pytest with 3 hard ignores | ~2 min incl. setup | Stale ignore list (2 files don't exist); no lint; no coverage; passes when local fails |
| `desktop-tauri-smoke.yml` | push/PR touching `src/voice-agent/desktop-tauri/**` | `npm ci`, `npm run build`, verify `dist/index.html`, `cargo check --locked`, conditional `cargo test` | ~5-8 min | Does `cargo check`, NOT `cargo build --release` — won't catch link-time or codegen regressions on release profile. The conditional `cargo test` is good. |
| `security-audit.yml` | push/PR + Mon 14:00 UTC | `pip-audit`, `npm audit cli`, `npm audit web`, `cargo audit` | ~3 min | Solid. Pin exception (`PYSEC-2025-49`/`CVE-2024-6345`) is justified and documented inline. |

### What's genuinely missing

| Gap | Severity |
|---|---|
| No Python lint (ruff/black). `requirements.txt` doesn't even include ruff/black. | P2 |
| No mypy / pyright. ~5300-line `jarvis_agent.py` has no static-type check. | P2 |
| No CI guard on `src/web/` — vitest tests exist (~100 cases) and `eslint` script defined, but NO workflow runs them. | P0 — actual blind spot |
| No CI guard on `src/cli/` (TypeScript/Bun). Round 1 noted off-limits, but at minimum a typecheck would help. | P2 |
| Voice-agent CI doesn't include `fakeredis` so the 6 hub tests collected locally are not even visible — meaning CI silently green when hub-flow is broken. | P0 |
| No coverage reporting (`--cov`). Cannot answer "did this PR test the lines it touched?" | P2 |
| No integration / e2e workflow (nightly). | P1 |
| `desktop-tauri-smoke.yml` doesn't run `cargo build --release` — the documented release-flow gotcha. | P1 |

### Proposed CI design (additive, cost-aware)

GitHub Actions free tier on a public-or-private repo gives 2000 minutes/month for private. Three workflows run ~10 min/PR; let's target the new design at **<15 min/PR** to keep budget headroom for nightly.

```yaml
# .github/workflows/lint.yml — add Python + JS lint
on: [pull_request]
jobs:
  voice-agent-lint:
    if: changes('src/voice-agent/**')
    runs: pip install ruff && ruff check src/voice-agent/ && ruff format --check
  web-lint:
    if: changes('src/web/**')
    runs: cd src/web && npm ci && npm run lint
```

```yaml
# .github/workflows/web-tests.yml — close the src/web/ blind spot
on:
  push:
    branches: [main, master]
    paths: ["src/web/**"]
  pull_request:
    branches: [main, master]
    paths: ["src/web/**"]
jobs:
  vitest:
    runs-on: ubuntu-latest
    steps:
      - actions/checkout@v4
      - actions/setup-node@v4 (node 20, cache npm)
      - working-directory: src/web; npm ci; npm run test
```

```yaml
# .github/workflows/nightly-e2e.yml — true e2e against fake LiveKit
on:
  schedule: [{cron: '0 6 * * *'}]   # 06:00 UTC daily
  workflow_dispatch: {}
jobs:
  voice-agent-e2e:
    runs-on: ubuntu-latest
    services:
      livekit:
        image: livekit/livekit-server:latest
        ports: [7880:7880]
        options: --health-cmd "curl -f http://localhost:7880/" 
    steps:
      - actions/checkout@v4
      - setup python 3.13 + system deps (libsndfile1, ffmpeg)
      - pip install -r src/voice-agent/requirements.txt
      - pip install pytest pytest-asyncio fakeredis
      - run: cd src/voice-agent && pytest tests/e2e/ -m e2e --timeout=120
```

```yaml
# Edit .github/workflows/desktop-tauri-smoke.yml — add release build
- name: cargo build --release (release-profile drift check)
  working-directory: src/voice-agent/desktop-tauri/src-tauri
  run: cargo build --release --locked
  timeout-minutes: 20
```

**Provider:** GitHub Actions free tier covers all of this comfortably. Linux runners only — no need for macOS (Tauri targets Linux for this project per the AI-native OS rice). **Estimated monthly cost: ~0 USD** on the free tier; if private repo hits the cap, switch to self-hosted runner on the same Kali box (cost: machine power, ~1 USD/month electricity).

---

## 4. Stop hook (`verify-before-done.sh`)

Read at `/home/ulrich/Documents/Projects/jarvis/.claude/hooks/verify-before-done.sh`. Key behaviors:

| Behavior | Status |
|---|---|
| Reads transcript JSONL, extracts Edit/Write/MultiEdit file paths | OK |
| Classifies by subtree (`/src/voice-agent/`, `/src/voice-agent/desktop-tauri/`, `/src/web/`, `/src/cli/`) | OK |
| Voice-agent: `pytest tests/ -x --tb=line --no-header` | OK |
| Desktop-tauri: **`npm run build` ONLY — NO `cargo build --release`** | **GAP** |
| Web: `npm run test` (vitest) | OK |
| CLI: warns but does not block | OK (intentional) |
| Recursion guard via `STOP_HOOK_ACTIVE` | OK |
| Escape hatch `JARVIS_SKIP_VERIFY=1` | OK |
| Prereq check (`test -x .venv/bin/python`, `test -d node_modules`) — skips suite if missing | Documented but: a missing prereq returns exit 0 with WARN — work claims "done" with no verification. Subtle gap. |
| Output `{decision: "block", reason: …}` JSON when any suite fails | OK |

### Gaps for "12-month deadline" workflow

1. **`cargo build --release` is documented as required for desktop-tauri release, but the hook only runs `npm run build`.** This is the exact failure mode CLAUDE.md and `regression-prevention.md` both warn about ("the user has been bitten by this before"). The hook contradicts its own documentation. **P0 fix.**
2. **Prereq fallback is silent.** If `.venv/bin/python` doesn't exist (fresh checkout, container rebuild, accidentally deleted), the hook says "WARN: prereq missing — skipping" and exits 0. A more honest contract: WARN + still exit 0 is fine for partial work, but the Stop hook should emit a `{decision: "ask", reason: "Verification skipped — prereq missing"}` so the user is reminded.
3. **No retry on flake.** Pytest with `-x` stops at first failure. If a test is flaky, the hook block looks like a real failure. Add `--reruns 1` (pytest-rerunfailures) for known-flaky tests via marker.
4. **No timeout.** A hung test would hang the Stop hook indefinitely. Add `timeout 300 bash -c "..."`.
5. **No CLI suite hook** (intentional per CLAUDE.md "off-limits" rule), but CLI changes happen and the warn-only is the right call.
6. **Web suite path correctness:** `test -x src/web/node_modules/.bin/vitest` — but vitest binary path in node 20 is at `node_modules/vitest/dist/vitest.mjs` for the JS wrapper or `.bin/vitest` (shell). If the user runs npm 10 with `package-lock.json` v3, this prereq holds. Worth a quick sanity check.

---

## 5. Flaky tests — query

Did a fresh local run with the CI command. Results:

- **2 outright failures** (deterministic, not flaky):
  - `test_vad_prewarm_uses_production_thresholds` — tests assert 0.5/0.25, source has 0.6/0.3. The VAD was retuned (the test_voice_fixes file is dated 2026-05-04; live retune is presumably more recent). Either the test is stale (most likely — the file is a "regression marker" pinning that-day's values) or the source regressed. Given `voice-agent.md` says "VAD threshold tuned 2026-05-04 to fix 'first turn missed'. Don't loosen it" and the source has *higher* values (0.6 > 0.5) — the source LOOSENED. Worth Ulrich's direct review. **P0.**
  - `test_realtime_model_called_with_full_config` — `livekit.plugins.google` not installed. The plugin is part of the gemini realtime path. Either: (a) plugin is now optional, (b) requirements.txt missing it, (c) it was recently removed. Test should `pytest.importorskip("livekit.plugins.google")` if optional. **P0.**

- **6 collection-time errors** on `test_hub_*` modules (all want `fakeredis`). The fix is one line: `pip install fakeredis` in CI and a test-requirements file. **P0.**

- **Suppression / skip review:** 4 files use `@pytest.mark.skipif` / `pytest.skip` — `test_github_subagent.py` (gh-cli auth check, reasonable), `test_voice_fixes_2026_05_04.py` (env-gated), `test_screen_share_subagent.py` (env-gated), `test_subagents_health.py` (gates by `JARVIS_SUBAGENT_*`). All skips are environment-conditional, not "this test is flaky." **No suppressed flakes detected.** Good hygiene.

- **No `pytest-rerunfailures` in requirements** — there's no retry mechanism for transient failures. Adding it is cheap insurance if/when network-touching tests grow.

---

## 6. Test infrastructure quality

`src/voice-agent/tests/conftest.py` is **31 lines**, doing two things:

1. **`_isolate_evolution_changelog` (autouse)** — redirects `pipeline.evolution.changelog.CHANGELOG_DIR` to `tmp_path` so evolution-tier tests don't pollute `~/Documents/jarvis-evolution/`. Good hermetic guarantee.
2. **`pytest_configure` env defaults** — sets `JARVIS_SUBAGENT_*=1` for all 7 gated-off subagents (so tests can verify their registration regardless of production default), and `JARVIS_MEMORY_CONSOLIDATOR=0` (avoid background asyncio leak across tests).

**Strengths:**
- Hermetic file-system isolation via `tmp_path`.
- Documented rationale for env overrides (with date references — auditable).
- Idempotent: tests that need to flip something monkeypatch within their own scope.

**Weaknesses:**
- **No HTTP / network sandbox.** Tests rely on individual modules to mock `httpx`/`openai`. If a test forgets, it WILL hit the live API (CI has DUMMY keys so it'll 401, but locally the user is paying for unintended calls). A repo-wide `responses` / `respx` autouse fixture that blocks all outbound HTTP unless explicitly allowed would be safer.
- **No clock control.** Tests using `time.time()` / `datetime.now()` are not pinned. Several telemetry / consolidator tests use `monkeypatch.setattr` ad-hoc; a global `freezegun` fixture would simplify.
- **No async-task leak detector.** The consolidator's "background task" is exactly the kind of thing that leaks; the fix in conftest (`JARVIS_MEMORY_CONSOLIDATOR=0` by default) is reactive. An asyncio-task-counter fixture catching dangling tasks at test exit would prevent the next instance of this category.
- **No fixture for a "minimal AgentSession"** that all the subagent / pipeline tests could share. Every test that needs an `AgentSession` builds its own with `MagicMock`, leading to subtle drift in what shape the mock has.

**Mock-LLM availability:** there's no dedicated `MockLLM` class. `MagicMock` is used inline. The pattern works but is brittle to changes in the `livekit.agents.llm.LLM` ABC.

---

## 7. Soak / eval pipeline

### `bin/jarvis-soak-rescore.sh`

Reads `~/.local/share/jarvis/turn_telemetry.db`. Produces a stdout report covering:

- Standard `turn_telemetry.py --report --days 1` output.
- Ack-opener distribution (first 30 chars of `jarvis_text`).
- "Sir-frequency" — `% of replies containing 'sir'` (regression marker after the 2026-05-09 butler-register removal).
- Per-binary `launch_app` outcomes (OK/MISSING/CRASHED).
- Interrupt rate per route.

Ends with **human verdict guidance** — does NOT auto-bump scores. Manual. Not CI-bound. **Operational tool**, not gate.

### `bin/jarvis-evolution-eval.sh`

Runs the golden-eval against the current loaded rules (`anchor + core + accepted + staged` tiers). Writes `~/.jarvis/evolution_golden_report.<date>.json`. Says it runs from a "systemd timer or cron at 06:00 local" — **but I cannot find that timer.** `find /home/ulrich -name "*.timer" -maxdepth 5` returns 0 results (excluding node_modules). The script may be wired but the timer unit is **not deployed**. The comment is aspirational.

The script invokes `pipeline.evolution.golden_eval.run()`. The set is at `tests/golden_evolution_canonical.jsonl` — **50 items**. Categories: `signature_reflex` (exact-match), `persona_invariant` (judge-rubric), and others. Real LLM is called via `_render_with_rules` (production path); a judge LLM is called via `_judge_quality`. So this **is** a real-loop eval — not a stubbed one (unlike the test file `test_evolution_golden_eval.py` which stubs both for unit testing).

### `bin/jarvis-evolution-soak-check.sh`

Day-N sanity check for the 7-day soak: counts `would_stage` events, warns on any actual STAGED rules while LOGGING_ONLY, prints last 5 evolution-log entries, scans for evolution-tagged log warnings, service health, recent autonomous-transitions. Writes `~/.jarvis/soak-check-<date>.txt`. Aspirationally **invoked by `jarvis-evolution-soak-check.timer` at 09:00 local each day** — same caveat as eval: timer absent on this machine.

### `bin/jarvis-rubric-rescore.sh`

The most sophisticated. Re-scores per-axis verdicts against live telemetry, auto-commits a verification section to `docs/superpowers/specs/2026-04-30-voice-intelligence-rubric.md`. **It auto-commits** — has `git commit -m` baked in. Dry-run flag exists. Run cadence is manual.

**Verdict on soak/eval:** the scripts are well-engineered and exist as **operational tools, not CI gates**. None of them are wired into `.github/workflows/`. None are scheduled by deployed systemd timers (the timer units are referenced in comments but don't exist on disk on this machine — could be that they're per-machine and never committed). The "Phase 7+ verification" rubric updates are produced manually when the user runs the script.

**Improvement:** add the eval scripts to CI as nightly jobs:
- `nightly-rubric-rescore.yml` — runs on a sampled DB checked into a private artifact bucket, posts rubric delta as PR comment.
- `nightly-golden-eval.yml` — runs against the 50-item golden set with the live LLM stack, fails the workflow if `signature_reflex_pass_rate < 0.95` or `judge_pass_rate < 0.85` (thresholds from `golden_eval.promotion_eligible`).

---

## 8. Voice intelligence rubric

Documented at `docs/superpowers/specs/2026-04-30-voice-intelligence-rubric.md` (638 lines). The 10 axes are:

1. Streaming TTS / TTFW (≤1000ms target)
2. Emotion detection
3. Turn routing (BANTER/TASK/REASONING/EMOTIONAL classifier)
4. LLM dispatch by route
5. Voice swap by route
6. Acknowledgment vocabulary
7. Interruption handling
8. Conversation memory & continuity
9. Tool execution discipline
10. Self-eval / closed loop

Score scale: 0-3 missing/broken, 4-6 functional with gaps, 7-8 solid, 9-10 parity with Claude AI voice. Iteration log appends a dated section each loop-iteration with score delta + outcome.

**Source of the ~95/100 score:** the doc is **manually updated** by `bin/jarvis-rubric-rescore.sh` (which appends a verification section + auto-commits) and by `/loop` iterations that run during user/agent sessions. Round 1's claim of "auto-generated" is not quite right — it's *semi-auto*, requiring the operator to invoke the script.

**Axes documented:** Yes, in the file linked above. Each axis has a one-line definition plus its scoring history.

**Improvement opportunities:**
- The rubric is currently NOT in CI. A nightly job could run `jarvis-rubric-rescore.sh --dry-run` against last-24h telemetry, compute the score, and post a delta if it changed by >1 in any axis.
- The 50-item golden eval should drive at least 1 of the 10 axes (probably axis 6 ack-vocab + axis 9 tool exec). Currently the golden set is independent of the rubric.

---

## 9. Mutation / property testing

JARVIS has **zero property-based tests**. `hypothesis` is not in `requirements.txt`. The repo would benefit *most* in these areas:

| Subsystem | Why property tests help | Expected value | Cost |
|---|---|---|---|
| **Sanitizers** (`pycall`, `dsml`, `tool_name`, `internal_phrase`) | These are regex-heavy text transforms with many edge cases. Hypothesis can generate adversarial strings (Unicode, nested function calls, partial markdown, escaped JSON). The current tests cover known regressions; properties catch the next one. | HIGH | ~2 days |
| **Route classifier** (`turn_router::classify_turn`) | Property: for any input, output is in `{BANTER, TASK, REASONING, EMOTIONAL}` or fallback. Property: latency under timeout-ms threshold. Catches "router LLM returns malformed label" silently. | HIGH | ~1 day |
| **Memory extractor** | Property: extracted memory passes the `_META_PARAPHRASE_RE` filter (since the live filter regression — narration shapes hitting the store — has happened). Property: total length bounded. | MEDIUM | ~1 day |
| **Token-aware pruning** | Property: post-prune token estimate ≤ budget. Property: system prompt always preserved. Property: tool-call/output pairs always preserved together. | HIGH (since the existing implementation already drops oldest pairs, but invariants aren't tested as properties) | ~1 day |
| **Anthropic strict schema** | Property: after sanitizer, every object schema has `additionalProperties: false`. Catches regressions in the post-build patch. | MEDIUM | ~0.5 day |

**Total cost ~5-6 days** for a meaningful property-test suite covering 5 most-leakable surfaces. **Recommendation: P1**. Highest payoff is on sanitizers — they're the load-bearing monkey-patches CLAUDE.md flags as "must not be removed", and current tests are example-based.

**Mutation testing** (`mutmut`, `cosmic-ray`) is lower priority. The codebase has too much I/O + async for mutation testing to be cheap; the signal-to-noise ratio would be low. Defer.

---

## 10. Build verification gaps — release-profile drift

`.claude/rules/regression-prevention.md` § 5 (the spec the Stop hook implements) says:

> Desktop-tauri edit → `npm run build` (vite, ~7s; catches syntax + import errors). For release, ALSO `cargo build --release` (re-embeds dist into binary; CLAUDE.md rule).

But the **Stop hook only runs `npm run build`**. The cargo step is documented as required but **not enforced**. CLAUDE.md says the user has been bitten by this:

> For desktop Tauri release builds: `npm run build` alone does NOT ship JS changes. You must `cargo build --release` afterward to re-embed `dist/` into the binary.

CI's `desktop-tauri-smoke.yml` runs `cargo check --locked` (compile-check only, debug profile) — also not `cargo build --release`. **Both layers skip the actual release-profile build.**

**Cost of fixing:** `cargo build --release --locked` on this codebase takes ~3-5 minutes with cache, ~15 minutes cold. Run only on push-to-main + workflow_dispatch (not every PR — too slow). Add a Stop-hook-level lighter check that at least runs `cargo check --release` (which catches release-profile cfg drift without doing codegen — ~30s incremental).

**Recommendation: P0 for CI on main, P1 for Stop hook.**

---

## Severity-tagged action list

### P0 — fix this week

1. **Master is red** — fix the 2 failing tests (`test_vad_prewarm_uses_production_thresholds`, `test_realtime_model_called_with_full_config`). Either update the test to match the new VAD thresholds (and bump the rule in `voice-agent.md`) or revert the source change; for the screen-share test, `pytest.importorskip("livekit.plugins.google")` if the plugin is now optional.
2. **Add `fakeredis` to requirements** so the 6 hub tests don't fail at collection. Local CI gap that CI doesn't even see.
3. **Add `src/web/` to a CI workflow.** Vitest tests exist (~100 cases), eslint script defined, but no workflow runs them. Pure blind spot.
4. **Make the Stop hook run `cargo build --release` (or at least `cargo check --release`)** when desktop-tauri files are edited. The hook contradicts its own rule and the user has been bitten before.
5. **Clean the CI ignore-list.** Remove `--ignore=tests/test_browser_ext_contract.py` and `--ignore=tests/test_supervisor_vision.py` — both files don't exist.

### P1 — fix this month

6. **Add a baseline e2e workflow** (nightly): fake LiveKit + stubbed LLM + 1-turn happy path. 2-3 days of authoring; high catch rate for live-config drift.
7. **Add a `cargo build --release` job** to `desktop-tauri-smoke.yml` (on push to main, not PRs — too slow for PR gating).
8. **Add `pip install fakeredis pytest-rerunfailures pytest-timeout` to CI** for retry / timeout protection.
9. **Wire the golden-eval into CI nightly** with the thresholds from `golden_eval.promotion_eligible` as the pass/fail gate.
10. **Property tests for sanitizers + token-aware pruning + route classifier** using hypothesis (~5-6 days).
11. **Schedule the soak-check + rubric-rescore via systemd timer units** (commit `setup/systemd/*.timer` so they install on `make install`).

### P2 — quality of life

12. **Add `ruff` + `ruff format --check` workflow** for Python; **add `mypy --install-types --non-interactive`** for static-type coverage on `src/voice-agent/`.
13. **Add a repo-wide `httpx`/`responses` autouse fixture in conftest** that blocks outbound network unless explicitly allowed. Currently relies on every test author remembering to mock.
14. **Add coverage reporting (`pytest --cov`)** and publish to a Codecov-style service. Gate at >85% diff coverage for PRs touching `src/voice-agent/sanitizers/` or `src/voice-agent/pipeline/`.
15. **Add a `make verify` target** that runs the full Stop-hook logic from the CLI so the user can dry-run it without re-architecting the hook.
16. **Document the "no `.timer` units on disk" gap** — either commit the timer units or remove the aspirational comments from the soak/eval scripts.
17. **Add a "regression marker" test convention page** — only 2 of 16 documented regression-fix dates have marker files; canonicalize the pattern.

---

## Bottom line

JARVIS's test infrastructure is **better than Round 1 reported**: CI exists, 1518 collected tests, hermetic conftest, dedicated tooling for soak/eval/rubric. The real problems are:

- **Two-day-fresh master is red and CI hides it** (different env, missing dep, stale ignore list).
- **The Stop hook contradicts its own documented release rule** (cargo build skipped).
- **No e2e**, **no property tests**, **no web-CI** despite tests existing.

These are tractable. A focused week on P0+P1 (≈1 week of QA work) would put the suite in genuinely defensible shape for the 12-month-deadline workflow the user runs. The 95/100 rubric score is plausible if measured against Claude voice mode parity, but the *test infrastructure* feeding into that score is closer to 75/100 — one major missed integration ("desktop-tauri release flow") and one CI-vs-local divergence away from a Friday-evening regression.

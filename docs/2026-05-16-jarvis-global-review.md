# JARVIS global review — 2026-05-16

10-domain in-depth review by a parallel team of 10 specialist reviewers (5 round 1 + 5 round 2 deep-dives). Source-of-truth source reviews live in [`docs/reviews/2026-05-16/`](reviews/2026-05-16/) — this doc is the navigable index, executive summary, and 12-month prioritized roadmap.

Total source material: 10 reviews, ~43k words, ~250 distinct findings, ~80 file:line-precise actions.

---

## Executive summary

**JARVIS is a top-decile-quality voice assistant codebase carrying load-bearing technical debt in five areas.** The Python voice-agent has 1,518 tests at ~20s wall-clock, decent CI, structured JSON logs, named regression-fix dates in code comments, and ~13k lines outside the 5,478-line `jarvis_agent.py` monolith. The desktop-tauri is clean Tauri 2.x + minimal-CSP + correct two-step release flow. **What's broken or wrong is concentrated and fixable**, not pervasive.

The ten reviewers agreed on these five **load-bearing bugs that should be fixed first**:

1. **Bridge security is broken in two layers** — `JARVIS_REQUIRE_LOCAL_AUTH` is unset everywhere; any local process or malicious web page can mint LiveKit JWTs, hijack the OpenAI model, and execute Chrome-extension actions (set_cookies, executeScript, debugger CDP) on every authenticated tab. CORS is `*`. (Bridge + Ops reviews)
2. **CLAUDE.md is wrong about three load-bearing facts**: `jarvis-bridge.service` doesn't exist (bridge lives in `src/cli/src/bridge/server.ts`); `/etc/sudoers.d/jarvis` doesn't exist (`sudo -n` requires password); both contradict downstream code that assumes they do. Either the units/files get restored OR the docs + 6 dependent files (CLI status check, SessionStart hook, evolution soak check, runbook, README) get fixed to match reality. (Bridge + Ops reviews)
3. **The memory store is polluted with garbage.** 20+ of 99 live memories are `Coding Kiddos`-prefixed fictional narration — the few-shot examples in the extractor prompt are 100% Coding Kiddos/Pretva, so the 8B extractor mimics surface form on background TV audio. Two-source `JARVIS_MEMORY_TOP_N` default divergence (8 vs 30) means 30 polluted memories seed every supervisor prompt. (Memory review)
4. **Every turn ships ~76k input tokens.** `prompts/supervisor.md` is 133,718 bytes (~34k tokens). Anthropic prompt caching is *configured* but never *verified* — telemetry shows zero `cost_usd` for Anthropic turns. The 2.5s unexplained TTFW slack on llama-70b is mostly Groq's TTFB at 76k input tokens. **Compressing supervisor.md to a 5-10k cacheable core + 25k dynamic tail is the single highest-leverage latency win.** (AI + Perf reviews)
5. **The supervisor LLM dispatcher is silently disabled when any model is pinned.** `DEFAULT_SPEECH_MODEL="gpt-5-mini"` (`providers/llm.py:84`); `user_pinned_llm = active_speech_id != DEFAULT_SPEECH_MODEL` (`jarvis_agent.py:3774`) forces every BANTER/TASK/REASONING/EMOTIONAL turn through the pinned model. Pinning `llama-3.3-70b` (today) defeats both BANTER's fast-path AND REASONING's accuracy-path. (AI + Router reviews)

The rest of this doc routes findings into a quarter-by-quarter roadmap. **Q1 = stop the bleeding** (P0s above). **Q2 = structural** (file splits, sanitizer cleanup, breaker exception classification). **Q3-Q4 = quality + scale** (real e2e tests, prompt-caching infrastructure, semantic recall, performance instrumentation).

---

## Critical P0 findings (fix this week — total ~3-4 days of work)

| # | Finding | Files | Source review |
|---|---|---|---|
| 1 | Restore bridge auth: `JARVIS_REQUIRE_LOCAL_AUTH=1` in `start-desktop.sh`, generate `~/.jarvis/local-api-token.env` at install, wire to Tauri React + extension + voice-agent | `src/cli/scripts/start-desktop.sh`, `src/cli/src/bridge/server.ts`, `install.sh`, `src/voice-agent/tools/_browser_ext_base.py` | bridge §P0 |
| 2 | Tighten CORS — drop `*`, allowlist `tauri://localhost`, `app://localhost`, `chrome-extension://<id>` | `src/cli/src/bridge/server.ts` | bridge §P0 |
| 3 | Auth `/ws` upgrade + `extension_hello` message (today anyone can register as the extension and intercept ext_browse) | `src/cli/src/bridge/server.ts` | bridge §P0 |
| 4 | `chmod 600 .env` + `.jarvis/*.env` at install time | `install.sh` | ops §P0 |
| 5 | Update CLAUDE.md to remove the `jarvis-bridge.service` lie OR create the unit and move the bridge to `src/desktop-bridge/` | `CLAUDE.md`, `README.md`, `docs/runbook/jarvis-voice.md`, `bin/jarvis-evolution-soak-check.sh`, `src/cli/src/commands/voice/status.ts`, `.claude/hooks/SessionStart.sh` | bridge §7, ops §1 |
| 6 | Reconcile sudo NOPASSWD claim: either restore `/etc/sudoers.d/jarvis` with scoped `Cmnd_Alias` OR remove the assumption from `bash` tool docstring + add `NoNewPrivileges=yes` to units | `/etc/sudoers.d/jarvis`, `~/.config/systemd/user/jarvis-*.service`, `src/voice-agent/tools/bash.py` | ops §P0 |
| 7 | Wipe ~20 `Coding Kiddos` fictional-narration rows from `state.db` AND rewrite memory_extractor few-shot examples with diverse subjects (no shared proper noun) | `src/voice-agent/pipeline/memory_extractor.py`, `~/.jarvis/hub/state.db` | memory §P0 |
| 8 | Unify `JARVIS_MEMORY_TOP_N` default to **8** in both `pipeline/config.py:242` and `tools/memory.py:393` | both | memory §P0 |
| 9 | Wire `memory_auto_extracted=True` into `log_turn` from `on_user_turn_completed` | `src/voice-agent/jarvis_agent.py`, `src/voice-agent/pipeline/turn_telemetry.py` | memory §P0 |
| 10 | **Non-Latin script gate on STT** — drop transcripts where >50% of alphabetic codepoints are non-Latin AND length <12 chars. Kills the Russian/Japanese/Chinese background-TV hallucinations (~9% of turns) | `src/voice-agent/pipeline/stt_gate.py:55-76` | ai §P0-1 |
| 11 | **Drop `deepseek-v4-pro`** from `SPEECH_MODELS` and the fallback cascade (66 of 200 recent turns, 22 took >30s, three >700s with hallucinated Bosnian replies) | `src/voice-agent/providers/llm.py:145-153, 749-755` | ai §P0-3 |
| 12 | Reconcile `DEFAULT_SPEECH_MODEL` / pin-disables-dispatcher logic — either change default to `llama-3.3-70b-versatile` OR redesign pinning to override SLOW route only while keeping BANTER + REASONING fast-paths | `src/voice-agent/providers/llm.py:84`, `src/voice-agent/jarvis_agent.py:3774` | ai §P0-2, router §P1 |
| 13 | Fix the 2 failing tests on master (VAD threshold drift `test_vad_prewarm_uses_production_thresholds`, missing `livekit.plugins.google`) AND add `fakeredis` to `requirements-test.txt` so 6 hub tests stop erroring at collection | `src/voice-agent/tests/test_voice_agent_smoke.py`, `requirements-test.txt`, CI ignore list | tests §P0 |
| 14 | Stop hook must run `cargo check --release` for desktop-tauri edits (it currently only runs `npm run build`, contradicting its own documented rule) | `.claude/hooks/verify-before-done.sh` | tests §P0 |
| 15 | **TTFW telemetry is poisoned** by zombie `_jarvis_turn_start_monotonic` (max values >1M ms). Clear after each `log_turn` AND clamp >60s to NULL on write | `src/voice-agent/pipeline/turn_dispatcher.py`, `src/voice-agent/pipeline/turn_telemetry.py` | router §P0 |
| 16 | Circuit breaker treats Groq 429 (rate-limit) as a real failure. Add `non_failure_classifier` to `CircuitBreaker.__init__`; promote the validation-error revert from `BreakeredLLMStream` (`providers/llm.py:530-547`) into the breaker | `src/voice-agent/resilience/circuit_breaker.py:94` | code §P0 |
| 17 | Audit & enable Anthropic prompt caching — `caching="ephemeral"` is set at `providers/llm.py:241-249` but telemetry shows zero `cost_usd` on Anthropic turns. Capture `cache_read_input_tokens` and confirm livekit-plugins-anthropic 1.5.8 honors the kwarg | `src/voice-agent/providers/llm.py:241-249`, `pipeline/turn_telemetry.py` | perf §P0 |
| 18 | **Pre-flight cache** in `BreakeredGroqLLM.chat` — chat_ctx stringification at lines 585-621 is ~200-500ms blocking per turn at 80-turn ctx + 134k-byte system prompt. Cache by `(id(chat_ctx), len(items), id(items[-1]))` | `src/voice-agent/providers/llm.py:585-621` | perf §P0 |
| 19 | TWO `bash` tools registered — registration order decides which wins. Pick one (modern one in `tools/bash.py`) and delete the legacy `jarvis_agent.py:1890::bash` 37-line version | `src/voice-agent/jarvis_agent.py:1890`, `src/voice-agent/tools/bash.py` | ops §P1 (escalate to P0 given threat) |
| 20 | Speak the truth on `listening` indicator — it's driven by raw RMS (`jarvis_voice_client.py:632-641`) not by Silero VAD. Either subscribe to a VAD-state LiveKit topic emitted by the agent, OR bump LISTENING_RMS_THRESHOLD higher and document the tradeoff | `src/voice-agent/jarvis_voice_client.py:632-641`, `src/voice-agent/jarvis_agent.py` (add `session.on("user_state_changed")` publisher) | audio §P1 |

---

## 12-month roadmap

### Q1 (weeks 1-4) — STOP THE BLEEDING

**Theme:** all 20 P0s above. Plus the highest-impact Q1 P1s:

- **P1: Compress `prompts/supervisor.md`** from 34k → 17-18k tokens. Collapse SUBSTANTIVE ENGAGEMENT + TECHNICAL DEPTH + NO-PREAMBLE + TASK BREVITY. Merge dual FEW-SHOT EXEMPLARS. Footnote past failures to a separate `prompts/regressions.md`. *Cost: 1-2 days. Value: ~50% faster prefill + 50% TPM headroom.*
- **P1: Mid-stream LLM stall watchdog** — extend `BreakeredLLMStream` to track inter-chunk timing; raise `APITimeoutError` if gap >`JARVIS_LLM_CHUNK_TIMEOUT_S=5`. Catches the >700s turns the breaker doesn't catch today (`providers/llm.py:463-565`).
- **P1: Output-language sanitizer** — new `src/voice-agent/sanitizers/output_language.py`. Drop reply if >30% non-Latin alphabetic AND not preceded by user turn in same language. Catches turn 160's Bosnian reply.
- **P1: AEC** — load PipeWire `module-echo-cancel` via `~/.config/pipewire/pipewire.conf.d/echo-cancel.conf`; add `JARVIS_AUDIO_{INPUT,OUTPUT}_DEVICE={mic,sink}_aec` to `.env`. Disable `JARVIS_APM_AGC=0` (AGC is amplifying room tone above VAD deactivation threshold).
- **P1: STT hallucination defenses** — add `prompt="The user is speaking English to a voice assistant named Jarvis."`, `temperature=0`, `no_speech_threshold=0.8`, `condition_on_previous_text=False` to the Groq Whisper call (`providers/stt.py:69`).
- **P1: Property-test sanitizers + pruning** — 5 highest-value targets cost ~5-6 days, prevents an entire class of regressions.
- **P1: 8 µs-precision timing columns** on `turns` table — `t_vad_end_us`, `t_stt_final_us`, `t_preflight_us`, `t_llm_ttfb_us`, `t_llm_first_text_us`, `t_tts_ttfb_us`, `sanitizer_us`, `chat_ctx_items`. Wire via existing `stamp_first_token` + `_on_agent_state` hooks. Without this, every other latency claim is hand-wavy.

**Q1 success criterion:** TTFW p50 ≤ 1.0 s on `gpt-5-mini` or `claude-haiku-4.5` (achievable per perf review, requires Anthropic caching + pre-flight cache + supervisor.md compression). Zero non-Latin STT hallucinations in 7-day soak window. Master branch tests green.

### Q2 (weeks 5-8) — STRUCTURAL

**Theme:** modularize `jarvis_agent.py` (5,478 lines → ~3,500), unify duplicated infra, ship the deferred test infrastructure.

- **`jarvis_agent.py` refactor (P1 deep refactor):** extract @function_tool defs (~1,750 lines → `tools/voice_tools.py`), TTS text-transform filters (~400 lines → `pipeline/tts_filters.py`), silent-mode + mute + wake (~170 lines → `pipeline/silent_mode.py`), tool-busy state (~80 lines → `pipeline/tray_status.py`). Net: ~2,400 lines extracted, ~3,100 in entrypoint.
- **`_handler` closure refactor (router §P1)** — 485-line `# noqa: C901` closure in `turn_dispatcher.py` collapses to ~200 lines via shared helpers. Extract `_inject_route_prefix` + `_swap_llm_tts`. Race between regex-stamp and graph-output for `_jarvis_route` becomes deterministic.
- **`main.rs` module split (desktop §P2)** — 2,090 lines → `tray_icon.rs` / `keys.rs` / `web_probe.rs` / `tray_menu.rs` / `hotspot_poll.rs` + thin `main.rs`.
- **Tray ✓-marker consistency (desktop §P1)** — Speech & Tool submenus get the same ✓-marker pattern as TTS. ~40 lines per submenu using the proven label-sync infra.
- **Move bridge to `src/desktop-bridge/`** — half-day of git mv + systemd unit + auth wiring. Closes the namespace collision with the CLI's separate "remote-bridge".
- **CI improvements (tests §P0/P1)** — `lint.yml` (ruff/black/mypy for Python, npm run lint for web), `web-tests.yml` (vitest), `nightly-e2e.yml` (livekit/livekit-server container + fake LLM + golden eval), `cargo build --release --locked` on push-to-main.
- **Bailout-phrase regex shared module** (ai §P1-6) — extract from `subagents/agent.py:46-77` + `sanitizers/internal_phrase.py:45+` to one shared module.
- **Subagent gate audit + reconcile with CLAUDE.md** (ai §P1-3) — `weather`/`researcher` are default-on, contradicting docs.

**Q2 success criterion:** `jarvis_agent.py` < 3,500 lines. Single source-of-truth for bailout phrases. CI green on every PR. Lint clean.

### Q3 (weeks 9-26) — QUALITY + DEPTH

**Theme:** real e2e tests, semantic memory recall, performance instrumentation pipeline, evolution-eval automation.

- **e2e test harness (tests §P1)** — fake LiveKit server container + stubbed LLM HTTP server + WAV-file STT input + assert audio sink writes PCM. ~2-3 days author cost, ~60-90 seconds per case. Would have caught every recent live failure (the realtime-mode episode, the breaker-stuck episode, the Whisper hallucination episode).
- **Semantic recall** (memory §P1) — current recall is pure SQLite LIKE substring match. Add an embeddings index (Weaviate is already in `.env`) for paraphrased queries. Cost: ~1 week, including embedding-on-write + recall-on-query + cache invalidation on consolidator runs.
- **Per-component TTFW dashboard** — wire the 8 µs timing columns into a real-time dashboard (Grafana + SQLite-via-promtail, or stdout-streamed to a TUI). Round 2 perf review provides concrete `--breakdown` SQL queries.
- **Evolution-eval automation** — golden-eval pipeline (`bin/jarvis-evolution-eval.sh`) exists but the systemd timer doesn't. Deploy the timer; auto-post deltas as a CI check-status; auto-revert on regression past `promotion_eligible` threshold.
- **Voice intelligence rubric automation** (tests §P2) — nightly `jarvis-rubric-rescore.sh --dry-run` against last-24h telemetry, post deltas to repo.
- **Token-budget pruner that doesn't stringify** (perf §P2) — move from full chat_ctx concat to per-message tokenization cache. Eliminates the last residual ~50-100ms pre-flight cost.

**Q3 success criterion:** 1 nightly e2e run, 1 user-asked recall query of "what did I tell you about X?" succeeds for paraphrased X, voice rubric score generated automatically per night.

### Q4 (weeks 27-52) — SCALE + UX

**Theme:** memory taxonomy + UI for user control, prompt-caching across providers, dispatcher generalization, OS rice clarification.

- **Memory UI** — current memory store has no user-facing list/delete/edit interface. Add a chat-panel sidebar that surfaces memories grouped by type (user/feedback/project/reference per the Claude Code MEMORY.md pattern) with a "forget this" button. Auto-expire `reference` memories at 30 days; never expire `user`.
- **Multi-provider prompt caching** — Groq has explicit caching support now too; the supervisor.md split-cacheable-core proposed in Q1 also works on Groq. Verify cache_read_tokens telemetry across all providers.
- **Dispatcher generalization** — currently 4 routes (BANTER/TASK/REASONING/EMOTIONAL). Several recent live failures point to a 5th: `MEMORY` (questions about prior turns) and possibly a 6th: `EXTENDED_THINKING` (complex multi-step requests that should burn 30s of reasoning budget). Validate against telemetry.
- **OS rice (Misty Scone) — decide its fate** — `src/os/desktop/` is referenced in CLAUDE.md as a separate Arch rice that copies cli + desktop-tauri. Round 1 ops review found the directory doesn't exist. Either build it or remove the references.
- **Memory expiration / TTL + cross-session contradiction detection** (memory §future) — `user has a background in medical psychology` was extracted in one session; a contradicting fact would conflict silently today.
- **Hardware refresh budget** — perf review shows i7-7600U (2C/4T, 2017) saturates at ~40% on one core during voice turns. A 6-core upgrade buys ~300ms TTFW. Compare to software wins; if Q1+Q2 deliver 1.0s p50 then upgrade is deferrable.

**Q4 success criterion:** "Forget what I said about X" works end-to-end. Memory store < 200 rows with < 5% stale.

---

## Per-domain findings index

Each row links to the source review. The "headline" column captures the most novel finding from that review — i.e. what wasn't already in CLAUDE.md or anyone's mental model.

| Domain | Source | Headline finding |
|---|---|---|
| AI engineering | [reviews/2026-05-16/jarvis-review-ai.md](reviews/2026-05-16/jarvis-review-ai.md) | DeepSeek path is fundamentally broken (76s mean TTFW vs 1.2s on gpt-5.1); supervisor.md sections SUBSTANTIVE ENGAGEMENT + TASK BREVITY + NO-PREAMBLE teach the same lesson 3× across ~3.3k tokens; non-Latin STT bleed-through is the dominant hallucination class |
| Voice-agent code quality | [jarvis-review-code.md](reviews/2026-05-16/jarvis-review-code.md) | Circuit breaker treats 429 as failure; `BreakeredLLMStream` already has the right exception-classification logic but it's hand-rolled — promote into the breaker. 5 orphan `asyncio.create_task` sites in `jarvis_agent.py` |
| Audio pipeline | [jarvis-review-audio.md](reviews/2026-05-16/jarvis-review-audio.md) | AEC is OFF everywhere; the "tray indicator stuck cyan" is because the listening flag is driven by RMS not VAD (architectural mismatch); Silero on 2C/4T is at its hardware budget but defensible |
| Desktop-Tauri | [jarvis-review-desktop.md](reviews/2026-05-16/jarvis-review-desktop.md) | One orphan event emit (`tray-toggle-screen-share`) with no JS listener; Speech & Tool submenus don't have ✓-markers like TTS does; `main.rs` is 2090 lines and ripe for a module split |
| Ops + security | [jarvis-review-ops.md](reviews/2026-05-16/jarvis-review-ops.md) | `/etc/sudoers.d/jarvis` doesn't exist — the entire bash-tool threat model assumes it does; .env files are world-readable with 22 prod API keys; TWO bash tools registered (order-dependent footgun) |
| Bridge / hub / Chrome ext | [jarvis-review-bridge.md](reviews/2026-05-16/jarvis-review-bridge.md) | The bridge has zero auth in current launcher config; CORS is `*`; any web page can mint LiveKit JWTs and join the room as 'jarvis-agent'. Chrome ext has `<all_urls>` + `cookies` + `debugger` permissions — full account takeover blast radius |
| Memory pipeline | [jarvis-review-memory.md](reviews/2026-05-16/jarvis-review-memory.md) | 20+ of 99 memories are `Coding Kiddos` fictional narration (extractor mimics few-shot surface form); `memory_auto_extracted` telemetry is dead code; two-source `JARVIS_MEMORY_TOP_N` divergence (8 vs 30) |
| Turn router + classifier | [jarvis-review-router.md](reviews/2026-05-16/jarvis-review-router.md) | REASONING starvation is a CLASSIFIER failure (only 1 hit / 144 turns); pin-disables-dispatcher logic is too broad (pinning Haiku for cost loses BANTER fast-path); TTFW telemetry is poisoned by zombie >1M ms values |
| Test infrastructure | [jarvis-review-tests.md](reviews/2026-05-16/jarvis-review-tests.md) | Round 1 was wrong — 3 CI workflows DO exist; but master is RED right now (2 deterministic failures + 6 hub-tests collection-error); Stop hook contradicts its own documented release rule (runs `npm run build` only, not `cargo build --release`) |
| Performance profiling | [jarvis-review-perf.md](reviews/2026-05-16/jarvis-review-perf.md) | Every turn ships ~76k input tokens; Anthropic caching is configured but never verified to hit; pre-flight stringification is ~200-500ms blocking; on 2C/4T i7-7600U the pre-flight cache buys more (~500ms) than a 6-core upgrade would (~300ms) |

---

## Anti-recommendations — load-bearing constraints across all 10 reviews

**Don't touch these. They're load-bearing per CLAUDE.md and have live-failure history.**

1. The four mandatory sanitizer monkey-patches: `deepseek_roundtrip`, `tool_name_sanitizer`, `AcousticTap`, `anthropic_strict_schema`. Plus `strict_schema_relax`, `pycall`, `dsml`, `handoff_text`.
2. Subagent tool gate's narrow `_BAILOUT_SUMMARY_RE` allowlist + 3-strike force-bail.
3. `resume_false_interruption = False` (LiveKit's `pause()` is broken on SFU output).
4. `handoff_text_suppressor` walks the FULL chat_ctx, not last 15.
5. Confab-detector tool-evidence lookback = 10; `transfer_to_*`/`delegate` count as evidence.
6. VAD `activation_threshold=0.6` / `min_silence_duration=0.4` (don't go below 0.5; live failure).
7. TASK `min_words=3` (don't drop to 2; live failure with backchannels).
8. Bare "Jarvis" → "Yes?" canonical answer.
9. No `Co-Authored-By` trailers; no "Generated with Claude Code" attribution.
10. `src/cli/` is off-limits when working on voice-agent / desktop / web.
11. `src/cli/src/utils/claudeInChrome/` is reserved.
12. Don't reintroduce the voice reactor sphere (`useVoiceClient.js:68` `audioLevel = 0` is intentional).
13. Don't bump `RECENT_TURNS_LIMIT` past 12 in `pipeline/chat_ctx.py`.
14. Don't auto-clear silent mode on agent restart.
15. Don't hardcode any LLM provider as primary.
16. Don't reintroduce the "sir" suffix to subagent `ack_phrase`.
17. Don't restart `jarvis-voice-agent.service` while a session is active (check `~/.local/share/jarvis/turn_telemetry.db` `ts_utc` within 60s — operational rule).
18. Don't skip `cargo build --release` when shipping JS changes to desktop-tauri.
19. Don't delete `voice_session_within_60s` from `main.rs` despite `#[allow(dead_code)]`.

---

## Risk-ranked threat model (consolidated from ops + bridge reviews)

| Threat | Probability | Impact | Reachable today? |
|---|---|---|---|
| Malicious web page mints LiveKit JWT → joins room → speaks as agent OR eavesdrops on mic | **HIGH** (any browsed page) | **CRITICAL** (mic + can issue commands) | **YES** — CORS `*`, no auth |
| Local process hijacks `extension_hello` WS → intercepts every `ext_browse` call | MEDIUM (requires local code execution) | **HIGH** (Chrome cookie/storage exfil) | **YES** |
| `.env` exfiltrated from world-readable file → 22 API keys leak (Groq/OpenAI/Anthropic/DeepSeek/Kimi/LiveKit) | MEDIUM (any local process) | **HIGH** (financial: ~$1k/day burn cap; reputational) | **YES** — files are 0664 |
| Prompt injection in user's mic audio → bash tool destructive command | LOW (destructive-pattern annotation but doesn't refuse) | **CRITICAL** (NOPASSWD sudo is documented; absent in reality, but the bash tool runs as user with full $HOME write) | PARTIAL — sudo NOPASSWD doesn't exist so escalation is gated |
| Two `bash` tools registered, order-dependent which wins | LOW | **HIGH** (the legacy 37-line version has no destructive-pattern gate) | YES |
| LiveKit room hijack via direct WS to SFU on 7880 | LOW (no LAN exposure) | HIGH (same as #1) | LOCALHOST only |
| Memory store pollution (already in place) → supervisor confidence in false facts | **CERTAIN** | LOW-MEDIUM (1-turn confusion, not exfil) | **HAS HAPPENED** |
| Test-skip in CI hides regressions on master | **CERTAIN** | LOW (caught at next bisect) | **TODAY** |

**Mitigation priority:** P0 #1-3 closes the bridge auth threat. P0 #6 closes the bash-tool / sudo confusion. P0 #4 closes the .env exfil. After Q1, the only un-mitigated CRITICAL/HIGH is the bash-tool prompt-injection (deferred to a longer redesign — see Q3 sandboxing work).

---

## What we are NOT reviewing (out of scope / deferred)

- `src/web/` Next.js app — light touch from ops review only. Acknowledged as separate 3-column-layout target per CLAUDE.md.
- `src/cli/` Claude-Code-shaped agent — explicitly off-limits per CLAUDE.md (the recent realtime episode taught us). The bridge subset of src/cli was reviewed; the CLI agent core was not.
- `src/os/desktop/` (Misty Scone) — flagged in ops review as missing from disk. Deferred until Q4.
- Android app (`src/android/`) — present in repo (we saw `llama.cpp` artifacts) but no review depth. Status unclear.
- Browser extension UI/UX (`src/extensions/jarvis-screen/side_panel.js`) — only the security surface was reviewed; UX gaps unaddressed.

---

## How to use this doc

For the **next 4 weeks**, work the P0 list in this order: 1-9 (the security + correctness fixes), 10-11 (the STT-quality fixes), 12 (the pin gotcha), 13-20 (the test + telemetry + breaker fixes). Each P0 has a file:line-precise pointer into the source review for context.

For **months 2-12**, the Q1/Q2/Q3/Q4 roadmap above is the prioritization. Each quarter has a success criterion you can grade against.

For **questions / disagreements**, the source reviews under [`docs/reviews/2026-05-16/`](reviews/2026-05-16/) carry the full reasoning, citations, and counter-arguments. Each review is independent and read-only; modifications to JARVIS during the next 12 months should NOT edit the source reviews — they're the historical baseline against which progress is measured.

**Last update:** 2026-05-16. Reviewers: 10 parallel specialists (Claude Sonnet 4.6 + Opus 4.7 mix). Synthesizer: Claude Opus 4.7.

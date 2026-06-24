# JARVIS — Enterprise-Grade Plan for a Single User

**Date:** 2026-05-17
**Author hat:** AI Engineering Lead, with a parallel team of 5 senior specialists (web/extension/android, data/backup/observability, supply-chain/sandboxing/CI, security-research, performance-research).
**Audience:** Ulrich.
**Status:** ACTIVE. Supersedes the prioritization in [`docs/2026-05-16-jarvis-global-review.md`](2026-05-16-jarvis-global-review.md) **only on items below; everything else there remains canonical.**

This is not a re-review of yesterday's review. It is the **next plan that incorporates what yesterday's review found AND what the deeper sweep surfaced** (web app + extension + android + supply chain + sandboxing + backup + observability + cost + 2026 best-practices research). It is opinionated, risk-tolerant per Ulrich's brief ("don't be scared to break things as long as it works perfectly later"), and structured as 4 sprints (90-day execution) feeding a 12-month roadmap.

---

## 0. Executive summary — what "enterprise-grade for one user" actually means

JARVIS is a top-decile-quality voice-first AI assistant codebase carrying load-bearing technical debt in six areas, of which **two were entirely missed by the 2026-05-16 review** and **three are getting worse, not better, as the codebase evolves**.

> **What single-user enterprise-grade means here.** No on-call team, so reliability + self-healing + recoverability matter MORE than at scale. No SLA negotiations, so honesty matters more than diplomacy. One person's API budget caps the blast radius of a runaway loop, so cost discipline must be auditable. One person's daily-driver, so a 5-minute downtime hurts more than a 0.1% throughput regression. The bar is **"if Ulrich's laptop disk dies tonight, JARVIS is back to identical state on a new laptop within 30 minutes, with no memory loss, no key re-pasting from screenshots, and no manual setup beyond `curl … | bash`"** — that is the operational floor an enterprise SRE team would set for a daily-driver internal tool.

### The six load-bearing weaknesses

1. **Backup is theatrical.** `scripts/jarvis-backup-local.sh` exists; the timer that fires it doesn't. `~/.jarvis/snapshots/` doesn't exist on disk. 93 memories + 876 conversation messages + 196 telemetry rows are one `rm -rf` from gone. The encryption-at-rest runbook says "use LUKS" but doesn't verify LUKS is actually enabled. **No backup of `.env`/keys to a password manager.** This is the highest-blast-radius finding in either review and is independent of every other open item. _Net new this review._

2. **The web app is a wide-open LAN-exposed shell.** `src/web/scripts/pty-server.mjs` binds to `0.0.0.0:8769` with **zero authentication** — anyone on the WiFi gets an unauthenticated bash session as `ulrich`. 60+ Next.js API routes (workspace/exec, file, delete-session, deploy, vercel-token) trust a hardcoded `LOCAL_USER_ID` constant. The `better-auth` package is installed and zero-imported. _Net new this review and worse than any bridge finding._

3. **Two supply-chain CVEs are live in shipped dependencies.** `axios 1.15.0` in `src/cli/` has an active high-severity authentication-bypass GHSA. `marked 18.0.0` in `src/cli/` has an OOM-DoS via tokenizer recursion. `next 16.2.4` in `src/web/` has a DoS via Server Components. **Dependabot was deleted on 2026-05-15** (commit `9bbb1285`) so no automated refresh runs. _Net new this review._

4. **The 2026-05-16 review's P0s were partially actioned and partially regressed.** `.env` files ARE now `chmod 600` (good — P0 #4 done). But: the global review's P0 #1 (bridge auth) is unchanged; P0 #6 (sudoers reconciliation) is unchanged; P0 #7 (Coding Kiddos purge) is unchanged; P0 #9 (memory_auto_extracted wiring) is unchanged; P0 #13 (fakeredis in tests) is partially done — `requirements-test.txt` has it but **CI doesn't install that file** (`voice-agent-tests.yml:71` only installs `requirements.txt`). The roadmap is alive but execution is uneven.

5. **88% of cost data is NULL.** `_PRICING_USD_PER_1M` in `tools/token_estimation.py:52-69` is missing entries for every Anthropic, OpenAI, and Google model JARVIS calls. 173 of 196 recent turns have `cost_usd IS NULL`. The Q1 global-review claim "$0.28 / 7-day spend" is the 12% visible tip. _Net new this review._

6. **DNS rebinding is a load-bearing 2026 threat** (CVE-2026-25253 "ClawJacked", Feb 2026) — same shape as the JARVIS bridge + Chrome-extension architecture. Beyond the bridge-auth P0 from 2026-05-16, the bridge must also enforce a Host-header allowlist (`127.0.0.1` / `localhost` / `[::1]` only) and WS Origin checks, because the bearer-token-only defense is bypassable by a malicious page that hits a rebinding domain. _Net new this review._

### What's actually good, so the rest reads honestly

- Voice-agent architecture (subagent registry + tool gate + sanitizer stack + 4-layer memory + LangGraph router) is sound and well-documented per CLAUDE.md.
- 1,545 voice-agent tests pass in ~22 s. Per-sanitizer coverage exists. 3 CI workflows exist.
- Tauri 2.x desktop is properly hardened (capability set is the right minimum; CSP is reasonably tight).
- All four `.env` files are now `0600`. `livekit-keys.yaml` is `0600`. Hub schema is consolidated. Convex retirement is mostly done.
- The 10-domain review machinery itself is a competitive moat — most one-person voice projects don't have this depth of self-criticism.

### What this plan adds beyond the 2026-05-16 review

| Theme | 2026-05-16 status | This plan adds |
|---|---|---|
| Bridge auth | P0 #1-3 captured | + DNS-rebinding Host-header allowlist (P0-SEC-7), + WS Origin check |
| Bash sandbox | P0 #6 (sudo reconcile) + Q3 sandboxing | + **bubblewrap wrap on every `bash()` call** with `--unshare-net` default (P0-SEC-9) |
| `.env` permissions | P0 #4 captured | DONE — verify in this plan |
| Memory pollution | P0 #7-9 captured | + 2 new P0s on consolidator dead-row leak + audit-log surface |
| Tests RED | P0 #13 captured | + CI doesn't install `requirements-test.txt` (P0-CI-1) |
| Stop hook missing cargo build | P0 #14 captured | + Stop hook itself has zero tests (P1-CI-7) |
| Pre-flight cache | P0 #18 captured | unchanged |
| Web app | OUT OF SCOPE | **+ 8 new findings, 3 P0s including LAN-exposed pty-server** |
| Backup/DR | NOT REVIEWED | **+ entire new section, 4 P0s** |
| Cost tracking | partially covered | **+ pricing table missing for 3 providers, 88% NULL costs** |
| Dep supply chain | P1 SHA256 livekit binary | **+ 4 active CVEs in shipped deps** |
| LiveKit OTel | NOT REVIEWED | **+ LiveKit 1.5 ships native OTel; JARVIS uses zero of it** |
| Android decision | Q4 "decide fate" | **+ recommend: move to own repo, save 150 MB clone bloat** |
| Browser extension | security-only | **+ XSS via innerHTML, safety.js dead code (CommonJS in MV3 SW)** |
| CLI boundary | not covered | **+ run_jarvis_cli inherits FULL env including all API keys** |

---

## 1. The unified P0-P3 inventory (consolidated, this review + 2026-05-16, deduped)

Format: `[Pn-AREA-#] <title> | <file:line> | <effort estimate> | <source>`

Source: **NEW** = surfaced by this review. **G** = global review 2026-05-16 (carried forward). **G+** = global review + this review reinforces or extends.

### P0 — STOP THE BLEEDING (target: 14 days)

These are **blocking** — the system has either security holes that any local process can walk through, data-loss vectors that one disk crash can trigger, or correctness regressions hiding in CI silence.

#### Security & Auth

| ID | Title | Evidence | Effort | Src |
|---|---|---|---|---|
| **P0-SEC-1** | Restore bridge auth — set `JARVIS_REQUIRE_LOCAL_AUTH=1` in `start-desktop.sh`, generate `~/.jarvis/local-api-token.env` at install (0600), plumb to Tauri React (`window.__JARVIS_LOCAL_API_TOKEN`), Chrome extension (`chrome.storage.local`), voice-agent (`JARVIS_LOCAL_API_TOKEN` env) | `src/cli/scripts/start-desktop.sh`, `src/cli/src/bridge/server.ts`, `install.sh`, `src/voice-agent/tools/_browser_ext_base.py` | 1d | G |
| **P0-SEC-2** | Tighten CORS — drop `*`, allowlist `tauri://localhost`, `app://localhost`, `chrome-extension://<id>` only; OPTIONS preflight must also be locked | `src/cli/src/bridge/server.ts` (lines around 417) | 2h | G |
| **P0-SEC-3** | Authenticate `/ws` upgrade + reject `extension_hello` from any socket without bearer token | `src/cli/src/bridge/server.ts` | 4h | G |
| **P0-SEC-4** | **DNS-rebinding defense** — Host-header allowlist on every HTTP + WS handler (only `127.0.0.1` / `localhost` / `[::1]`); WS `Origin` header check. CVE-2026-25253 fits JARVIS bridge + extension exactly | `src/cli/src/bridge/server.ts` (add as middleware) | 4h | **NEW** |
| **P0-SEC-5** | **Kill the web pty-server LAN exposure** — `src/web/scripts/pty-server.mjs:30` binds to `0.0.0.0:8769`; change to `127.0.0.1` and add the same bearer-token guard as the bridge. Audit every Next.js API route under `src/web/src/app/api/` for binding assumption drift | `src/web/scripts/pty-server.mjs:30`, ~60 route handlers | 1d | **NEW** |
| **P0-SEC-6** | **Lock all 60+ web API routes behind real auth** — `better-auth` is installed but never imported; either wire it OR ship a middleware that requires the same bridge bearer token. Stop relying on the `LOCAL_USER_ID` hardcoded constant for tenancy | `src/web/src/lib/chat/persist.ts:5`, all `src/web/src/app/api/**/route.ts` | 2d | **NEW** |
| **P0-SEC-7** | **Sandbox `bash()` via bubblewrap** — wrap every invocation with `bwrap --new-session --bind / / --tmpfs ~/.ssh --tmpfs ~/.aws --ro-bind /usr /usr --unshare-net` and an opt-in `network=True` arg that the supervisor must explicitly pass. Log every `network=True` use to `turn_telemetry.db`. Promote `_DESTRUCTIVE_PATTERNS` from annotate-only to **refuse** for `rm -rf /`, `dd of=/dev/sd*`, `mkfs.*`, `curl/wget | sh/bash` | `src/voice-agent/tools/bash.py:262` | 1d | G+ |
| **P0-SEC-8** | **Scrub provider keys from `run_jarvis_cli` env** — `jarvis_agent.py:1115-1142` propagates the full env to the CLI subprocess. Strip every `*_API_KEY` except a proxy stub `JARVIS_PROXY_URL=http://127.0.0.1:4000` so CLI inherits routing without the key ring | `src/voice-agent/jarvis_agent.py:1115-1142` | 2h | **NEW** |
| **P0-SEC-9** | **Reconcile sudo NOPASSWD claim** — `/etc/sudoers.d/jarvis` doesn't exist; remove the assumption from CLAUDE.md + bash docstring + unit comments AND add `NoNewPrivileges=yes` to all `jarvis-*.service` units (since no scoped Cmnd_Alias is in flight) | CLAUDE.md, `src/voice-agent/tools/bash.py:10-15`, all `setup/systemd/jarvis-*.service` | 2h | G |
| **P0-SEC-10** | Pick ONE bash tool, delete the legacy 37-line `jarvis_agent.py:1890::bash` (no destructive gate, no plan-mode gate, 90s timeout, 3 KB cap) | `src/voice-agent/jarvis_agent.py:1890`, `src/voice-agent/tools/bash.py` | 1h | G |
| **P0-SEC-11** | **Browser extension XSS** — `side_panel.js:134` writes `renderMarkdown(content)` output via `innerHTML` without sanitization; bridge or LLM output containing `<img onerror=...>` runs in extension origin with `<all_urls>` + `cookies` + `debugger` permissions. Use DOMPurify or `textContent` for assistant text; reserve `innerHTML` for hardcoded UI shells only | `src/extensions/jarvis-screen/side_panel.js:134` | 4h | **NEW** |
| **P0-SEC-12** | **Wire or delete `safety.js`** — `src/extensions/jarvis-screen/safety.js` uses `module.exports = ...` (CommonJS) which doesn't load in an MV3 service worker; the documented safety gates for `exec_js`, `set_cookies`, payment domains, credential exfil are **inert**. Convert to ES module, import from `background.js`, enforce server-side in `ext_browse.ts` too | `src/extensions/jarvis-screen/safety.js`, `background.js` | 1d | **NEW** |

#### Data Integrity & Backup

| ID | Title | Evidence | Effort | Src |
|---|---|---|---|---|
| **P0-DATA-1** | **Backup is theatrical — schedule it.** `scripts/jarvis-backup-local.sh` exists with proper atomic SQLite `.backup` API + retention; **no systemd timer fires it**. Add `~/.config/systemd/user/jarvis-backup-local.timer` (OnCalendar=hourly, Persistent=true) + matching `.service` unit. Verify `~/.jarvis/snapshots/` populates within 1 hour of install | `scripts/jarvis-backup-local.sh`, new `setup/systemd/jarvis-backup-local.{service,timer}` | 2h | **NEW** |
| **P0-DATA-2** | **Off-disk backup layer** — local snapshots share the source disk. Install Restic, point at a USB-mounted local repo (`/mnt/backup/restic`) AND Backblaze B2 (free tier covers 5 GB; egress is free via Cloudflare alliance). Nightly. Encrypt with a passphrase stored in Ulrich's password manager — **NOT in `.env`** | new `bin/jarvis-backup-remote` + systemd timer | 1d | **NEW** |
| **P0-DATA-3** | **Mirror `.env` to a password manager once.** Add `bin/jarvis-keys-export` that dumps repo `.env` + `src/voice-agent/.env` + `~/.jarvis/livekit-keys.yaml` into a 1Password/Bitwarden vault entry. Run on every credential rotation. **Without this, fresh-laptop recovery is hours of provider portal visits** | new `bin/jarvis-keys-export` | 2h | **NEW** |
| **P0-DATA-4** | **Schedule log rotation** — `scripts/rotate-jarvis-logs.sh` exists; no timer fires it. `voice-agent.log` is currently **55 MB** (over the documented 50 MB cap). Add `jarvis-log-rotate.timer` (OnCalendar=daily, 02:00). Also schedule `jarvis-retention-prune.sh` (telemetry DB) monthly | `scripts/rotate-jarvis-logs.sh`, `scripts/jarvis-retention-prune.sh`, new timer units | 1h | **NEW** |
| **P0-DATA-5** | **SQLite WAL + synchronous=NORMAL on every connect.** Both `state.db` and `turn_telemetry.db` are in DELETE journal mode (verified: `sqlite3 ~/.jarvis/hub/state.db "PRAGMA journal_mode;"` returns `delete`). Add `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON` to `bootstrap_schema` in `src/hub/server.py:41`, every `connect()` in `src/hub/client.py`, and `pipeline/turn_telemetry.py:81/209/244/292` | listed | 4h | **NEW** |
| **P0-DATA-6** | **Wipe Coding Kiddos memory pollution + rewrite extractor few-shots** — 20+ of 99 memories are `Coding Kiddos`-prefixed fictional narration. `DELETE FROM memories WHERE content LIKE '%Coding Kiddos%'` (preserving the 2 real ones); replace `_EXTRACTOR_PROMPT` few-shots with diverse non-repeating subjects | `src/voice-agent/pipeline/memory_extractor.py:182-235`, `~/.jarvis/hub/state.db` | 4h | G |
| **P0-DATA-7** | Unify `JARVIS_MEMORY_TOP_N` default to **8** in both `pipeline/config.py:242` and `tools/memory.py:393` | listed | 30m | G |
| **P0-DATA-8** | Wire `memory_auto_extracted=True` into `log_turn` from `on_user_turn_completed` | `src/voice-agent/jarvis_agent.py`, `pipeline/turn_telemetry.py` | 2h | G |
| **P0-DATA-9** | Delete the 0-byte `~/.jarvis/conversations.db` zombie (retired Phase 12, still touched on first hub boot) — remove the touch logic | `src/hub/server.py` boot path | 30m | **NEW** |

#### Observability & Cost

| ID | Title | Evidence | Effort | Src |
|---|---|---|---|---|
| **P0-OBS-1** | **Fill `_PRICING_USD_PER_1M` for Anthropic + OpenAI + Google** — 173 of 196 recent turns have `cost_usd IS NULL`. Add rate dicts for: claude-haiku-4.5, claude-sonnet-4.6, claude-opus-4.7, gpt-5-nano, gpt-5-mini, gpt-5.1, gpt-5-pro, gemini-2.5-flash, gemini-2.5-pro. Use current 2026-05 public list prices | `src/voice-agent/tools/token_estimation.py:52-69` | 2h | **NEW** |
| **P0-OBS-2** | **Verify Anthropic prompt caching actually hits** — `caching="ephemeral"` set at `providers/llm.py:241-249` but telemetry shows zero `cost_usd` rows on Anthropic turns. Stash `usage.cache_read_input_tokens` from the LLM response into a new `prompt_cached_tokens` column. **Note: Anthropic dropped cache TTL from 60 min → 5 min in early 2026**; consider the 1-hour TTL at 2x write premium if sessions exceed 5-min idle | `src/voice-agent/providers/llm.py:241-249, ~4900-4907`, `pipeline/turn_telemetry.py` schema | 4h | G+ |
| **P0-OBS-3** | **Enable LiveKit Agents 1.5 native OTel** — LiveKit 1.5 ships `set_tracer_provider(...)` with TTFT/tok-s/STT/TTS/VAD/EOU/function-tool spans out of the box. Wire to a local SigNoz or OTLP collector. JARVIS currently emits zero traces despite the rich data being free. This is the single highest-leverage observability win available | `src/voice-agent/jarvis_agent.py` (entrypoint), new docker-compose for SigNoz | 1d | **NEW** |
| **P0-OBS-4** | **Provider hard-spend caps** — set portal-side caps on Anthropic ($50/mo), OpenAI ($50/mo), Groq ($20/mo), DeepSeek ($10/mo), Google ($10/mo) so a prompt-injection loop ("call llama-70b 1M times") fails closed. Document the per-provider portal URLs in `docs/runbook/credential-rotation.md`. **Without portal caps, a misbehaving LLM can burn the entire balance silently** | provider portals + runbook update | 2h | **NEW** |

#### Voice Pipeline Quality

| ID | Title | Evidence | Effort | Src |
|---|---|---|---|---|
| **P0-VOICE-1** | **Non-Latin script gate on STT** — drop transcripts where >50% of alphabetic codepoints are non-Latin AND length <12 chars. Kills ~9% of recent turns (Russian/Japanese/Chinese background-TV hallucinations) | `src/voice-agent/pipeline/stt_gate.py:55-76` | 2h | G |
| **P0-VOICE-2** | **Output-language sanitizer** — new `sanitizers/output_language.py`; drop replies if >30% non-Latin alphabetic AND not preceded by a user turn in the same language (catches the Bosnian reply at turn 160) | new file + install in `sanitizers/__init__.py` | 4h | G |
| **P0-VOICE-3** | **Drop `deepseek-v4-pro`** from `SPEECH_MODELS` and the fallback cascade. 66 of 200 recent turns hit it; 22 took >30s; three >700s with hallucinated Bosnian replies. Replace rung 2 with `gpt-5-mini` | `src/voice-agent/providers/llm.py:145-153, 749-755` | 2h | G |
| **P0-VOICE-4** | **Reconcile `DEFAULT_SPEECH_MODEL` / pin-disables-dispatcher** — either change default to `llama-3.3-70b-versatile` so a tray pin doesn't kill BANTER fast-path AND REASONING fast-path, OR redesign pinning into two semantics (`JARVIS_PIN_SUPERVISOR_LLM` vs `JARVIS_PIN_ALL_ROUTES`) with the tray defaulting to the former | `src/voice-agent/providers/llm.py:84`, `jarvis_agent.py:3774-3787` | 1d | G |
| **P0-VOICE-5** | **Whisper hallucination defenses** — add `prompt="The user is speaking English to a voice assistant named Jarvis."`, `temperature=0.0`, `no_speech_threshold=0.8`, `condition_on_previous_text=False` to the Groq Whisper call. If the livekit-plugins-groq stub doesn't accept these, override `_recognize_impl` to inject them into the request body | `src/voice-agent/providers/stt.py:69` | 4h | G |
| **P0-VOICE-6** | **TTFW telemetry poison** — clear `_jarvis_turn_start_monotonic` after every `log_turn` AND clamp `ttfw_ms > 60_000` to NULL on write (today max is 1.7M ms zombie values poisoning all per-route averages) | `src/voice-agent/pipeline/turn_dispatcher.py`, `pipeline/turn_telemetry.py` | 2h | G |
| **P0-VOICE-7** | **Circuit breaker exception classification** — add `non_failure_classifier: Callable[[BaseException], bool]` parameter to `CircuitBreaker.__init__`; move the validation-error revert logic from `BreakeredLLMStream` (`providers/llm.py:530-547`) into the breaker so STT + TTS benefit too; 429 rate-limits stop counting as failures | `src/voice-agent/resilience/circuit_breaker.py:94`, `providers/llm.py:500-548` | 1d | G |
| **P0-VOICE-8** | **Pre-flight cache** in `BreakeredGroqLLM.chat` — cache stringification by `(id(chat_ctx), len(items), id(items[-1]))`. Skip pre-flight when `len(items) < 10`. ~200-500 ms TTFW saved per turn after the first | `src/voice-agent/providers/llm.py:585-621` | 4h | G |
| **P0-VOICE-9** | **8 µs telemetry columns** — add `t_vad_end_us`, `t_stt_final_us`, `t_preflight_us`, `t_llm_ttfb_us`, `t_llm_first_text_us`, `t_tts_ttfb_us`, `sanitizer_us`, `chat_ctx_items`. Without these every latency claim is hand-wavy | `src/voice-agent/pipeline/turn_telemetry.py:78-158`, providers/llm.py + tts.py | 1d | G |

#### CI / Tests

| ID | Title | Evidence | Effort | Src |
|---|---|---|---|---|
| **P0-CI-1** | **CI must install `requirements-test.txt`** — `voice-agent-tests.yml:71` only installs `requirements.txt`, so the 6 fakeredis-dependent hub tests still collection-error in CI silently | `.github/workflows/voice-agent-tests.yml:71` | 30m | **NEW** |
| **P0-CI-2** | **Master is RED** — fix `test_vad_prewarm_uses_production_thresholds` (test pins 0.5/0.25, source has 0.6/0.3 — update the test to match the validated tuning) AND `test_realtime_model_called_with_full_config` (add `pytest.importorskip("livekit.plugins.google")`) | `src/voice-agent/tests/test_voice_fixes_2026_05_04.py`, `tests/test_screen_share_subagent.py` | 1h | G |
| **P0-CI-3** | **Add `cargo build --release`** to the Stop hook AND to `desktop-tauri-smoke.yml` (PR push only, not every PR — too slow). Today's hook only runs `npm run build`, contradicting its own documented rule | `.claude/hooks/verify-before-done.sh`, `.github/workflows/desktop-tauri-smoke.yml` | 1h | G |
| **P0-CI-4** | **Add `web-tests.yml`** — `src/web/` has ~100 vitest cases and `eslint.config.mjs`; nothing in CI runs them. Pure blind spot | new `.github/workflows/web-tests.yml` | 1h | G+ |
| **P0-CI-5** | **Fix CI ignore-list ghosts** — `voice-agent-tests.yml` ignores `test_browser_ext_contract.py` and `test_supervisor_vision.py`; both files don't exist | listed | 5m | G |

#### Dependency Supply Chain

| ID | Title | Evidence | Effort | Src |
|---|---|---|---|---|
| **P0-DEP-1** | **Fix CLI axios + marked CVEs** — `cd src/cli && npm audit fix` resolves `axios 1.15.0` (auth bypass GHSA) and `marked 18.0.0` (OOM-DoS). Both are direct deps. CLI is the most-privileged process JARVIS ships | `src/cli/package-lock.json`, `npm audit` | 30m | **NEW** |
| **P0-DEP-2** | **Bump web `next 16.2.4 → 16.2.6+`** — DoS via Server Components | `src/web/package.json`, `npm audit` | 30m | **NEW** |
| **P0-DEP-3** | **Re-enable Dependabot in security-only mode** — `9bbb1285` deleted `.github/dependabot.yml` for noise. Replace with `open-pull-requests-limit: 0` + GitHub Security Updates ON (gets you CVE patches with no version-bump noise) | new `.github/dependabot.yml` | 1h | **NEW** |

#### Docs / Configuration

| ID | Title | Evidence | Effort | Src |
|---|---|---|---|---|
| **P0-DOCS-1** | **Reconcile CLAUDE.md `jarvis-bridge.service` lie** — either create the systemd unit (preferred — bridge wants to be a daemon, 6 places already probe for it) OR strip the references from CLAUDE.md, README.md, `docs/runbook/jarvis-voice.md`, `bin/jarvis-evolution-soak-check.sh`, `src/cli/src/commands/voice/status.ts`, `.claude/hooks/SessionStart.sh` | listed | 4h create-unit / 1h strip-refs | G |
| **P0-DOCS-2** | **Wire (or delete) `learned_rules.md`** — file doesn't exist on disk; the v2 loader silently returns empty. Either populate it from `evolution_log.jsonl` accepted-tier rules OR remove the loader path | `pipeline/learned_rules_v2.py` + `~/.jarvis/learned_rules.md` | 4h | **NEW** |

**P0 total: 38 items. Estimated effort: ~22 person-days. With Ulrich + Claude pairing: ~10 calendar days at 4-6 hours/day.**

---

### P1 — STRUCTURAL (target: 30-60 days)

Material risk reductions, big modularization wins, or productivity multipliers. None of these are "the system breaks today" but each is something an enterprise SRE would file as a Sev-2 ticket.

#### Voice Pipeline & Memory (12)

| ID | Title | Evidence | Effort | Src |
|---|---|---|---|---|
| P1-VOICE-1 | **Compress `prompts/supervisor.md`** from ~34k → ~16-18k tokens. Merge SUBSTANTIVE ENGAGEMENT + TECHNICAL DEPTH + NO-PREAMBLE + TASK BREVITY. Footnote past-failure dates into `prompts/regressions.md`. ~50% prefill speedup + ~50% TPM headroom | `src/voice-agent/prompts/supervisor.md` | 2d | G |
| P1-VOICE-2 | **Mid-stream LLM stall watchdog** — extend `BreakeredLLMStream` to track inter-chunk timing; raise `APITimeoutError` if any gap > `JARVIS_LLM_CHUNK_TIMEOUT_S=5`. Catches the >700s turns the breaker doesn't | `src/voice-agent/providers/llm.py:463-565` | 4h | G |
| P1-VOICE-3 | **AEC via PipeWire module-echo-cancel** — `~/.config/pipewire/pipewire.conf.d/echo-cancel.conf` + `JARVIS_AUDIO_{INPUT,OUTPUT}_DEVICE={mic,sink}_aec` in `.env`. Disable `JARVIS_APM_AGC=0` (AGC amplifies room tone above VAD deactivation) | `.env`, PipeWire conf | 4h | G |
| P1-VOICE-4 | **VAD-truth listening indicator** — voice-client's `state.listening` is RMS-driven not VAD-driven; publish `vad_state` via LiveKit data channel from `session.on("user_state_changed")` and consume in the voice client | `src/voice-agent/jarvis_voice_client.py:632-641`, `jarvis_agent.py` (add publisher) | 1d | G |
| P1-VOICE-5 | **Retire `llama-3.1-8b-instant` from BANTER and from the LangChain classifier** — telemetry confirms 3.3-70b is faster (3.8 s vs 5.1 s mean TTFW). Swap BANTER to `gpt-5-nano` if `OPENAI_API_KEY` present, else `llama-3.3-70b-versatile`. Same for the classifier in `turn_graph.py:348` | `providers/llm.py:707-872`, `pipeline/turn_graph.py:348` | 4h | G |
| P1-VOICE-6 | **Property tests for sanitizers + token-pruning + classifier** using hypothesis. Highest payoff on `pycall`, `dsml`, `internal_phrase` — they're load-bearing per CLAUDE.md and currently example-based only. ~5-6 days for 5 highest-value targets | `tests/property/` (new dir) | 5d | G |
| P1-VOICE-7 | **Memory TTL pruner** — `JARVIS_MEMORY_TTL_DAYS=90`; on consolidator run, drop memories with `last_used_ts < now - TTL` AND `use_count < 5`. With 99 rows growing, garbage compounds | `src/voice-agent/pipeline/memory_consolidator.py` | 4h | G |
| P1-VOICE-8 | **Expand `denial_detector` patterns** — add "I don't keep track of", "I don't preserve conversation history", "Each session starts fresh". Add unit tests | `src/voice-agent/sanitizers/denial_detector.py` | 2h | G |
| P1-VOICE-9 | **Memory pane in desktop-tauri** — read-only list grouped by category with delete-this-row button. Calls `forget()` via hub bus. Eliminates the "voice-only memory editing" cliff | `src/voice-agent/desktop-tauri/src/components/MemoryPanel.jsx` (new), Rust IPC | 2d | G |
| P1-VOICE-10 | **EMOTIONAL classifier over-firing fix** — `_EMOTION_LEX["excited"]` triggers on "nice"/"perfect"/"cool" with no length floor; add `len(transcript.split()) >= 3` precondition | `src/voice-agent/pipeline/turn_router.py:28-80` | 2h | G |
| P1-VOICE-11 | **Refactor `_handler` closure** — extract `_inject_route_prefix`, `_swap_llm_tts`, move hot-reload-prompt-state to its own listener. Closure drops from 485 → ~200 lines; `# noqa: C901` goes away | `src/voice-agent/pipeline/turn_dispatcher.py` | 2d | G |
| P1-VOICE-12 | **Delete `_pick_supervisor_llm` dead function** — 7-line function whose docstring says it's dead | `src/voice-agent/jarvis_agent.py:3692-3699` | 10m | G |

#### Code Quality & Refactor (5)

| ID | Title | Evidence | Effort | Src |
|---|---|---|---|---|
| P1-CODE-1 | **`jarvis_agent.py` extraction** — move ~1,750 lines of `@function_tool` defs to `tools/voice_tools.py`, ~400 lines of TTS text-transform filters to `pipeline/tts_filters.py`, ~170 lines of silent-mode/mute/wake to `pipeline/silent_mode.py`. Net: 5,478 → ~2,800 lines | `src/voice-agent/jarvis_agent.py` | 3-4d | G |
| P1-CODE-2 | **`main.rs` module split** — 2,090 lines → `tray_icon.rs` / `keys.rs` / `web_probe.rs` / `tray_menu.rs` / `hotspot_poll.rs` + thin `main.rs` (~300 lines) | `src/voice-agent/desktop-tauri/src-tauri/src/main.rs` | 1d | G |
| P1-CODE-3 | **Orphan `asyncio.create_task` fixes** — 5 sites in `jarvis_agent.py` (lines 3505, 3539, 4010, 5357, 5362) missing the `_bg_tasks.add(t); t.add_done_callback(_bg_tasks.discard)` pattern that 3 of 8 sites already use | listed | 1h | G |
| P1-CODE-4 | **Tray ✓-markers on Speech & Tool submenus** — mirror the TTS pattern. 17-item submenus need this for usability | `src/voice-agent/desktop-tauri/src-tauri/src/main.rs:741, 829, 1620-1632` | 4h | G |
| P1-CODE-5 | **Drop orphan `tray-toggle-screen-share` emit** — Rust fires it, nothing listens | `main.rs:1830` | 10m | G |

#### Security (5)

| ID | Title | Evidence | Effort | Src |
|---|---|---|---|---|
| P1-SEC-1 | **Sandbox `jarvis-hub.service`** — currently zero hardening. Copy voice-agent's block + `ProtectSystem=strict`, `ReadWritePaths=%h/.jarvis/hub`, `NoNewPrivileges=yes`. Move logs out of `/tmp` → `~/.local/share/jarvis/logs/hub.log` | `setup/systemd/jarvis-hub.service` | 1h | G+ |
| P1-SEC-2 | **Enforce `confirmed=true` server-side** in the extension for `set_cookies`, `storage_state_set`, `local_storage` writes, `exec_js`. Today the flag flows through `ext_browse.ts::cmd.confirmed` but extension actions don't check it | `src/extensions/jarvis-screen/background.js`, `actions.js` | 4h | G |
| P1-SEC-3 | **Limit `/api/livekit/token` identity** — today any caller picks their own identity. Bind identity to bearer-token (`identity=token_subject`) or allowlist of `["desktop", "android", "jarvis-agent"]` | `src/cli/src/bridge/server.ts` | 2h | G |
| P1-SEC-4 | **SHA256 verification on `livekit-server.bin`** — 50MB binary committed to repo. Verify checksum at install.sh time; pin in a manifest file | `install.sh`, new `setup/livekit-server.bin.sha256` | 1h | G |
| P1-SEC-5 | **Encryption-at-rest via fscrypt** on `~/.jarvis` if LUKS not enabled (verify LUKS status first). Per-directory encryption tied to PAM login session, ext4-native | runbook + `install.sh` | 2h | G+ |

#### Data / Backup / Observability (8)

| ID | Title | Evidence | Effort | Src |
|---|---|---|---|---|
| P1-OBS-1 | **`bin/jarvis-canary` desktop alerts** — every 5 min, check: voice-agent service active? last turn < 30 min ago? daily cost < `$JARVIS_DAILY_COST_CEILING_USD` (default 5)? `notify-send` if any check fails. Run via systemd timer | new `bin/jarvis-canary` + timer | 4h | **NEW** |
| P1-OBS-2 | **Correlation IDs across services** — generate `turn_id = uuid4()` at `on_user_turn_completed`; propagate to voice-client, bridge, hub, livekit-server logs as a structured field. Loki queries can fan out one turn across all four services | every service writer | 1d | **NEW** |
| P1-OBS-3 | **Audit log surface — `bin/jarvis-audit-today`** — joins `messages` + `memories` + tool-call telemetry; pipes through `less -RX`. "What did JARVIS do today" in one command | new bin script + SQL | 4h | **NEW** |
| P1-OBS-4 | **Daily digest** — `bin/jarvis-daily-digest` chained from daily timer; renders TTFW p50/p95 + cost by model + memory growth + breaker-trip count + 429-event count; `notify-send` summary | new bin script | 4h | **NEW** |
| P1-OBS-5 | **SigNoz local stack** — one docker-compose; OTel collector in front so JARVIS code stays portable. Replaces piecemeal observability with logs+metrics+traces in one UI on a 2C/4T machine | new `infra/observability/docker-compose.yml` | 1d | **NEW** |
| P1-OBS-6 | **Restore drill** — `scripts/jarvis-restore-drill.sh` does a snapshot round-trip into `/tmp/jarvis-restore-test`, asserts row counts unchanged. Weekly timer | new script + timer | 2h | **NEW** |
| P1-OBS-7 | **Schema-versioned migrations for hub** — `schema_version` rows are written but never read. Add explicit per-version migration steps so `ALTER TABLE` additions don't silently no-op on existing installs | `src/hub/server.py`, `src/hub/schema.sql` | 4h | **NEW** |
| P1-OBS-8 | **Hub watchdog** — `jarvis-hub.service` has no `WatchdogSec=`; if hub deadlocks, systemd never restarts. Add `WatchdogSec=120` and `sd_notify` heartbeats from the consumer loop | `setup/systemd/jarvis-hub.service`, `src/hub/server.py` | 2h | **NEW** |

#### CI / DX (8)

| ID | Title | Evidence | Effort | Src |
|---|---|---|---|---|
| P1-CI-1 | **`lint.yml` workflow** — `ruff check src/voice-agent` + `eslint src/web` + `tsc --noEmit -p src/web` + `cargo clippy --workspace -- -D warnings`. ~45s. Catches the entire "import error at runtime" class | new `.github/workflows/lint.yml` | 2h | G+ |
| P1-CI-2 | **`nightly-e2e.yml` workflow** — fake LiveKit server + stubbed LLM + WAV STT input + assert audio sink writes PCM. ~60-90s per case. Catches every live-config drift class | new workflow + `tests/e2e/` | 2-3d | G |
| P1-CI-3 | **`.pre-commit-config.yaml`** — ruff on staged `.py`, eslint on staged `.ts/.tsx`, gitleaks on every commit. <2s on staged files | new config | 1h | **NEW** |
| P1-CI-4 | **`bin/jarvis-release`** script — validate clean tree, bump version, vite build, cargo build --release, sign + checksum, tag, push, gh release create with binary | new script | 4h | **NEW** |
| P1-CI-5 | **Add `pytest-timeout`, `pytest-rerunfailures` to CI** for hung-test + flake protection | `requirements-test.txt` + workflow | 30m | G |
| P1-CI-6 | **Add `--durations=10` to pytest** so slow tests surface in normal runs | `verify-before-done.sh`, CI | 5m | **NEW** |
| P1-CI-7 | **Stop hook self-tests** — `tests/test_verify_before_done.bats` covering stdin parsing, JSONL decoding, subtree classification, recursion guard. Gates the gate | new test file | 4h | **NEW** |
| P1-CI-8 | **Move `bin/jarvis-desktop` launcher out of `src/cli/scripts/`** — `start-desktop.sh` is cross-tree dep; relocate to `bin/_internal/start-desktop.sh` so the off-limits boundary is real | listed | 1h | **NEW** |

#### Web App (4)

| ID | Title | Evidence | Effort | Src |
|---|---|---|---|---|
| P1-WEB-1 | **CSRF / Origin checks** on all POST/DELETE/PUT routes — middleware pass. Today combined with no-auth, any local webpage can write to state.db / delete sessions | `src/web/src/middleware.ts` (new) | 1d | **NEW** |
| P1-WEB-2 | **Workspace file-read .env exposure** — `/api/workspace/[id]/file?path=.env&raw=1` happily streams workspace `.env`. Add denylist for `.env*` paths regardless of dotfile filter | `src/web/src/lib/workspace/storage.ts:371` | 1h | **NEW** |
| P1-WEB-3 | **Wire `better-auth` for real or kill it** — installed, zero-imported. Ship a single-user passcode flow (Ulrich's local password manager auto-fills) or rip out 2 MB attack surface | `src/web/src/lib/auth/*`, `package.json`, schema | 1d / 1h | **NEW** |
| P1-WEB-4 | **Vitest coverage for the 60+ API routes** — currently 1 sanity test. Author 1 test per route asserting auth+CSRF gate. Snapshot the route surface to catch silent additions | `src/web/tests/` | 2d | **NEW** |

#### Configuration / Docs (5)

| ID | Title | Evidence | Effort | Src |
|---|---|---|---|---|
| P1-CFG-1 | **`jarvis config show` / `jarvis config edit`** — single CLI entry point that dumps every JARVIS-related file + value + last-modified-by-whom. Today config is 8+ scattered files | new `bin/jarvis-config` | 4h | **NEW** |
| P1-CFG-2 | **Document config precedence** — `~/.jarvis/keys.env` > repo `.env` > `src/<x>/.env.local`; document in CLAUDE.md + `docs/runbook/config-precedence.md` | docs | 1h | **NEW** |
| P1-CFG-3 | **Versioned hot-reload for subagent prompts** — `JARVIS_PROMPT_HOT_RELOAD=1` checks mtime on subagent specs before each handoff. Today only supervisor.md + learned_rules.md hot-reload | `src/voice-agent/subagents/agent.py`, registry | 4h | **NEW** |
| P1-CFG-4 | **README per subtree** — `src/voice-agent/README.md`, `src/hub/README.md`, `src/voice-agent/desktop-tauri/README.md` covering venv location, env vars, test command, gotchas | 3 docs | 2h | **NEW** |
| P1-CFG-5 | **Define RPO/RTO targets in `docs/runbook/disaster-recovery.md`** — memories + rules = RPO 1h / RTO 5min, telemetry = RPO 24h / RTO best-effort, API keys = RPO 7d / RTO "go log into each portal" so dump to password manager | new doc | 1h | **NEW** |

**P1 total: 47 items. Effort: ~38 person-days. Sprint 2-3.**

---

### P2 — QUALITY + DEPTH (target: 60-180 days)

Polish, structural maintenance, things that pay back over the year. Detailed enumeration omitted for brevity; selection of the most-impactful:

- **Subagent registry consolidation** — pick HandoffSubagent OR DelegatedSubagent and retire the other (currently both wrappers exist, all 7 DelegatedSubagents are gated off by default)
- **Bailout-phrase regex shared module** — `subagents/agent.py:46-77` and `sanitizers/internal_phrase.py:45+` are 95% identical, separately maintained
- **`sanitizers/__init__.py::install_all()`** — formalize the install order in code, not docstrings
- **Semantic recall** via embeddings (nomic-embed-text local 137M model → `memories.embedding` blob column) — only after pollution is cleaned
- **Source-pinning on memory bullets** — render `· first stated 2026-05-04` so the supervisor knows when a fact was learned
- **Per-route TTS warm-up** at session start (200-400 ms saved on first BANTER/REASONING/EMOTIONAL turn)
- **`Memory Audit pane`** in web app with delete-with-undo workflow
- **Move bridge to `src/desktop-bridge/`** — half-day, kills the `cli/` namespace collision
- **Move android to its own repo** — frees ~150 MB clone, lets it iterate independently
- **Decide `src/os/desktop/` fate** — build Misty Scone or strip references from CLAUDE.md + desktop-tauri.md
- **Strip 20 unmaintained Rust crates** transitive via wry/tauri — tracking only, no CVEs today
- **Voice rubric automation** — nightly `jarvis-rubric-rescore.sh --dry-run` posts deltas
- **Replace `_PRICING_USD_PER_1M` flat dict** with a `pricing_overrides.yaml` that's tracked separately from code (so a price change isn't a code change)
- **`pyright`/`mypy` strict mode** on `src/voice-agent/` touched files
- **`tracing` crate** in Rust desktop (replace `println!`/`eprintln!`)
- **Coverage reporting** via `pytest --cov`, gate at >85% diff coverage on `sanitizers/` and `pipeline/`
- **Read-only memory mirror to `~/.jarvis/memory/<category>.md`** — auto-regenerated on consolidator runs; user can `vim` and save → file watcher republishes events (Claude Code MEMORY.md pattern)
- **Litestream** continuous SQLite replication to B2 (vs hourly snapshots) for sub-minute RPO on memories

**P2 total: ~30-40 items. Ongoing throughout months 3-6.**

---

### P3 — POLISH (target: 180-365 days)

- AppImage + Flathub packaging path for sharable desktop binary
- OS rice (Misty Scone) build or delete decision
- Hardware refresh decision — N100 mini PC as the JARVIS box, laptop becomes pure UI
- Memory contradiction detector (separate `detect_contradiction` LLM pass)
- True E2E LiveKit room + Chrome-driver + audio-pipe test in CI (nightly only)
- Voice intelligence rubric → 10/10 axes goal with auto-bisect-on-regression
- `topic` field added to memories (short noun phrase, indexed)
- `time-travel debugging` — `bin/jarvis-replay --at 2026-05-16T14:00`
- Multi-provider prompt caching audit + cache_read telemetry per provider
- Dispatcher generalization — 5th `MEMORY` route + 6th `EXTENDED_THINKING` route validated against telemetry

---

## 2. The four-sprint execution plan

> **Each sprint is 14 days. Total = 8 weeks of focused work to clear the P0 + P1 backlog.** P2 + P3 are background tracks layered on top.

### Sprint 1 (days 1-14) — Security + Data Integrity

**Theme:** stop a malicious webpage + a disk crash from owning Ulrich.

Order matters. Do auth before any non-security changes (so we ship behind a guard), then close the backup gap (so any subsequent breakage is recoverable), then the bash sandbox + LAN-exposed pty.

1. P0-SEC-1, 2, 3, 4 — bridge auth + CORS + WS auth + Host-allowlist (1-2 days, single PR)
2. P0-SEC-5, 6 — kill web pty 0.0.0.0 bind + lock 60+ web API routes (2 days)
3. P0-DATA-1, 2, 3, 4 — backup timer + Restic + password-manager mirror + log rotation (1-2 days)
4. P0-DATA-5 — SQLite WAL on every connect (4 hours)
5. P0-DATA-9, P0-DOCS-1, P0-CI-5 — purge zombies, fix CLAUDE.md, clean ignores (half-day)
6. P0-SEC-7, 8, 9, 10 — bubblewrap on bash + scrub CLI env + sudoers reconcile + delete duplicate bash (1-2 days)
7. P0-SEC-11, 12 — extension XSS + safety.js wiring (1 day)
8. P0-DEP-1, 2, 3 — npm audit fix + re-enable Dependabot (half-day)
9. P0-CI-1, 2, 3, 4 — CI installs requirements-test, fix RED tests, add cargo release + web-tests (half-day)

**Sprint 1 exit criterion:** `curl 192.168.x.x:8769` returns connection refused. `curl 127.0.0.1:8765/api/think -d '{}'` returns 401 without bearer token. `~/.jarvis/snapshots/` populates within 1 hour of install. `cd src/cli && npm audit` shows zero high-severity. CI master is green.

### Sprint 2 (days 15-28) — Voice Quality + Cost Visibility

**Theme:** make the user-visible behavior sharp and the spend auditable.

1. P0-VOICE-1, 2, 3, 5 — STT non-Latin gate + output language sanitizer + drop deepseek-v4-pro + Whisper biasing (1 day)
2. P0-VOICE-4 — pin-disables-dispatcher reconcile (1 day)
3. P0-VOICE-6 — TTFW telemetry poison fix (2 hours)
4. P0-VOICE-7 — breaker exception classification (1 day)
5. P0-VOICE-8 — pre-flight cache (4 hours)
6. P0-VOICE-9 — 8 µs telemetry columns (1 day)
7. P0-OBS-1, 2 — fill pricing table + verify Anthropic caching (1 day)
8. P0-OBS-3 — LiveKit OTel + SigNoz docker-compose (1 day)
9. P0-OBS-4 — provider portal caps (2 hours)
10. P0-DATA-6, 7, 8 — Coding Kiddos purge + TOP_N unify + memory_auto_extracted wiring (1 day)
11. P0-DOCS-2 — learned_rules.md wire-or-delete (half-day)

**Sprint 2 exit criterion:** TTFW p50 ≤ 1.5 s on `gpt-5-mini` over 100-turn dev set. Zero non-Latin STT hallucinations in 24h soak. SigNoz dashboard shows per-turn cost + cache-hit rate per provider. `bin/jarvis-canary` fires on simulated $5 daily-cost breach. Memory store has zero `Coding Kiddos`-prefixed rows.

### Sprint 3 (days 29-56) — Structural Refactor + Test Maturity

**Theme:** make tomorrow's regressions cheaper to catch and tomorrow's changes safer to land.

1. P1-VOICE-1 — supervisor.md compression (2 days)
2. P1-VOICE-2 — mid-stream stall watchdog (4 hours)
3. P1-VOICE-3, 4, 5 — AEC + VAD-truth indicator + retire 8b-instant from BANTER (1-2 days)
4. P1-CODE-1, 2, 3, 4, 5 — `jarvis_agent.py` extraction + `main.rs` split + asyncio orphans + tray ✓-markers + orphan emit (4-5 days)
5. P1-VOICE-11, 12 — `_handler` refactor + delete `_pick_supervisor_llm` (2 days)
6. P1-CI-1, 2, 3, 4 — lint workflow + nightly e2e + pre-commit + release script (3-4 days)
7. P1-OBS-1, 2, 3, 4 — canary + correlation IDs + audit-today + daily-digest (1-2 days)
8. P1-SEC-1, 2, 3, 4 — hub sandbox + extension confirmed-true + LiveKit identity + livekit-server SHA256 (1-2 days)
9. P1-WEB-1, 2, 4 — CSRF + .env denylist + vitest route coverage (2-3 days)

**Sprint 3 exit criterion:** `jarvis_agent.py` < 3,500 lines. `main.rs` < 800 lines. CI suite green with lint + web-tests + nightly e2e. Property tests pass for `pycall`/`dsml`/`internal_phrase`. `bin/jarvis-canary` running. Web routes have auth + CSRF + .env protection.

### Sprint 4 (days 57-84) — Quality + Operational Excellence

**Theme:** the system should self-report, self-heal, and self-document.

1. P1-VOICE-6 — property tests for sanitizers + pruning + classifier (5 days)
2. P1-VOICE-7, 8 — memory TTL pruner + denial pattern expansion (1 day)
3. P1-VOICE-9, 10 — memory pane in Tauri + emotional classifier tighten (2-3 days)
4. P1-SEC-5 — fscrypt or LUKS verification (2 hours + maybe reinstall)
5. P1-OBS-5, 6, 7, 8 — SigNoz integration polish + restore drill + hub migration + hub watchdog (2-3 days)
6. P1-WEB-3 — better-auth wire-or-kill decision (1 day either way)
7. P1-CFG-1, 2, 3, 4, 5 — config show + precedence docs + subagent hot-reload + per-subtree READMEs + DR doc (2-3 days)
8. P1-CI-5, 6, 7, 8 — pytest plugins + slow-test surface + Stop-hook tests + launcher relocation (1-2 days)
9. Begin P2 backlog selection.

**Sprint 4 exit criterion:** `bin/jarvis-config show` renders complete state. `bin/jarvis-audit-today` answers "what did JARVIS do today". Memory pane in Tauri loads + delete works end-to-end. SigNoz dashboards include 8 trace types. fscrypt or LUKS verified active on `~/.jarvis`. Property-test suite covers 5 highest-value targets.

---

## 3. Bold "take risks" recommendations — explicitly flagged per the brief

Ulrich said "don't be scared to break things as long as it works perfectly later." Below are the not-merely-incremental moves the team would take if the brief permits.

### 3.1 KILL THESE — net-negative as they stand

| Subject | Status | Recommendation |
|---|---|---|
| `src/android/` | 150 MB vendored llama.cpp, last touched 2026-04-26, talks to homelab 10.10.0.50:8765, zero integration with voice-agent/bridge/hub | **Move to its own repo** (`jarvis-android`), vendor llama.cpp as a submodule. Frees clone time + CI runs + cognitive load. If Ulrich abandons it later, deletion in the standalone repo is a one-command op |
| `src/convex/` (residual) | Convex retired Phase 7; only `.env.local` (197 bytes) + empty Python pin remain | Delete the dir + remove `convex~=0.7` from `src/voice-agent/requirements.txt:42` |
| `src/os/desktop/` (Misty Scone) | Referenced in CLAUDE.md + desktop-tauri.md; doesn't exist on disk | Strip refs from both files. Add a `docs/future/misty-scone.md` placeholder if Ulrich still wants the option |
| Browser extension `safety.js` | Documented as the safety layer; doesn't actually load (CommonJS in MV3 SW) | Rewrite as ES module + wire from `background.js` OR delete + add a comment in `actions.js` admitting the gates are LLM-prompt-only |
| `_pick_supervisor_llm` | Dead since 2026-05-10 | Delete + inline |
| `~/.jarvis/conversations.db` | 0-byte zombie | Delete + remove first-boot touch logic |
| `~/.jarvis/livekit-keys.yaml.bogus-format` | Documented mistake from 2026-05-15 | Delete |

### 3.2 REBUILD THESE — the current shape is fundamentally wrong

| Subject | Current | Target |
|---|---|---|
| Bridge location | `src/cli/src/bridge/server.ts` (off-limits subtree) | `src/desktop-bridge/` standalone subtree + systemd unit. Removes the off-limits-but-required tension and the namespace collision with the CLI's separate "remote bridge" |
| Web app trust model | "Single trusted user on localhost" with zero enforcement (60+ routes, no auth, hardcoded `LOCAL_USER_ID`) | Pick one: (a) wire `better-auth` for real (single-user passcode) + middleware enforces it on every route; (b) **kill the workbench/PTY/deploy machinery entirely** and ship the web app as a thin client over `hub/state.db`. The current "looks enterprise, actually wide open" is worse than either extreme |
| Bash tool sandbox | In-process, regex-warning-only destructive detection | bubblewrap wrap default; `--unshare-net` off by default; opt-in network via tool arg. Audit-log every network=True turn |
| `run_jarvis_cli` env | Full env including all `*_API_KEY` | Stripped env + `JARVIS_PROXY_URL=http://127.0.0.1:4000` only. CLI inherits routing without the key ring |
| Memory store format | Single SQLite row per fact, opaque | Add `topic` field; auto-mirror to `~/.jarvis/memory/<category>.md` (read-only export, vim-able with file-watcher republish) |
| Subagent dual-shape | Both `HandoffSubagent` and `DelegatedSubagent` machinery, 7 Delegated ones gated off | Pick HandoffSubagent (the simpler one). Retire DelegatedSubagent; document deprecation date |

### 3.3 ADD THESE — net-new capabilities the system needs

| Subject | Why | Cost |
|---|---|---|
| **SigNoz local observability stack** | LiveKit 1.5 emits OTel for free; JARVIS uses none of it. 1 docker-compose = traces + metrics + logs across all services on a 2C/4T box | 1 day |
| **Litestream continuous WAL replication** | Snapshot-every-hour → continuous = sub-minute RPO. Backblaze B2 backend, free tier covers 5 GB, Cloudflare-alliance free egress | 4 hours |
| **`bin/jarvis-canary`** | Closes the "voice-agent silently dying at 2am" failure mode. `notify-send` is good enough for one user | 4 hours |
| **`bin/jarvis-audit-today`** | The "what did JARVIS do today" question is unanswerable today | 4 hours |
| **`bin/jarvis-config show`** | Single source of truth for 8 scattered config files | 4 hours |
| **`bin/jarvis-keys-export`** | Mirrors `.env` to password manager. Fresh-laptop recovery becomes 30 min, not hours of provider portal visits | 2 hours |
| **`bin/jarvis-release`** | Two-step Tauri release is documented but un-automated. Codifies it | 4 hours |
| **OpenTelemetry tracer provider** | LiveKit 1.5 native. Wire `set_tracer_provider` once, get TTFT/STT/TTS/VAD/EOU spans for free | 4 hours |
| **Pre-commit hooks (.pre-commit-config.yaml)** | Stop hook gates Claude turns; humans bypass via direct commit. gitleaks closes the .env-exfil class | 1 hour |
| **Memory pane in Tauri** | "Forget what I said about X" UX without voice-only | 2 days |

### 3.4 HARDWARE TAKE — explicit recommendation

Ulrich is on a 2C/4T i7-7600U (2017 ULV). The perf review establishes that **software wins ~500 ms TTFW; a 6-core upgrade buys ~300 ms.** Concrete recommendation:

- **Stay on the laptop for daily use** but pair with an **Intel N100 mini PC** ($150-250, 15W) hosting:
  - `jarvis-hub.service` (currently lives on laptop)
  - SigNoz observability stack (currently absent)
  - Restic backup repo (off-laptop disk)
  - Optional: local Whisper.cpp 1.8.3 with iGPU 12x perf (offline fallback)
- The laptop runs `jarvis-voice-agent.service` + `jarvis-voice-client.service` + Tauri desktop only.
- Cost: $200 hardware + $0/mo (B2 + CF free tier covers backup; SigNoz self-hosted is free).
- ROI: ~300 ms TTFW win + observability + backup off-disk + always-on hub even when laptop is closed.

**This is the only hardware recommendation. Everything else is software work.**

---

## 4. Dependency / Sequencing Graph

```
                          ┌─────────────────────────────┐
                          │  Sprint 1 — Security + Data │
                          └──────────────┬──────────────┘
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              ▼                          ▼                          ▼
   ┌────────────────────┐     ┌────────────────────┐    ┌─────────────────────┐
   │ Bridge auth +      │     │ Backup timer +     │    │ Web pty bind +      │
   │ CORS + WS + Host   │     │ Restic + keys      │    │ web API auth +      │
   │ allowlist          │     │ export             │    │ npm audit fix       │
   └──────────┬─────────┘     └──────────┬─────────┘    └──────────┬──────────┘
              │                          │                         │
              └──────────────────────────┼─────────────────────────┘
                                         │
                                         ▼
                          ┌─────────────────────────────┐
                          │ bubblewrap on bash +        │
                          │ scrub CLI env +             │
                          │ sudoers reconcile           │
                          └──────────────┬──────────────┘
                                         │
                          ┌──────────────▼──────────────┐
                          │  Sprint 2 — Voice + Cost    │
                          └──────────────┬──────────────┘
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              ▼                          ▼                          ▼
   ┌────────────────────┐    ┌──────────────────────┐    ┌─────────────────────┐
   │ STT gates + drop    │   │ Pricing table +      │    │ Memory purge +      │
   │ deepseek + breaker  │   │ Anthropic cache +    │    │ TOP_N unify +       │
   │ classification      │   │ provider caps        │    │ memory_auto wire    │
   └──────────┬─────────┘    └──────────┬───────────┘    └──────────┬──────────┘
              │                         │                           │
              └─────────────────────────┴───────────────────────────┘
                                        │
                                        ▼
                          ┌─────────────────────────────┐
                          │ Pre-flight cache + 8µs cols │
                          │ + LiveKit OTel + SigNoz     │
                          └──────────────┬──────────────┘
                                         │
                          ┌──────────────▼──────────────┐
                          │  Sprint 3 — Refactor + Tests│
                          └──────────────┬──────────────┘
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              ▼                          ▼                          ▼
   ┌────────────────────┐    ┌──────────────────────┐    ┌─────────────────────┐
   │ supervisor.md       │   │ jarvis_agent.py      │    │ lint + e2e + pre-   │
   │ compress + AEC +    │   │ extract + main.rs    │    │ commit + release    │
   │ VAD-truth indicator │   │ split + handler ref  │    │ script              │
   └──────────┬─────────┘    └──────────┬───────────┘    └──────────┬──────────┘
              │                         │                           │
              └─────────────────────────┴───────────────────────────┘
                                        │
                                        ▼
                          ┌─────────────────────────────┐
                          │ canary + audit + digest +   │
                          │ correlation IDs + hub       │
                          │ sandbox + LK identity       │
                          └──────────────┬──────────────┘
                                         │
                          ┌──────────────▼──────────────┐
                          │  Sprint 4 — Operations      │
                          └──────────────┬──────────────┘
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              ▼                          ▼                          ▼
   ┌────────────────────┐    ┌──────────────────────┐    ┌─────────────────────┐
   │ Property tests +    │   │ Memory pane +        │    │ jarvis-config +     │
   │ TTL pruner + denial │   │ fscrypt + better-auth│    │ per-subtree READMEs │
   │ patterns            │   │ wire-or-kill         │    │ + DR doc            │
   └─────────────────────┘    └──────────────────────┘    └─────────────────────┘
```

---

## 5. Success criteria — measurable, per quarter

### End of Sprint 2 (~30 days)
- Bridge auth ON; `curl 127.0.0.1:8765` without bearer = 401
- `~/.jarvis/snapshots/` populated hourly; restic remote has 1 nightly backup
- TTFW p50 ≤ 1.5 s on dispatcher-default speech model over 100-turn dev set
- Zero non-Latin STT hallucinations in 24h soak window
- 0 `Coding Kiddos` rows in `state.db.memories`
- `cost_usd` populated on >95% of recent turns
- Master branch tests green
- Provider portal caps configured; canary alerts on simulated breach

### End of Sprint 4 (~90 days)
- `jarvis_agent.py` < 3,500 lines; `main.rs` < 800 lines
- Property test suite for 5 sanitizers
- `bin/jarvis-canary`, `bin/jarvis-audit-today`, `bin/jarvis-daily-digest`, `bin/jarvis-config`, `bin/jarvis-release` all live
- SigNoz dashboard renders TTFW + cost + cache-hit per provider
- Memory pane in Tauri loads + delete-via-UI works end-to-end
- fscrypt OR LUKS verified active on `~/.jarvis`
- Web app: zero unauthenticated routes; pty-server binds 127.0.0.1
- Bridge has its own systemd unit OR docs match reality

### End of 12 months
- Per Q3-Q4 of the 2026-05-16 global review: nightly e2e, semantic recall, voice-rubric automation, OS rice decided
- One additional bold goal from this plan: **the system survives `rm -rf ~/.jarvis && rm -rf ~/.local/share/jarvis && killall systemd-user-units` followed by `bin/jarvis-restore --from-b2` and resumes within 30 minutes with zero memory loss**

---

## 6. Anti-recommendations — load-bearing constraints to preserve

These are inherited from the 2026-05-16 review + CLAUDE.md + verified against current code. The new plan reaffirms them.

1. **Don't touch the four mandatory monkey-patches** (`deepseek_roundtrip`, `tool_name_sanitizer`, `AcousticTap`, `anthropic_strict_schema`) plus `strict_schema_relax`, `pycall`, `dsml`, `handoff_text`
2. **Don't tighten the subagent tool gate** beyond the narrow `_BAILOUT_SUMMARY_RE` allowlist
3. **Don't re-enable `resume_false_interruption=True`** (LiveKit's `pause()` is broken on SFU output)
4. **Don't tighten confab-detector tool-evidence lookback** below 10; keep `transfer_to_*`/`delegate`/recent-extractor as evidence
5. **Don't loosen VAD `activation_threshold` below 0.5**; the 0.6 bump on 2026-05-16 was the right call
6. **Don't drop `min_words=3` for TASK route**
7. **Bare "Jarvis" → "Yes?"** is canonical, not "Yes, sir?"
8. **No `Co-Authored-By` trailers; no "Generated with Claude Code" attribution**
9. **Don't touch `src/cli/`** when working on voice-agent / desktop / web (this plan respects that; CLI items are flagged as "delegate to user")
10. **Don't move logs back to `/tmp`** (2026-05-07 evidence-loss incident)
11. **Don't reintroduce the voice reactor sphere** — `audioLevel = 0` in `useVoiceClient.js:68` is intentional
12. **Don't bump `RECALL_SEARCH_LIMIT` past 12** in `pipeline/chat_ctx.py`
13. **Don't restart `jarvis-voice-agent.service` while a session is active** (check `turn_telemetry.db` `ts_utc` within 60s)
14. **Don't skip `cargo build --release` when shipping JS changes to desktop-tauri**
15. **Don't delete `voice_session_within_60s` in `main.rs`** despite `#[allow(dead_code)]`
16. **Don't reintroduce the "sir" suffix to subagent `ack_phrase`**

---

## 7. Open questions for Ulrich

The plan above is opinionated but six decisions are Ulrich's to make:

1. **Hardware: N100 mini PC for hub + observability + backup target?** ($150-250 one-time; enables continuous hub uptime + off-disk backup + offline Whisper fallback)
2. **Backup destination preference?** Backblaze B2 (cheapest, free egress via Cloudflare alliance) vs Wasabi (flat $6.99/mo regardless) vs local-NAS-only (zero cloud trust, but correlated failures with the laptop)
3. **Web app fate — wire `better-auth` for single-user, or rip out the workbench/PTY/deploy machinery and ship a thin client?** Both are 1-day jobs; affect future capability
4. **Android — separate repo or formal archive?** "Move to its own repo" is the recommendation; "archive in this repo" is the cheaper alternative if Ulrich wants the option to revive
5. **OS rice (Misty Scone) — build, defer, or remove docs?** Currently aspirational with zero code; references in CLAUDE.md mislead every fresh Claude session
6. **CLI is off-limits but has 4 active CVEs and inherits the full env from voice-agent.** Do we allow this plan's P0-DEP-1 (`npm audit fix`) and P0-SEC-8 (env scrub at the boundary inside voice-agent) without re-opening the CLI subtree itself?

---

## 8. How to use this plan

For the **next 14 days**: work P0 in the Sprint 1 order above. Each P0 has file:line evidence, effort estimate, and source provenance (NEW = this review; G = 2026-05-16 global review). Use the `bin/jarvis-release` script (P1-CI-4) once it exists to ship each meaningful change as a tagged release so rollback is `git revert + jarvis-release`.

For **months 2-12**: Sprints 2-4 above, then P2 backlog rotation. The 12-month roadmap in [`docs/2026-05-16-jarvis-global-review.md`](2026-05-16-jarvis-global-review.md) §`12-month roadmap` remains canonical for Q3-Q4 themes (semantic recall, OS rice decision, dispatcher generalization, memory expiration/TTL). This plan augments Q1-Q2 with the items above.

For **deeper context on any single finding**: each `[Pn-AREA-#]` has either a `G` (read the matching source review under `docs/reviews/2026-05-16/`) or a `NEW` (read the corresponding subsection above).

For **conflicts between this plan and the 2026-05-16 plan**: this plan wins on items it explicitly addresses; the 2026-05-16 plan remains canonical for everything else. No silent overrides — every change to the prior plan is enumerated in §0's delta table.

---

**Reviewers:** Engineering Lead (synthesis), 5 specialist subagents (web/extension/android, data/backup/observability, supply-chain/sandboxing/CI, security-research, performance-research).

**Last updated:** 2026-05-17.

**Next review:** Sprint 2 closeout (~2026-06-15) — re-run delta against this plan, update success criteria with measured numbers.

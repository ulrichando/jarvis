# Code Review — voice-agent + desktop-tauri (2026-05-08)

> Format modeled on Claude Code's Code Review (severity markers, file:line, extended reasoning). Triggered after the voice-tool audit (six in-flight fixes) and a parallel desktop-tauri health check.

## Scope

- **voice-agent**: in-flight 2026-05-08 fixes (subagent gating × 7, no-tool retry ceiling, confab auto-extractor evidence, `_META_PARAPHRASE_RE` filter, token-aware chat_ctx pruning, LangGraph guards) plus a broader pass against the load-bearing constraints in [`CLAUDE.md`](../../CLAUDE.md) and [`.claude/rules/voice-agent.md`](../../.claude/rules/voice-agent.md).
- **desktop-tauri**: full health pass — frontend (React/JSX), backend (Rust/Tauri 2), IPC, build hygiene, security posture.

## Severity legend

| Marker | Severity | Meaning |
| :--- | :--- | :--- |
| 🔴 | Important | A bug that should be fixed before merging |
| 🟡 | Nit | A minor issue, worth fixing but not blocking |
| 🟣 | Pre-existing | A bug that exists in the codebase but was not introduced by this PR |

## Verdicts

| Surface | Verdict | Findings |
| :--- | :--- | :--- |
| voice-agent | **fix-then-ship** | 1 🔴, 4 🟡 (1018/1018 tests green) |
| desktop-tauri | **concerns** | 3 🔴, 12 🟡, 4 🟣 |

---

## Voice-agent findings

### Severity table

| Severity | File:Line | Issue |
| :--- | :--- | :--- |
| 🔴 Important | `src/voice-agent/specialists/agent.py:80-82` | `_NO_TOOL_RETRY_CEILING` cached at module import — env-var changes after import are silently ignored |
| 🟡 Nit | `src/voice-agent/confab_detector.py:127-139` | `_has_recent_extraction_evidence` doesn't check whether assistant text actually claims a memory write |
| 🟡 Nit | `src/voice-agent/pipeline/memory_extractor.py:95-116` | `_META_PARAPHRASE_RE` over-rejects: "Pretva appears to involve regulatory work in Cameroon" would be filtered as narration |
| 🟡 Nit | `src/voice-agent/pipeline/memory_extractor.py:35-62` | `_LAST_EXTRACTION_SUCCESS_AT` is module-global mutable state — single-event-loop-only assumption is undocumented |
| 🟡 Nit | `src/voice-agent/tests/test_specialist_bailout_2026_05_08.py:139-143` | `importlib.reload(specialists.agent)` mid-test invalidates `RegistrySpecialist` identity for any session-scoped fixture |

### Extended reasoning

**🔴 Important — `specialists/agent.py:80-82` — env-var-cached retry ceiling**

CLAUDE.md states the retry ceiling is `JARVIS_SPECIALIST_NO_TOOL_RETRY_CEILING` (default 3) and force-bails after consecutive refusals. The implementation reads `os.environ` at module-import time and caches the result as `_NO_TOOL_RETRY_CEILING`. After an operator edits the systemd unit's `Environment=` line, the new ceiling never takes effect until the worker process restarts. The neighboring `JARVIS_SPECIALIST_TOOL_GATE` flag five lines below uses the runtime-read pattern correctly (`os.environ.get(...) != "0"` inside `task_done`). Inconsistency hides the difference.

**Suggested fix:** read `os.environ.get("JARVIS_SPECIALIST_NO_TOOL_RETRY_CEILING", "3")` inside `task_done` (or wherever the counter is checked), matching the gate flag's pattern. ~5-line change.

**Verification:** existing test in `test_specialist_bailout_2026_05_08.py:139-143` passes only because it uses `importlib.reload(specialists.agent)` to force re-evaluation — see the related 🟡 below for why that reload is itself a smell.

---

**🟡 Nit — `confab_detector.py:127-139` — extraction evidence lacks save-claim gate**

The fix grants confab-evidence credit when `extract_memory_from_turn` succeeded ≤30 s ago, motivated by the v2 architecture where the supervisor's `chat_ctx` doesn't see the off-band memory write. But `_has_recent_extraction_evidence` is consulted whenever the prior-message tool-evidence path returns nothing — regardless of what the assistant actually said. A confab like "Done, sir, opened a new tab" arriving 25 s after a successful extraction passes the gate even though it claims a browser action, not a memory write.

**Suggested fix:** condition the extraction-evidence path on `_SAVE_CLAIM_RE` matching the assistant text (e.g., `\b(?:saved|noted|got it|remembered|added to memory)\b`). Only "save/remember" claims get the extraction evidence credit; other claim shapes still need real tool evidence in chat_ctx.

---

**🟡 Nit — `memory_extractor.py:95-116` — `_META_PARAPHRASE_RE` over-rejects**

The regex `\b(?:appears|seem(?:s|ed)?|looks?\s+like|...)\s+to\s+(?:be\s+|involve|describe|...)` is intended to drop LLM-narration shapes ("The user is X-ing", "It appears to be Y"). It also rejects genuine first-person facts that happen to use hedging — e.g., "Pretva appears to involve regulatory work in Cameroon" or "Coding Kiddos seems to involve teaching kids to code." Memory-extractor LLM rarely produces facts in this hedged shape, but the filter overshoots its written intent.

**Suggested fix:** anchor the regex to subject prefixes that ARE narration markers ("The user", "It seems to me", "It looks like the user") rather than blanket `\b(?:appears|seem...)\b`. Risk is low — both forms are rare in extractor output — but a unit test row that asserts a hedged-but-real fact PASSES the filter would prevent silent narrowing.

---

**🟡 Nit — `memory_extractor.py:35-62` — `_LAST_EXTRACTION_SUCCESS_AT` is module-global**

The 30-s evidence timestamp lives in a module-global. `extract_memory_from_turn` is called via `asyncio.create_task` per turn, so concurrent tasks could race on the timestamp. The 30-s TTL absorbs any plausible interleave window, so the race is benign today, but the assumption ("single event loop, never thread-spawned") is undocumented. If the voice-agent ever runs concurrent extractor tasks (e.g., per-segment streaming), this becomes a real race.

**Suggested fix:** add a comment `# single-event-loop only — concurrent tasks are not safe but TTL=30s absorbs the window` next to the global, or wrap with an `asyncio.Lock`.

---

**🟡 Nit — `tests/test_specialist_bailout_2026_05_08.py:139-143` — `importlib.reload` smell**

Reloading `specialists.agent` mid-test invalidates the `RegistrySpecialist` class identity for any later test that imports the pre-reload class (e.g., a session-scoped pytest fixture caching a `RegistrySpecialist` instance). Currently fine because every test in this file re-imports inside the test body, but a future fixture-level Specialist would silently break with `isinstance` mismatch. Symptom of the underlying 🔴: the constant is module-cached, so the test has to reload to test variations.

**Suggested fix:** if the 🔴 fix lands (read env at runtime), this test loses its need for `importlib.reload` — drop the reload and use `monkeypatch.setenv` directly.

### Load-bearing constraints — pass

- Three monkey-patches at import (`deepseek_roundtrip`, `tool_name_sanitizer`, `AcousticTap`) — installed at `jarvis_agent.py:101,118,7793-7794`
- STAY-IN-SUPERVISOR rule present at `jarvis_agent.py:2322`
- `_ROUTE_BASE` (BANTER=1, TASK=3, REASONING=3, EMOTIONAL=3) intact
- `handoff_text_suppressor` walks FULL chat_ctx, not last 15
- Confab lookback = 10, `transfer_to_*` / `delegate` count as evidence
- All 7 subagent gates use the same `JARVIS_SUBAGENT_<NAME>=1` pattern
- No bare `except:`, no zero-stub telemetry, no Co-Authored-By, sanitizer install order unchanged

### Caveat

`weather` specialist's failure phrase ("I couldn't determine your location") is NOT in `_BAILOUT_SUMMARY_RE`. The retry-ceiling will eventually force-bail it, but a clean exit is preferable. Either add the phrase to the allowlist or rephrase the specialist prompt to use `cannot accomplish — handing back to supervisor`. Minor since `weather` is off by default.

---

## Desktop-tauri findings

### Severity table

| Severity | File:Line | Issue |
| :--- | :--- | :--- |
| 🔴 Important | `src-tauri/src/main.rs:1499-1522` | `quit` handler stops `jarvis-voice-agent.service` without checking `turn_telemetry.db` — direct violation of CLAUDE.md operational rule |
| 🔴 Important | `package.json:12,16,23` + `public/{ort.*,silero_vad_v5.onnx,vad.worklet.bundle.min.js}` | ~97 MB of unused VAD/ORT/WASM shipped on every release; voice migrated to Python `jarvis-voice-client:8767` |
| 🔴 Important | `src/App.jsx:78` + `src/components/ChatPanel.jsx:474-498` | Two WebSocket clients both connect as `client=desktop`, duplicating `chat_response` events |
| 🟡 Nit | `src-tauri/src/main.rs:1058-1067` | Bridge bearer token injected via `window.eval` — combined with `csp:null`, creates a future-XSS escalation surface |
| 🟡 Nit | `src-tauri/tauri.conf.json:36` | `"csp": null` |
| 🟡 Nit | `src/main.jsx:8-10` | 5-second heartbeat ping to dead `:8766/debug/level` (no listener — only `:8765` and `:8767` exist) |
| 🟡 Nit | `src-tauri/src/main.rs:495-548,583-592` | Cross-process HTTP via `format!`-built JSON shelled to `curl`, spawned-and-orphaned (no `.wait()`, no error capture) |
| 🟡 Nit | `src-tauri/capabilities/default.json:6-10` | `shell:default` allowlisted but frontend never imports `@tauri-apps/plugin-shell` — drop it |
| 🟡 Nit | `src/components/ChatPanel.jsx:298-325` | New `AudioContext` per chime; Chromium caps ~6 concurrent contexts → silent failure after long tool runs |
| 🟡 Nit | `src/components/ChatPanel.jsx:415-422` | `clear_tools` zeros loading state — race with mid-stream replies |
| 🟡 Nit | `src/App.jsx:88-101` | WS effect closure captures stale `ttsEnabled`; toggle won't take effect for queued messages |
| 🟡 Nit | `src/components/ChatPanel.jsx:79-81` | Initial size `(window.innerWidth*0.72, window.innerHeight*0.78)` snapshots once — can overflow smaller monitors after resize |
| 🟡 Nit | `src-tauri/src/main.rs:756-770` + `:198,203-208` | `find_project_root` walks 6 hardcoded ancestors; `_keys_file()` hardcodes `$HOME/Documents/Projects/jarvis/` — brittle for packaged installs |
| 🟡 Nit | `src-tauri/src/main.rs:881-888` | `handle_open_browser` sleeps 500 ms × 60 = 30 s on a worker thread; double-clicking spawns two threads |
| 🟡 Nit | `src-tauri/Cargo.toml:7` + `package.json:14` | `tauri-plugin-shell` and `@tauri-apps/plugin-shell` listed but JS never invokes shell commands directly |
| 🟣 Pre-existing | `src/voice-agent/desktop-tauri/` (no test files) | 0 frontend tests, 0 `cargo test` modules — risk concentrated in Rust IPC handlers |
| 🟣 Pre-existing | `.github/workflows/` (only `security-audit.yml`) | No CI for desktop build/dist consistency; "must `cargo build --release` after `npm run build`" is enforced only by human memory |
| 🟣 Pre-existing | `src-tauri/tauri.conf.json:11` | `"bundle.active": false` — no installers, no codesigning, no auto-update (likely by-design but worth confirming) |
| 🟣 Pre-existing | `package.json` (no `engines`, no `tsconfig.json`) | Node version unpinned; `@types/bun` devDep with zero `.tsx` files in tree |

### Extended reasoning — Important findings

**🔴 Important — `src-tauri/src/main.rs:1499-1522` — `quit` handler kills voice-agent without telemetry check**

CLAUDE.md operational rule: "Don't restart `jarvis-voice-agent.service` while a session is active. Check `~/.local/share/jarvis/turn_telemetry.db` for the latest `ts_utc`; if within 60s, ask the user first." The Tauri `quit` handler issues `systemctl --user stop jarvis-voice-agent`, sleeps 500 ms, then `app.exit(0)`. No telemetry check, no prompt, no log. A user clicking the tray "Quit" mid-conversation kills the in-flight specialist; the user hears nothing. Compare with `src/cli/src/commands/voice/restart.ts:80-89` (committed in `dd0cbef`), which honors the rule via `checkActiveSession()`.

**Suggested fix:** mirror the CLI's `checkActiveSession` — open `turn_telemetry.db` via `rusqlite`, check `SELECT ts_utc FROM turns ORDER BY ts_utc DESC LIMIT 1`, and if within 60 s either prompt the user or skip the systemctl stop. Telemeter the choice for diagnostics.

---

**🔴 Important — Dead voice assets (~97 MB) shipped on every release**

`package.json` lists `@ricky0123/vad-web`, `livekit-client`, `esbuild`, `@types/bun` as deps; `public/` ships `ort.*.{mjs,wasm}`, `silero_vad_v5.onnx`, `vad.worklet.bundle.min.js`. Voice work was migrated to the Python `jarvis-voice-client` (port 8767) — none of these JS deps or assets are imported anywhere in `src/`. `tauri.conf.json:7` (`frontendDist: "../dist"`) re-embeds them into the binary on every `cargo build --release`.

**Why it matters:** every release bundle pays ~97 MB of dead-weight WASM/ONNX. The build is also slower than it needs to be. Confused future contributor sees `livekit-client` in deps and assumes the Tauri app does its own WebRTC.

**Suggested fix:** drop the four deps from `package.json`, delete the `public/` voice assets, verify `npm run build && cargo build --release` produces a smaller binary. Verify nothing imports them via `rg "vad-web|livekit-client|esbuild|silero_vad" src/`.

---

**🔴 Important — Two WebSocket clients to the same `client=desktop`**

`src/App.jsx:78` opens a WS via `useJarvisWS` to `ws://127.0.0.1:8765/ws?client=desktop`. `src/components/ChatPanel.jsx:474-498` opens a SECOND WS to the same endpoint with the same query param. Bridge broadcasts `chat_response` to all clients matching the role; both Tauri sockets receive it. Result: `speech.speak()` from App AND a UI message render from ChatPanel for every reply. Race hazard: future Bridge logic that broadcasts "first connected client only" will silently route past one of the two — non-deterministic which.

**Suggested fix:** hoist the WS into a React Context (or App.jsx state), pass an event-handler-registration API down to ChatPanel, never open a second socket.

---

**🟡 Nits worth flagging together (token + CSP):**

`window.eval(format!(... \"{escaped}\"))` for bearer-token injection escapes only `\\` and `\"`. Combined with `csp: null` in `tauri.conf.json:36`, a future token source that introduces `</script>`, unicode quirks, or template literals turns into JS arbitrary code. Tauri 2 exposes `WebviewWindowBuilder::initialization_script(...)` for exactly this use case — same effect, no `eval` surface, plus a strict CSP would block exfil even if a script slipped through.

### Pre-existing pattern observations

- The Tauri app has **zero tests** (no `*.test.*`, no `cargo test` modules). The voice-agent runs 1018 tests in 21 s; the desktop has none. Risk concentrated in the Rust IPC handlers (`set_panel_rect`, `handle_open_browser`, key-store rewriting, `find_project_root` walks).
- The single repo workflow is `security-audit.yml`; nothing catches a `dist/` ↔ binary mismatch in CI. Combined with the documented gotcha "must run `cargo build --release` after `npm run build`," there's no automated guard.
- No code-signing config — Linux-only by deliberate constraint and bundles disabled, so consistent. Worth confirming this is permanent.

### Constraints honored — pass

- **Reactor sphere correctly absent** — `useVoiceClient.js:64` keeps `audioLevel=0`. The intentional 2026-05-XX removal is preserved.
- **No 3-column drift** — Tauri panel is a single drag-and-resize chat surface; the website target layout is correctly NOT replicated here.
- **No duplicated TTS/STT** — voice work delegated to Python `jarvis-voice-client:8767`. The only audio in the app is two short `AudioContext` UI chimes.

---

## Surprises across both surfaces

- **Out-of-scope CLI changes appeared mid-session** — `src/cli/src/{commands.ts,tools.ts,commands/voice/index.ts}` modifications and `commands/voice/{logs,restart}.ts` + `tools/Voice{Speak,AgentStatus}Tool/` additions were not in the session-start `git status` snapshot. Resolved 2026-05-08 by user authorization → committed as `dd0cbef` ("feat(cli): add voice-restart/voice-logs commands and Voice{Speak,AgentStatus} tools").
- **CLI's `voice-restart` command honors the operational rule the desktop's `quit` handler violates** (60-s `turn_telemetry.db` check). Use the CLI as a reference implementation when fixing the Tauri 🔴.

---

## Suggested fix order

1. **voice-agent 🔴** (5-line change) — read `JARVIS_SPECIALIST_NO_TOOL_RETRY_CEILING` at runtime, drop `importlib.reload` from the test
2. **voice-agent 🟡 × 4** — `_SAVE_CLAIM_RE` gate on confab extraction-evidence; tighten `_META_PARAPHRASE_RE`; comment the global; small test cleanup
3. **desktop-tauri 🔴 × 3** — telemetry-guarded quit handler; purge dead VAD assets + 4 deps; single-WS context
4. **desktop-tauri 🟡** — replace `window.eval` with `initialization_script`; set CSP; delete `:8766` heartbeat; switch `curl`-shell to `reqwest`; drop unused `shell` plugins
5. **desktop-tauri 🟣** — add a smoke `cargo build --release` CI step (catches the documented dist/binary gotcha); add at minimum 2-3 Rust unit tests for IPC commands

---

## Methodology

- voice-agent reviewed by `code-reviewer` agent (JARVIS-conventions tuned: tool gate, sanitizers, min_words, restart safety, no-co-author trailers)
- desktop-tauri reviewed by general-purpose agent with explicit context handover (load-bearing constraints from CLAUDE.md, dead reactor sphere note, build-flow gotcha, hub URL)
- Reviews ran in parallel; merged findings into this single document
- All file:line references verified against the working tree on 2026-05-08
- Voice-agent test suite re-run after review: 1018 passed, 2 skipped, 0 failed in 21.27 s

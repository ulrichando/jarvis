# JARVIS — project context for Claude

JARVIS is Ulrich's voice-first AI assistant. Real-time speech in, real-time speech out, with direct tools for desktop / browser / multi-step coding work. Runs on Linux (Kali) as a multi-process LiveKit Agents Python worker, fronted by a Tauri desktop UI.

> **Two layers of context:** this file is committed, project-wide, and loads in any session opened anywhere in the repo. Per-conversation evolving memory (user preferences, recent feedback, session-specific learnings) lives in `~/.claude/projects/-home-ulrich-Documents-Projects-jarvis/memory/MEMORY.md` — read it for ground-truth on user style + recent decisions. Don't duplicate that content here; this file is for things that stay true across sessions.

## Stack at a glance

| Layer | Tech | Where |
|---|---|---|
| Voice agent (the brain) | Python 3.13, LiveKit Agents framework | [src/voice-agent/](src/voice-agent/) |
| Desktop UI | Tauri (Rust + React/TS) | [src/desktop-tauri/](src/desktop-tauri/) |
| Web app | Next.js / React | [src/web/](src/web/) |
| CLI agent (`jarvis`) | TypeScript / Bun, Claude Code shape | [src/cli/](src/cli/) |
| Bridge | Bun/TypeScript HTTP+WS on `127.0.0.1:8765`, started by `src/cli/scripts/start-desktop.sh` when the desktop launches (no systemd unit; dies when desktop closes). Brokers HTTP→Chrome ext over WS. Auth required via `JARVIS_REQUIRE_LOCAL_AUTH=1` + `~/.jarvis/local-api-token.env` (added 2026-05-16 per global review §P0-1) | [src/cli/src/bridge/](src/cli/src/bridge/) |
| AI-native OS rice (Misty Scone) | Arch + custom desktop, copies cli/desktop-tauri (aspirational — `src/os/desktop/` not present in current checkout) | [src/os/desktop/](src/os/desktop/) |

JARVIS's `bash` tool runs as the local user (`ulrich`). **As of 2026-05-16 there is NO `/etc/sudoers.d/jarvis` NOPASSWD entry** — earlier docs claimed there was; live `sudo -n true` test confirmed it requires a password. Threat model: a misbehaving LLM / prompt injection through user mic can execute anything under the user's $HOME + run any tool without sudo. If you ever add the sudoers entry, document its scoped `Cmnd_Alias` here so future readers can audit the blast radius — do NOT restore a blanket NOPASSWD.

## Voice-agent architecture

[src/voice-agent/jarvis_agent.py](src/voice-agent/jarvis_agent.py) is the entrypoint (~5300 lines after the 2026-05-10 10/10 refactor extracted prompts, providers, sanitizers, and pipeline helpers into their own modules). It wires:

- **Supervisor agent** — Anthropic Claude Sonnet 4.6 (current pick, 2026-05-11's `e681914` promoted from Haiku 4.5 for orchestration reliability); tray-switchable across Groq/DeepSeek/OpenAI/Anthropic/Kimi via [providers/llm.py](src/voice-agent/providers/llm.py)::SPEECH_MODELS, with a FallbackAdapter cascade behind it. Owns conversation, routing, and ALL action via direct tools (no subagent layer). Its system prompt is assembled **SOUL-first** (2026-05-20 Hermes-style extraction): [prompts/soul.md](src/voice-agent/prompts/soul.md) (identity/voice/character — slot #1) + [prompts/supervisor.md](src/voice-agent/prompts/supervisor.md) (ops/routing only) + a volatile runtime-id block. Spec: [docs/superpowers/specs/2026-05-20-jarvis-soul-design.md](docs/superpowers/specs/2026-05-20-jarvis-soul-design.md).
- **Direct tools (no subagent layer)** — the `subagents/` tree was torn down in the 2026-05-20 rebuild (see MEMORY: Hermes→JARVIS capability rebuild) and never restored; there is **NO** `HandoffSubagent` / `DelegatedSubagent` / `transfer_to_*` / `delegate`. The supervisor's whole tool surface is REGISTRY-ONLY, discovered + adapted by [tools/_adapter.py](src/voice-agent/tools/_adapter.py)::`load_all_livekit_tools` from self-registering modules under [tools/](src/voice-agent/tools/) (each calls `registry.register(...)` against the [tools/registry.py](src/voice-agent/tools/registry.py) singleton; env-gated tools opt out via `check_fn`). The live action tools are:
  - **`computer_use(request)`** — vision→plan→act loop on the user's Linux X11 desktop using Anthropic's `computer_20251124` tool surface (Sonnet 4.6 with Opus 4.7 escalation). SEES the screen, so it handles screen-reading, GUI/multi-step work, dialogs, and minimized/occluded windows (restores from the panel). Now a direct tool (not a gated handoff). Loop owns its own audit trail in `~/.local/share/jarvis/turn_telemetry.db` (`computer_use_actions` table) + screenshot dump at `~/.local/share/jarvis/computer_use/screenshots/`. X11 only (no Wayland). Spec: [docs/superpowers/specs/2026-05-18-jarvis-computer-use-parity-design.md](docs/superpowers/specs/2026-05-18-jarvis-computer-use-parity-design.md). Soak: `bin/jarvis-cua-soak`. [tools/computer_use.py](src/voice-agent/tools/computer_use.py).
  - **`browser_task(task)`** — drives a real browser for a natural-language web task (open tab / navigate / search / post). [tools/browser.py](src/voice-agent/tools/browser.py); env-gated via `check_fn` (needs a browser provider available).
  - **`terminal(command)`** — shell. Blind named-action surface: launch apps by name (`setsid`), send known keystrokes (`xdotool`), run commands. [tools/terminal_tool.py](src/voice-agent/tools/terminal_tool.py).
  - **Files + code** — `read_file` / `write_file` / `patch` / `code_search` / `find_definitions` / `execute_code`.
  - **Plus** — `web_search` / `web_fetch`, `memory`, `session_search`, `schedule`, `todo`, `vuln_check`, `clarify`, and the skill tools (`skills_list` / `skill_view` / `skill_manage`). Multi-step coding work goes through plan-mode (`enter_plan_mode` / `exit_plan_mode`) + the file/terminal tools — no subagent.
  - Inline (in jarvis_agent.py) capabilities like `screenshot()` / `set_screen_share` / `ask_user_question` are NOT currently in the supervisor surface — they were dropped in the rebuild and will re-port into the registry one wave at a time (the screen-vision/read role is currently covered by `computer_use`).
- **Pipeline** — [pipeline/turn_router.py](src/voice-agent/pipeline/turn_router.py) classifies BANTER / TASK / REASONING / EMOTIONAL → picks LLM + TTS + interrupt tuning. [pipeline/turn_telemetry.py](src/voice-agent/pipeline/turn_telemetry.py) writes every turn to SQLite at `~/.local/share/jarvis/turn_telemetry.db`. [pipeline/turn_graph.py](src/voice-agent/pipeline/turn_graph.py) is the LangGraph slow-path dispatcher (default on; kill-switch `JARVIS_GRAPH_DISABLED=1`).
- **Sanitizers** ([sanitizers/](src/voice-agent/sanitizers/)) — installed as monkey-patches at import time, all idempotent:
  - `pycall.py` — suppresses tool-call-as-text leaks (`task_done(...)`, `<function>...</function>`, JSON arrays, `<tool_call>...`) (`task_done` is residual — no subagent emits it now; the guard stays as defense against any LLM hallucinating a tool-call shape into reply text)
  - `dsml.py` — DeepSeek meta-language sanitizer
  - `tool_name.py` — coerces tool name shapes
  - `deepseek_roundtrip.py`, `strict_schema_relax.py` — provider-shape fixups
  - `anthropic_strict_schema.py` — forces `additionalProperties: false` on every object in Anthropic tool schemas (Anthropic rejects without it; strict_schema_relax produces legacy shapes that omit it)
  - `handoff_text.py` — drops anticipatory text content from supervisor turns containing `transfer_to_*` / `delegate` (residual — no subagent emits these now; the guard stays as cheap defense against an LLM hallucinating the old shapes)
- **Resilience** ([resilience/](src/voice-agent/resilience/)) — circuit breaker, idle timeout, reconnect ladder, track guard, watchdog.
- **Confab detector** — [confab_detector.py](src/voice-agent/confab_detector.py) refuses to write turns to the conversation DB when the assistant claims success without tool evidence in the prior 10 messages.

## Operational rules — durable, override defaults

**Don't restart `jarvis-voice-agent.service` while a session is active.** Check `~/.local/share/jarvis/turn_telemetry.db` for the latest `ts_utc`; if within 60s, ask the user first. Restarting kills in-flight tool calls and the user hears nothing.

**No Co-Authored-By trailers on commits. No "🤖 Generated with Claude Code" or similar attribution in PR bodies.** Never.

**`src/cli/` is off-limits when working on desktop / voice-agent / web.** It's a separate codebase (the `jarvis` CLI agent). Ask before modifying.

**`src/cli/src/utils/claudeInChrome/` is reserved** for future Firefox/Chrome extension work. Don't delete.

**Anthropic Claude is the primary LLM provider (Haiku 4.5 for BANTER/TASK/EMOTIONAL; Sonnet 4.6 for REASONING) — chosen for prompt caching (~700ms TTFW cached vs ~2s on Groq).** Groq is the cross-provider fallback rung (`llama-3.1-8b-instant` for BANTER, `llama-3.3-70b-versatile` for TASK, etc.) and DeepSeek-v4-flash is the third rung. Don't hardcode any single provider — JARVIS is multi-provider via `providers/llm.py::build_dispatching_llm` with per-route env overrides (`JARVIS_{BANTER,TASK,REASONING,EMOTIONAL}_MODEL`).

**Bare "Jarvis" pings reply EXACTLY "Yes?"** — canonical, not "Yes, sir?" or "How can I help?". This is part of the voice persona; don't drift it.

**`/loop`, `/schedule`, follow-up agent offers**: don't end JARVIS replies with "want me to schedule a follow-up agent?" — the user finds it noisy.

**For desktop Tauri release builds:** `npm run build` alone does NOT ship JS changes. You must `cargo build --release` afterward to re-embed `dist/` into the binary. ([src/desktop-tauri/](src/desktop-tauri/))

**Voice reactor sphere is intentionally removed.** Don't re-add per-frame React state in voice UI — latency cost was too high.

**JARVIS website target layout is 3-column** (left nav / center chat / right preview). The Tauri desktop is a separate, smaller UI — don't conflate them.

## Active design decisions — the load-bearing constraints

- **Four load-bearing monkey-patches** on import (must not be removed): `deepseek_roundtrip`, `tool_name_sanitizer`, `AcousticTap`, `anthropic_strict_schema` (added 2026-05-11 — without it every Anthropic supervisor or fallback turn returns 400 `additionalProperties must be explicitly set to false`). See [sanitizers/__init__.py](src/voice-agent/sanitizers/__init__.py).
- **STAY-IN-SUPERVISOR rule.** For conversational/ambiguous/emotional user input ("Jarvis, mute" / "I love you" / vague fragments / yes-no replies), the supervisor just REPLIES — it does NOT reach for a tool. Tools are for concrete, nameable actions. (There are no subagents to transfer to; the supervisor handles everything directly.) See the "STAY-IN-SUPERVISOR RULE" section in [prompts/supervisor.md](src/voice-agent/prompts/supervisor.md).
- **`min_words` per route** ([pipeline/turn_router.py::_ROUTE_BASE](src/voice-agent/pipeline/turn_router.py)): **all routes 0 as of 2026-05-18** — VAD-only barge-in, no STT confirmation. Was BANTER=1, TASK=3, REASONING=3, EMOTIONAL=3 before. Reason: JARVIS uses Groq Whisper Large v3 Turbo which is non-streaming — interim transcripts never arrive, so "fire interrupt after N words" was equivalent to "fire interrupt after user stops talking", by which time the framework had already treated the utterance as a NEW turn. Live failure 2026-05-18 03:13 UTC: user said "stop" multiple times during a 23 s TTS; each was processed as the next user turn, never as barge-in. The min_duration values (BANTER=0.3 / TASK=0.4 / REASONING=0.5 / EMOTIONAL=0.6) still gate against breath/cough. Per-emotion overlay (+1 word for frustrated/sad) still applies. Kill-phrase regex at jarvis_agent.py bypasses min_words for deliberate "stop / wait / cancel" — but with min_words=0 the kill-phrase fast-path is largely redundant; left in place as belt-and-suspenders.
- **`resume_false_interruption` is OFF on purpose.** LiveKit's `pause()` is broken on the SFU output (gates new frames but doesn't clear the queue). Disabling routes every barge-in to `interrupt() → clear_buffer() → clear_queue()`, which actually works. Don't re-enable without verifying the SFU path.
- **STT chain is Deepgram Nova-3 streaming primary + Groq Whisper failover** (2026-05-18 per [docs/superpowers/specs/2026-05-18-barge-in-interrupt-fix-design.md](docs/superpowers/specs/2026-05-18-barge-in-interrupt-fix-design.md)). [`providers/stt.py::build_stt_chain`](src/voice-agent/providers/stt.py) returns a `FallbackAdapter([deepgram, whisper], vad=...)` when `DEEPGRAM_API_KEY` is set; degrades to Whisper-alone when key is unset (safe to ship without the key — no regression). Deepgram delivers partial transcripts every ~150 ms over WebSocket, which is what makes STT-confirmed barge-in actually work — Whisper Turbo is non-streaming (finals only after the user stops talking), so without Deepgram the framework's STT-confirmed barge-in path is dead. `FallbackAdapter` needs the prewarmed Silero VAD passed via `vad=` so it can auto-wrap the non-streaming Whisper as a streaming STT for chain compatibility. Key lives in `src/voice-agent/.env`; `pip install livekit-plugins-deepgram` (also in requirements.txt).
- **VAD-direct barge-in handler** ([jarvis_agent.py `_on_user_state_for_interrupt`](src/voice-agent/jarvis_agent.py)) calls `session.interrupt()` the moment Silero VAD's `user_state_changed → "speaking"` event fires AND `agent_state == "speaking"`. Before 2026-05-18 it only flagged for telemetry. Combined with the TTS-cancel below, perceived barge-in stop is ~250–400 ms instead of 1–3 s.
- **TTS upstream-cancel is wired into Groq Orpheus** (2026-05-18 per [docs/superpowers/specs/2026-05-18-barge-in-interrupt-fix-design.md](docs/superpowers/specs/2026-05-18-barge-in-interrupt-fix-design.md)). [`providers/tts.py::LoggingGroqChunkedStream._run`](src/voice-agent/providers/tts.py) catches `asyncio.CancelledError` around the `iter_chunks` loop and calls `resp.close()` immediately, aborting the Groq HTTP socket. Without this catch, Orpheus kept streaming WAV bytes for 1-3 s after `clear_buffer()` fired (the framework drops them, but the perceived "JARVIS keeps talking" UX was 5-15× over industry target). Don't remove the try/except without a replacement — the framework's `_run` task-level cancellation only stops `push()` calls; it doesn't proactively kill the underlying HTTP stream. Log line `[tts] Orpheus cancelled after Nms` confirms the path fired.
- **`turn_handling.interruption.mode="vad"` is explicit** (2026-05-18). Default (absent) would auto-detect → probe `livekit.cloud/agent-gateway` for AdaptiveInterruptionDetector → fail silently because JARVIS runs local LiveKit (`LIVEKIT_URL=ws://127.0.0.1:7880`) with no Cloud key → fall back to VAD anyway. Setting `mode="vad"` skips the wasted probe and makes intent clear. Switch to `"adaptive"` only when moving to LiveKit Cloud or self-hosting `agent-gateway`. The AudioSource output queue is already 200 ms in the framework (`livekit/agents/voice/room_io/_output.py:45`) — no override needed JARVIS-side.
- **`handoff_text_suppressor` walks the FULL chat_ctx**, not the last 15 items. The 15-item window dropped `task_done` past it in busy sessions, then suppressed all supervisor text indefinitely. Cost is O(n), bounded by `CTX_MAX_TURNS=80`. (Residual — no subagent emits `task_done`/`transfer_to_*` now; the suppressor stays as defense against the old shapes leaking into reply text.)
- **Confab-detector's tool-evidence lookback is 10 messages**. **As of 2026-05-19 (L2 confab fix), a bare `transfer_to_*` / `delegate` no longer counts as evidence by default** — required: a structured `tool_result` (role:`tool` OR `FunctionCallOutput` shape with `.output`+`.call_id`) OR a non-handoff tool_call in the lookback window. (Residual — `transfer_to_*`/`delegate` no longer exist as tools; the rule stays as defense, and the normal direct-tool path produces the structured `tool_result` it wants anyway.) The legacy permissive rule is preserved as the kill-switch `JARVIS_CONFAB_STRICT_DISABLED=1`. **Auto-extractor also counts as evidence**: a successful `extract_memory_from_turn` within the last 30 s grants evidence credit for "saved/remembered" claims (the v2 architecture has the extractor own memory writes off-band, so the supervisor's chat_ctx contains no tool call for save replies). See `pipeline.memory_extractor.has_recent_extraction_evidence` — unchanged by the L2 fix.
- **VAD threshold tuned 2026-05-04** to fix "first turn missed". Don't loosen it.
- **Kimi K2.6 voice supervisor is broken** ("web_search not in request.tools"). Entries gated behind `JARVIS_KIMI_VOICE_EXPERIMENTAL=1`. Don't re-enable without a fix.
- **Memory layer is 4-layered, NOT tool-choice driven.** [pipeline/memory_extractor.py](src/voice-agent/pipeline/memory_extractor.py) auto-extracts on turn boundary; [pipeline/turn_router.py::is_recall_query](src/voice-agent/pipeline/turn_router.py) force-routes recall queries; [sanitizers/denial_detector.py](src/voice-agent/sanitizers/denial_detector.py) blanks gaslighting outputs. The supervisor's `remember()` tool still exists but is a backup, not the primary write path. See [docs/superpowers/specs/2026-05-08-anti-gaslighting-memory-design.md](docs/superpowers/specs/2026-05-08-anti-gaslighting-memory-design.md). Extractor output is post-filtered against `_META_PARAPHRASE_RE` so LLM-narration shapes ("The user is X-ing", "It seems to be Y") never hit the memory store.

- **Token-aware chat_ctx pruning (added 2026-05-08).** When the pre-flight estimator reports `pressure=hard` (≥115k of 128k), `_prune_chat_ctx_for_budget` drops oldest non-system items (in tool-call-pair-aware fashion) until the estimate fits. Always preserves system prompt + tool-call/output pairs. Disable with `JARVIS_TOKEN_AWARE_PRUNE=0`. Live capture 2026-05-08T17:51 hit `est_tokens=293321 max=128000`; without pruning Groq silently truncates the head, removing JARVIS_INSTRUCTIONS, and the supervisor LLM degenerates into garbage output (the original capture showed hallucinated `delegate(role='summarize', ...)` calls — residual example; `delegate` no longer exists, but head-truncation still degrades the supervisor, which is the point of the rule).

- **Memory consolidator** (added 2026-05-08, default ON, kill: `JARVIS_MEMORY_CONSOLIDATOR=0`). [pipeline/memory_consolidator.py](src/voice-agent/pipeline/memory_consolidator.py) runs after every `JARVIS_MEMORY_CONSOLIDATE_EVERY_N` (default 10) successful per-turn extractions. Per-category LLM call (llama-3.1-8b-instant) returns clusters of near-duplicate memories; canonical content replaces them via the existing `_publish_event_async("memory.value.{upserted,removed}")` path. Memories younger than `JARVIS_MEMORY_CONSOLIDATE_YOUNG_EXCLUSION_S` (default 300 s) are excluded so active-conversation extractions don't get merged mid-flow. Single-event-loop concurrency guard. All env vars read at runtime.

- **Auto-mod loop is gated, audited, and reversible** (Spec B, 2026-05-24).
  `JARVIS_AUTOMOD_ENABLED=1` activates the pattern detector + `propose_code_mod`
  voice tool. `JARVIS_AUTOMOD_SPAWN_LIVE=1` enables the subprocess spawner
  (default OFF — shadow mode). Daily cap: 3 PRs (env: `JARVIS_AUTOMOD_DAILY_CAP`).
  HARD BLOCKLIST (never touched by auto-mod, defended in 3 layers — spawner
  prompt, `finalize.py` diff-check, `bin/jarvis-automod merge` re-validation):
  `src/voice-agent/sanitizers/`, `src/voice-agent/confab_detector.py`,
  `src/voice-agent/pipeline/automod/`, `src/voice-agent/pipeline/skill_review.py`,
  `src/voice-agent/prompts/soul.md`, `CLAUDE.md`,
  `.claude/rules/regression-prevention.md`, `MEMORY.md`, `USER.md`. Edits
  restricted to `src/voice-agent/` prefix. Manual merge via
  `bin/jarvis-automod merge <id>`; one-keystroke revert via
  `bin/jarvis-automod revert <sha>`. Spec:
  [docs/superpowers/specs/2026-05-24-jarvis-source-code-self-mod-design.md](docs/superpowers/specs/2026-05-24-jarvis-source-code-self-mod-design.md).

## Common workflows

| Task | Command |
|---|---|
| Restart voice agent | `systemctl --user restart jarvis-voice-agent.service` |
| Voice agent logs | `tail -f ~/.local/share/jarvis/logs/voice-agent.log` (JSON-formatted; rotated daily by `jarvis-log-rotate.timer`) |
| Voice agent test suite | `cd src/voice-agent && .venv/bin/python -m pytest tests/` |
| Telemetry inspection | `sqlite3 ~/.local/share/jarvis/turn_telemetry.db` (tables: `turns`, `launch_attempts`) |
| Periodic re-scoring | `bin/jarvis-soak-rescore.sh` |
| Desktop dev (Tauri) | `cd src/desktop-tauri && npm run tauri dev` |
| Desktop release | `npm run build && cargo build --release` (BOTH steps) |
| Run jarvis CLI | `bin/jarvis` (Claude-Code-shaped, has gstack skill access) |
| Bridge status | `pgrep -fa 'bridge/server.ts'` (Bun process, no systemd unit) |

## File-shape conventions

- **JARVIS's persona lives in [prompts/soul.md](src/voice-agent/prompts/soul.md)** — the "soul": `WHO YOU ARE`, `VOICE TEXTURE`, register, few-shot exemplars, and 13 other identity/voice/values sections (16 total), loaded as **slot #1** of the supervisor prompt ahead of the ops rules. **Edit voice / character / tone HERE, not in supervisor.md** (which is ops/routing only since the 2026-05-20 extraction). [pipeline/prompt_builder.py](src/voice-agent/pipeline/prompt_builder.py)::`load_soul` resolves an optional `~/.jarvis/SOUL.md` runtime override (injection-scanned + truncated) → git `soul.md` → hardcoded `DEFAULT_SOUL`. soul.md is git-only / hand-curated. `anchor_rules.md` is a consolidated persona-invariants reference alongside it (no longer auto-loaded — the rule-evolution system that consumed it was removed 2026-05-20; see [docs/superpowers/specs/2026-05-20-jarvis-self-improvement-rebuild-design.md](docs/superpowers/specs/2026-05-20-jarvis-self-improvement-rebuild-design.md)).
- **Leak-guard lives in [prompts/supervisor.md](src/voice-agent/prompts/supervisor.md)** — its opening `═══ NEVER WRITE THESE AS REPLY TEXT ═══` section blocks the LLM from emitting tool-call shapes (`task_done(...)` / `transfer_to_*` / `delegate` / `<function>...</function>` / JSON-array / bare-or-dotted `name(...)`) as voiced text, backed by the `pycall` sanitizer. NEVER reintroduce the butler "sir" suffix anywhere — the user has explicitly removed it across the codebase.

## Voice intelligence rubric

There's a 10-axis /100 rubric tracking voice-mode parity with Claude AI. Current score ~95/100. Phase commits update score deltas. See `project_voice_intelligence_rubric.md` in the memory dir for the axes.

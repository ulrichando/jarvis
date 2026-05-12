# JARVIS — project context for Claude

JARVIS is Ulrich's voice-first AI assistant. Real-time speech in, real-time speech out, with subagent handoffs for desktop / browser / multi-step coding work. Runs on Linux (Kali) as a multi-process LiveKit Agents Python worker, fronted by a Tauri desktop UI.

> **Two layers of context:** this file is committed, project-wide, and loads in any session opened anywhere in the repo. Per-conversation evolving memory (user preferences, recent feedback, session-specific learnings) lives in `~/.claude/projects/-home-ulrich-Documents-Projects-jarvis/memory/MEMORY.md` — read it for ground-truth on user style + recent decisions. Don't duplicate that content here; this file is for things that stay true across sessions.

## Stack at a glance

| Layer | Tech | Where |
|---|---|---|
| Voice agent (the brain) | Python 3.13, LiveKit Agents framework | [src/voice-agent/](src/voice-agent/) |
| Desktop UI | Tauri (Rust + React/TS) | [src/desktop-tauri/](src/desktop-tauri/) |
| Web app | Next.js / React | [src/web/](src/web/) |
| CLI agent (`jarvis`) | TypeScript / Bun, Claude Code shape | [src/cli/](src/cli/) |
| Browser extension | TS, talks to bridge over WS | [src/extensions/](src/extensions/) |
| Hub (jarvis-bridge.service) | Python on `127.0.0.1:8765`, brokers HTTP→Chrome ext via WS | [src/hub/](src/hub/) |
| AI-native OS rice (Misty Scone) | Arch + custom desktop, copies cli/desktop-tauri | [src/os/desktop/](src/os/desktop/) |

JARVIS has full `sudo NOPASSWD` root via `/etc/sudoers.d/jarvis` — every shell tool runs as root. Treat that as a load-bearing constraint, not a typo.

## Voice-agent architecture

[src/voice-agent/jarvis_agent.py](src/voice-agent/jarvis_agent.py) is the entrypoint (~5300 lines after the 2026-05-10 10/10 refactor extracted prompts, providers, sanitizers, and pipeline helpers into their own modules). It wires:

- **Supervisor agent** — Anthropic Claude Sonnet 4.6 (current pick, 2026-05-11's `e681914` promoted from Haiku 4.5 for orchestration reliability); tray-switchable across Groq/DeepSeek/OpenAI/Anthropic/Kimi via [providers/llm.py](src/voice-agent/providers/llm.py)::SPEECH_MODELS, with a FallbackAdapter cascade behind it. Owns conversation, routing, direct tools (bash/read/edit/write/plan-mode + memory + face ID + `screenshot()`).
- **Subagent registry** — [subagents/registry.py](src/voice-agent/subagents/registry.py) + [subagents/agent.py](src/voice-agent/subagents/agent.py). Two flavors:
  - `HandoffSubagent` (one `transfer_to_X` tool each): `desktop`, `browser`, `browser_v2`, `screen_share`. Desktop+browser enabled by default; browser_v2 self-disables until 3 known bugs are fixed; screen_share is gated behind `JARVIS_SUBAGENT_SCREEN_SHARE=1` (default OFF — Gemini Live's native-audio voice doesn't match JARVIS's Orpheus Troy, so screen-vision goes through `screenshot()` + Gemini Flash Lite instead, preserving one consistent voice).
  - `DelegatedSubagent` (single `delegate(role, task)` tool covers all): `summarize`, `weather`, `researcher`, `validator`, `code_reviewer`, `memory_recall`, `github`. **All seven gated off by default 2026-05-08** behind `JARVIS_SUBAGENT_<NAME>=1` env vars after live capture showed `summarize` hijacking trivial conversation ("Yeah", "Okay" → 5s of "The user is expressing gratitude" voiced back). Re-enable individually as the supervisor's `delegate` routing is hardened.
  - `planner` is retired (replaced by direct in-process tools + plan-mode).
  - Each spec has `transfer_tool`, `when_to_use`, `instructions`, `tool_factory`, `ack_phrase`, `max_history_items`, plus an optional `pre_transfer` hook (2026-05-11's `29006a6` — code-level invariant for handoff prerequisites the LLM might forget) and `tools_required=False` opt-out (for Live subagents whose RealtimeModel produces the work without function tools).
- **Pipeline** — [pipeline/turn_router.py](src/voice-agent/pipeline/turn_router.py) classifies BANTER / TASK / REASONING / EMOTIONAL → picks LLM + TTS + interrupt tuning. [pipeline/turn_telemetry.py](src/voice-agent/pipeline/turn_telemetry.py) writes every turn to SQLite at `~/.local/share/jarvis/turn_telemetry.db`. [pipeline/turn_graph.py](src/voice-agent/pipeline/turn_graph.py) is the LangGraph slow-path dispatcher (default on; kill-switch `JARVIS_GRAPH_DISABLED=1`).
- **Sanitizers** ([sanitizers/](src/voice-agent/sanitizers/)) — installed as monkey-patches at import time, all idempotent:
  - `pycall.py` — suppresses tool-call-as-text leaks (`task_done(...)`, `<function>...</function>`, JSON arrays, `<tool_call>...`)
  - `dsml.py` — DeepSeek meta-language sanitizer
  - `tool_name.py` — coerces tool name shapes
  - `deepseek_roundtrip.py`, `strict_schema_relax.py` — provider-shape fixups
  - `anthropic_strict_schema.py` — forces `additionalProperties: false` on every object in Anthropic tool schemas (Anthropic rejects without it; strict_schema_relax produces legacy shapes that omit it)
  - `handoff_text.py` — drops anticipatory text content from supervisor turns containing `transfer_to_*` / `delegate`
- **Resilience** ([resilience/](src/voice-agent/resilience/)) — circuit breaker, idle timeout, reconnect ladder, track guard, watchdog.
- **Confab detector** — [confab_detector.py](src/voice-agent/confab_detector.py) refuses to write turns to the conversation DB when the assistant claims success without tool evidence in the prior 10 messages.
- **Subagent tool gate** — [subagents/agent.py](src/voice-agent/subagents/agent.py): `task_done` is refused if no real (non-`task_done`) tool fired this handoff. Narrow bailout-phrase allowlist (`user changed topic`, `not a desktop task`, `wrong subagent`, `cannot accomplish`, `handing back to supervisor`, plus environmental gates `extension not connected` / `Google Chrome isn't available` / `tool unavailable`) lets wrongly-routed subagents exit cleanly. After `JARVIS_SUBAGENT_NO_TOOL_RETRY_CEILING` (default 3) consecutive refusals on a single handoff, the gate force-allows a graceful "Cannot accomplish — handing back to supervisor" so the user isn't trapped in silence.

## Operational rules — durable, override defaults

**Don't restart `jarvis-voice-agent.service` while a session is active.** Check `~/.local/share/jarvis/turn_telemetry.db` for the latest `ts_utc`; if within 60s, ask the user first. Restarting kills in-flight subagents and the user hears nothing.

**No Co-Authored-By trailers on commits. No "🤖 Generated with Claude Code" or similar attribution in PR bodies.** Never.

**`src/cli/` is off-limits when working on desktop / voice-agent / web.** It's a separate codebase (the `jarvis` CLI agent). Ask before modifying.

**`src/cli/src/utils/claudeInChrome/` is reserved** for future Firefox/Chrome extension work. Don't delete.

**Groq is the primary LLM provider, DeepSeek secondary.** Don't hardcode either — JARVIS is multi-provider (Groq, DeepSeek, OpenAI, Google, Anthropic, Kimi).

**Bare "Jarvis" pings reply EXACTLY "Yes?"** — canonical, not "Yes, sir?" or "How can I help?". This is part of the voice persona; don't drift it.

**`/loop`, `/schedule`, follow-up agent offers**: don't end JARVIS replies with "want me to schedule a follow-up agent?" — the user finds it noisy.

**For desktop Tauri release builds:** `npm run build` alone does NOT ship JS changes. You must `cargo build --release` afterward to re-embed `dist/` into the binary. ([src/desktop-tauri/](src/desktop-tauri/))

**Voice reactor sphere is intentionally removed.** Don't re-add per-frame React state in voice UI — latency cost was too high.

**JARVIS website target layout is 3-column** (left nav / center chat / right preview). The Tauri desktop is a separate, smaller UI — don't conflate them.

## Active design decisions — the load-bearing constraints

- **Four load-bearing monkey-patches** on import (must not be removed): `deepseek_roundtrip`, `tool_name_sanitizer`, `AcousticTap`, `anthropic_strict_schema` (added 2026-05-11 — without it every Anthropic supervisor or fallback turn returns 400 `additionalProperties must be explicitly set to false`). See [sanitizers/__init__.py](src/voice-agent/sanitizers/__init__.py).
- **Subagent tool-gate** refuses `task_done` with no real tool. Narrow bailout allowlist (`_BAILOUT_SUMMARY_RE` in [subagents/agent.py](src/voice-agent/subagents/agent.py)) lets wrongly-routed subagents exit; retry ceiling (`_NO_TOOL_RETRY_CEILING`, default 3) force-bails after consecutive refusals so the user isn't stuck in silence. Adding new subagents: their prompt must list the exact bailout phrases the gate honors.
- **STAY-IN-SUPERVISOR rule.** Conversational/ambiguous user input ("Jarvis, mute" / "I love you" / vague fragments / yes-no replies) stays in the supervisor — never `transfer_to_*`. Subagents need a nameable target. See the "STAY-IN-SUPERVISOR RULE" section in [prompts/supervisor.md](src/voice-agent/prompts/supervisor.md) (extracted from JARVIS_INSTRUCTIONS in 2026-05-10's `ce01e0a`).
- **`min_words` per route** ([pipeline/turn_router.py::_ROUTE_BASE](src/voice-agent/pipeline/turn_router.py)): BANTER=1, TASK=3, REASONING=3, EMOTIONAL=3. TASK was bumped 2→3 on 2026-05-07 to filter 2-word backchannels ("yeah okay" / "got it"). Kill-phrase regex at [jarvis_agent.py:4156](src/voice-agent/jarvis_agent.py#L4156) bypasses min_words for deliberate "stop / wait / cancel".
- **`resume_false_interruption` is OFF on purpose.** LiveKit's `pause()` is broken on the SFU output (gates new frames but doesn't clear the queue). Disabling routes every barge-in to `interrupt() → clear_buffer() → clear_queue()`, which actually works. Don't re-enable without verifying the SFU path.
- **`handoff_text_suppressor` walks the FULL chat_ctx**, not the last 15 items. The 15-item window dropped `task_done` past it in busy sessions, then suppressed all supervisor text indefinitely. Cost is O(n), bounded by `CTX_MAX_TURNS=80`.
- **Confab-detector's tool-evidence lookback is 10 messages**, and `transfer_to_*` / `delegate` count as evidence. The supervisor's `chat_ctx` doesn't see the subagent's internal `ext_*` calls, so the handoff alone proves the subagent had a chance to do work. **Auto-extractor also counts as evidence**: a successful `extract_memory_from_turn` within the last 30 s grants evidence credit for "saved/remembered" claims (the v2 architecture has the extractor own memory writes off-band, so the supervisor's chat_ctx contains no tool call for save replies). See `pipeline.memory_extractor.has_recent_extraction_evidence`.
- **VAD threshold tuned 2026-05-04** to fix "first turn missed". Don't loosen it.
- **Kimi K2.6 voice supervisor is broken** ("web_search not in request.tools"). Entries gated behind `JARVIS_KIMI_VOICE_EXPERIMENTAL=1`. Don't re-enable without a fix.
- **Memory layer is 4-layered, NOT tool-choice driven.** [pipeline/memory_extractor.py](src/voice-agent/pipeline/memory_extractor.py) auto-extracts on turn boundary; [pipeline/turn_router.py::is_recall_query](src/voice-agent/pipeline/turn_router.py) force-routes recall queries; [sanitizers/denial_detector.py](src/voice-agent/sanitizers/denial_detector.py) blanks gaslighting outputs. The supervisor's `remember()` tool still exists but is a backup, not the primary write path. See [docs/superpowers/specs/2026-05-08-anti-gaslighting-memory-design.md](docs/superpowers/specs/2026-05-08-anti-gaslighting-memory-design.md). Extractor output is post-filtered against `_META_PARAPHRASE_RE` so LLM-narration shapes ("The user is X-ing", "It seems to be Y") never hit the memory store.

- **Token-aware chat_ctx pruning (added 2026-05-08).** When the pre-flight estimator reports `pressure=hard` (≥115k of 128k), `_prune_chat_ctx_for_budget` drops oldest non-system items (in tool-call-pair-aware fashion) until the estimate fits. Always preserves system prompt + tool-call/output pairs. Disable with `JARVIS_TOKEN_AWARE_PRUNE=0`. Live capture 2026-05-08T17:51 hit `est_tokens=293321 max=128000`; without pruning Groq silently truncates the head, removing JARVIS_INSTRUCTIONS, and the supervisor LLM degenerates into hallucinated `delegate(role='summarize', ...)` for every utterance.

- **Memory consolidator** (added 2026-05-08, default ON, kill: `JARVIS_MEMORY_CONSOLIDATOR=0`). [pipeline/memory_consolidator.py](src/voice-agent/pipeline/memory_consolidator.py) runs after every `JARVIS_MEMORY_CONSOLIDATE_EVERY_N` (default 10) successful per-turn extractions. Per-category LLM call (llama-3.1-8b-instant) returns clusters of near-duplicate memories; canonical content replaces them via the existing `_publish_event_async("memory.value.{upserted,removed}")` path. Memories younger than `JARVIS_MEMORY_CONSOLIDATE_YOUNG_EXCLUSION_S` (default 300 s) are excluded so active-conversation extractions don't get merged mid-flow. Single-event-loop concurrency guard. All env vars read at runtime.

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
| Hub status | `systemctl --user status jarvis-bridge.service` |

## File-shape conventions

- **Voice-agent subagent instructions** ([subagents/desktop.py](src/voice-agent/subagents/desktop.py), [subagents/browser.py](src/voice-agent/subagents/browser.py)) include `═══ NEVER WRITE PROTOCOL SHAPES AS REPLY TEXT ═══` sections — these block the LLM from emitting `task_done(...)`/`<function>...</function>`/JSON-array tool-call leaks as voiced text. Mirror the section when adding new subagents.
- **Subagent `ack_phrase`** is the only supervisor-side voice the user hears between the handoff and the subagent's `task_done` summary. Strings live in [subagents/_ack_phrases.py](src/voice-agent/subagents/_ack_phrases.py); keep them crisp and sir-free per the 2026-05-09 drop-butler-register overhaul ("Right away." / "On it." / "One sec." / "Looking into it."). NEVER reintroduce the butler "sir" suffix — the user has explicitly removed it across the codebase.
- **Adding a new subagent:** see [subagents/HOW_TO_ADD_A_SUBAGENT.md](src/voice-agent/subagents/HOW_TO_ADD_A_SUBAGENT.md). Terminology rule: use "subagent" everywhere (code, comments, prompts, commits) — never "specialist" — per the rename in 2026-05-11's `c2dfa40` + `af90cc0`.

## Voice intelligence rubric

There's a 10-axis /100 rubric tracking voice-mode parity with Claude AI. Current score ~95/100. Phase commits update score deltas. See `project_voice_intelligence_rubric.md` in the memory dir for the axes.

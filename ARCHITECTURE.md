# JARVIS — Architecture Overview

> For the authoritative per-module detail and load-bearing design decisions,
> read [`CLAUDE.md`](CLAUDE.md). For a full file-tree inventory with line
> counts and health notes, see
> [`docs/2026-05-17-jarvis-repo-map.md`](docs/2026-05-17-jarvis-repo-map.md).
> This document is a one-page orientation guide; links above are the source of truth.

---

## Multi-process model

JARVIS is not a single process. Five long-running processes cooperate:

| Process | Language / framework | Systemd unit |
|---|---|---|
| **LiveKit SFU** | Go (bundled binary) | `livekit-server.service` |
| **Voice agent** | Python 3.13 / LiveKit Agents | `jarvis-voice-agent.service` |
| **Voice client** | Python / LiveKit SDK | `jarvis-voice-client.service` |
| **Bridge** | TypeScript / Bun | started by `start-desktop.sh`; no unit |
| **Desktop UI** | Tauri (Rust + React) | launched by bridge script |

The **voice agent** is the brain. Everything else is either a UI layer or a
data-routing layer.

The **web app** (`src/web/`) is a Next.js development server that reads
local JARVIS state (conversations, logs, telemetry) and proxies to the
bridge. It is not a required runtime process.

---

## Data flow

```
Microphone
    │ Silero VAD (frame-level)
    ▼
STT chain
    ├─ Deepgram Nova-3 (streaming, ~150 ms partials)  [primary]
    └─ Groq Whisper Large v3 Turbo (final-only)       [fallback]
    │
    ▼
Turn router  (src/voice-agent/pipeline/turn_router.py)
    │ classifies: BANTER / TASK / REASONING / EMOTIONAL
    │ selects LLM + TTS + interrupt parameters
    ▼
Supervisor LLM
    ├─ Anthropic Claude Sonnet 4.6            [primary]
    ├─ Groq llama-3.3-70b / llama-3.1-8b     [first fallback]
    └─ DeepSeek-v4-flash                      [second fallback]
    │ (provider cascade: providers/llm.py::build_dispatching_llm)
    │
    ├─ Tool calls → self-registering tool registry (tools/)
    │   ├─ computer_use   (X11 desktop GUI automation)
    │   ├─ browser_task   (Chrome/Playwright web automation)
    │   ├─ terminal       (named-action shell surface)
    │   ├─ read/write/patch/code_search/execute_code
    │   ├─ web_search / web_fetch
    │   ├─ memory / session_search
    │   ├─ schedule / todo / vuln_check
    │   ├─ dispatch_agent (out-of-process subagent, 2026-05-27)
    │   └─ skills_list / skill_view / skill_manage
    │
    ▼
TTS — Groq Orpheus (streaming WAV, upstream-cancel on barge-in)
    │
Speaker
```

### Memory

Memory is file-backed and tool-driven (the turn-boundary auto-extractor and
the consolidator were removed in the 2026-05-20 rebuild):
- Three curated stores under `~/.jarvis/memories/` — `USER.md` /
  `MEMORY.md` / `PROCEDURES.md` — char-budgeted, written only via the
  `memory` tool (`pipeline/file_memory.py`), and injected as a frozen
  snapshot at session start so the prompt prefix-cache stays stable.
- Cross-session deep recall is the `recall` tool over a pluggable provider
  (`pipeline/memory_provider.py`, selected by `JARVIS_MEMORY_PROVIDER`);
  the live backend is self-hosted honcho, which auto-syncs every message
  off the turn path.
- `recall_conversation` / `session_search` search past transcripts
  (`~/.jarvis/conversations.db` / `turn_telemetry.db`).
- Recall queries are force-routed to the memory read path by the turn router.

---

## Turn pipeline internals

```
user utterance
    │
    ├─ [kill-phrase fast-path]  "stop / wait / cancel" → immediate interrupt
    │
    ▼
turn_router.py  →  route label + LLM/TTS config
    │
    ▼
LangGraph slow-path dispatcher (pipeline/turn_graph.py)
    │  [kill-switch: JARVIS_GRAPH_DISABLED=1]
    │
    ▼
Supervisor LLM + tool loop
    │
    ├─ Sanitizer layer (monkey-patched at import time, all idempotent)
    │   ├─ pycall.py          — strips tool-call shapes from reply text
    │   ├─ anthropic_strict_schema.py — enforces additionalProperties: false
    │   ├─ dsml.py            — DeepSeek meta-language
    │   ├─ tool_name.py       — coerces tool name shapes
    │   ├─ deepseek_roundtrip.py, strict_schema_relax.py
    │   └─ handoff_text.py    — drops legacy transfer_to_* / delegate text
    │
    ├─ Confab detector (confab_detector.py)
    │   — refuses to record "success" without real tool-result evidence
    │
    ▼
turn_telemetry.py  →  SQLite at ~/.local/share/jarvis/turn_telemetry.db
    │
    ▼
TTS → speaker
```

---

## Multi-provider LLM

JARVIS never hardcodes a single LLM provider. Per-route overrides:

| Route | Default | Override env var |
|---|---|---|
| BANTER | Anthropic Claude Haiku 4.5 | `JARVIS_BANTER_MODEL` |
| TASK | Anthropic Claude Haiku 4.5 | `JARVIS_TASK_MODEL` |
| REASONING | Anthropic Claude Sonnet 4.6 | `JARVIS_REASONING_MODEL` |
| EMOTIONAL | Anthropic Claude Haiku 4.5 | `JARVIS_EMOTIONAL_MODEL` |

A `FallbackAdapter` cascade sits behind every route: Anthropic → Groq →
DeepSeek. Provider selection and fallback order are configured in
`src/voice-agent/providers/llm.py`. The tray icon lets the user switch the
supervisor model at runtime (written to `~/.jarvis/voice-model`).

---

## Subtree locations

| Subtree | Path | Primary language |
|---|---|---|
| Voice agent | `src/voice-agent/` | Python 3.13 |
| Desktop UI | `src/desktop-tauri/` | Rust + React/JSX |
| Web app | `src/web/` | TypeScript / Next.js |
| CLI agent | `src/cli/` | TypeScript / Bun |
| Chrome extension | `src/cli/src/utils/claudeInChrome/` (reserved for future work) | JavaScript (MV3) |
| Android app | `src/android/` | Kotlin + NDK |
| ACP adapter | `src/voice-agent/acp_registry/` | Python |

---

## Key design constraints (summary)

- **Barge-in** uses VAD-direct mode (`min_words=0` on all routes); Deepgram
  streaming partials are required for STT-confirmed interrupts.
- **~13 load-bearing monkey-patches** install at import and must not be
  removed (grep `\.install()` in `jarvis_agent.py` for the live list); the
  provider-critical four are `deepseek_roundtrip`, `tool_name`,
  `strict_schema_relax`, and `anthropic_strict_schema`.
- **No in-process HandoffSubagent layer** — the torn-down `subagents/` tree
  was replaced by direct registry tools and the out-of-process
  `dispatch_agent` tool (2026-05-27).
- **Tauri release builds** require BOTH `npm run build` AND
  `cargo build --release` — the JS bundle must be re-embedded into the binary.
- **X11 only** — `computer_use` and screen-share do not support Wayland.

---

See [`CLAUDE.md`](CLAUDE.md) and [`docs/`](docs/) for deeper detail on each
subsystem, the full list of env-var kill-switches, and the active design
decision log.

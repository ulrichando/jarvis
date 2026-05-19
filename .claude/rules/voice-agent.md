---
description: Voice-agent load-bearing constraints (sanitizers, tool gate, min_words, restart safety)
paths:
  - src/voice-agent/**
---

# Voice-agent rules (loads only when working in src/voice-agent/**)

These constraints are load-bearing. Don't remove or weaken without the user's sign-off.

**Four monkey-patches must remain installed.** [jarvis_agent.py](../../src/voice-agent/jarvis_agent.py) installs `deepseek_roundtrip`, `tool_name_sanitizer`, the AcousticTap, and `anthropic_strict_schema` (added 2026-05-11 — Anthropic rejects any tool whose object nodes don't set `additionalProperties: false`, and `strict_schema_relax` emits legacy schemas that omit it; the sanitizer fixes the schema post-build). Removing any one breaks DeepSeek + Groq + Anthropic reliability. They're idempotent — safe to re-import, never to delete.

**Subagent tool gate refuses no-tool `task_done`.** [subagents/agent.py](../../src/voice-agent/subagents/agent.py)'s `task_done` walks chat_ctx since handoff start; if no real (non-`task_done`) tool fired, the call is refused. Narrow bailout-phrase allowlist (`_BAILOUT_SUMMARY_RE`) covers `user changed topic`, `not a desktop task`, `wrong subagent`, `cannot accomplish`, `handing back to supervisor`. New subagents' prompts must list these EXACT phrases — freelance phrasings get refused and the subagent loops.

**STAY-IN-SUPERVISOR rule** lives in [prompts/supervisor.md](../../src/voice-agent/prompts/supervisor.md) (extracted from JARVIS_INSTRUCTIONS in 2026-05-10's `ce01e0a`). Conversational/ambiguous input ("Jarvis, mute" / vague fragments / yes-no) stays in supervisor — never `transfer_to_*`. Subagents need a nameable target. This rule was added 2026-05-07 after live failure where 11 desktop-subagent refusals in 2 minutes produced "I'm here to assist with desktop tasks" boilerplate.

**Per-route `min_words` lives in [pipeline/turn_router.py::_ROUTE_BASE](../../src/voice-agent/pipeline/turn_router.py).** BANTER=1, TASK=3, REASONING=3, EMOTIONAL=3. TASK was bumped 2→3 on 2026-05-07 to filter 2-word backchannels. Single-word "stop / wait / cancel" still fires via the kill-phrase regex at [jarvis_agent.py:4156](../../src/voice-agent/jarvis_agent.py#L4156), bypassing min_words.

**`resume_false_interruption` is OFF on purpose.** LiveKit's `pause()` is broken on the SFU output (gates new frames, doesn't clear queue). Disabling routes every barge-in to `interrupt() → clear_buffer() → clear_queue()`. Don't re-enable without verifying the SFU path. Comment + assignment at [jarvis_agent.py:4538-4555](../../src/voice-agent/jarvis_agent.py#L4538-L4555).

**`handoff_text_suppressor` walks the FULL chat_ctx**, not the last 15. The 15-item window dropped `task_done` past it in busy sessions, then suppressed all supervisor text indefinitely. Cost is O(n), bounded by `CTX_MAX_TURNS=80`.

**Confab-detector tool-evidence lookback is 10 messages.** **Strict-default since 2026-05-19 (L2 confab fix):** bare `transfer_to_*` / `delegate` does NOT count as evidence — required: a structured `tool_result` (role:'tool' OR `FunctionCallOutput` shape) OR a non-handoff tool_call. Legacy permissive rule survives as kill-switch `JARVIS_CONFAB_STRICT_DISABLED=1`. The "supervisor's chat_ctx doesn't see subagent internals" concern is addressed by L1's pycall synthesis (commit `bc7e0087`) which lands a synthesized `FunctionCallOutput` pair in chat_ctx when the subagent's tool came through as text-content.

**Don't restart `jarvis-voice-agent.service` while a session is active.** Check `~/.local/share/jarvis/turn_telemetry.db` for the latest `ts_utc`; if within 60s, ask the user first.

**TTS is Groq Orpheus via `_LoggingGroqTTS` shim.** Don't replace with ElevenLabs (removed 2026-05-01) or other providers without coordinating the dispatcher in [pipeline/dispatching_tts.py](../../src/voice-agent/pipeline/dispatching_tts.py).

**Voice-agent has its own `.venv`** at [src/voice-agent/.venv/](../../src/voice-agent/.venv/). Don't use the project root venv or system Python — the voice-agent's livekit-agents version is pinned.

**Tests:** `cd src/voice-agent && .venv/bin/python -m pytest tests/`. 800+ tests; full suite runs in ~25s.

**Logs live at `~/.local/share/jarvis/logs/voice-agent.log`** (NOT `/tmp/...` anymore — was switched 2026-05-07 because `/tmp` rotates aggressively and we lost the 11:57 SFU disconnect evidence). Rotated daily by `jarvis-log-rotate.timer` (50MB cap or 24h, gzip, keep 14). Old archives: `~/.local/share/jarvis/logs/voice-agent.log.<stamp>.gz`. Use `zgrep` for historical searches across archives.

**`total_audio_ms` was hardcoded to 0 across 1167 turns** until 2026-05-07. Now wired via `_on_agent_state` accumulator in [jarvis_agent.py](../../src/voice-agent/jarvis_agent.py): captures sum of all `speaking → not-speaking` segments per turn, including partial captures on barge-in. Resets after each turn-write. New rule: **never hardcode a telemetry field to 0 with a "not measured in v1" comment** — wire it or drop the column. Blind metrics make debugging impossible.

# Conversation Modes — design

**Date:** 2026-06-29
**Status:** Design (approved shape; pending spec review → plan)

## Context

Today the voice agent and CLI read **separate, single-purpose settings files**
under `~/.jarvis/`:

| File | Controls |
|---|---|
| `voice-model` | voice supervisor LLM (the "Speech" line) |
| `cli-model` | CLI / tool model (the tray's "Conversation mode" label reflects this) |
| `tts-provider` | TTS engine + voice (e.g. `kokoro:af_bella`) |
| `voice-tts-voice` | TTS voice id |
| `active-mode` | backend selector: `jarvis` \| `gemini` \| `openai` (orthogonal) |
| `voice-mode` | `cloud` \| `local` (orthogonal) |

Because these are independent, "switch the conversation mode to Claude" only
moved `cli-model` — the voice supervisor and TTS stayed put. The user expects
one pick to move **the whole stack together** and wants to define their own
named presets.

## Goal

A **conversation mode** is a named preset that bundles a default
`{voice LLM, CLI/tool model, TTS voice, allowed tools}`. Selecting a mode sets
all of them at once. Users can create/edit/delete custom modes.

**Non-goals (this iteration):** per-mode system prompt/persona; cross-machine
sync. The `active-mode` (jarvis/gemini/openai) backend selector stays orthogonal
and untouched — a conversation mode operates within the `jarvis` backend. (A
mode *does* set `voice-mode` cloud/local, which is how the Local mode works.)

## Architecture

A mode is a **layer on top of** the existing single-setting files, not a
replacement: selecting a mode *writes* `voice-mode` / `voice-model` /
`cli-model` / `tts-provider` / `voice-tts-voice` and the tool allowlist. Every
existing consumer (`read_speech_model`, the tray labels, `build_dispatching_tts`,
`_local_voice_mode`, …) keeps reading the same files it does today — they just
get written as a set.

### Components (each independently testable)

1. **`pipeline/conversation_modes.py`** (Python, new) — the mode store.
   - `~/.jarvis/modes.json` is the single source of truth.
   - API: `load() -> ModesDoc`, `list_modes()`, `active_mode()`,
     `resolve(mode_id) -> dict`, `apply(mode_id)` (writes `voice-mode` + the
     model/voice files + the allowlist file), `create/update/delete(mode)`.
   - Lock-protected atomic writes (mirrors `pipeline/file_memory.py`).
   - Seeds built-ins on first run if the file is missing.

2. **Tool-allowlist filter** (Python, small addition to
   `tools/_adapter.py::load_all_livekit_tools`) — after the existing
   `check_fn` skip (line ~233), also skip any tool whose name is not in the
   active mode's `allowed_tools`. `null`/absent allowlist = all tools. A
   `CORE_TOOLS` set (e.g. `clarify`, `memory`) is **always** kept so a mode
   can't brick itself. The allowlist is read from a tiny
   `~/.jarvis/mode-allowed-tools` file (newline-separated names; absent = all),
   written by `conversation_modes.apply()`.

3. **`/mode*` HTTP endpoints** (`voice_client_http_api.py`, port 8767) — thin
   layer over `conversation_modes.py`:
   - `GET /modes` → `{active, modes:[…]}`
   - `POST /mode` `{id}` → apply + restart agent (reuses the existing
     `/voice-model` restart path)
   - `POST /mode/create|update|delete` → persist a custom mode
   Mirrors the existing `/voice-model` + `/cli-model` handlers.

4. **Web mode editor** (`src/web`, new settings section) — create/edit a mode:
   pick voice LLM, CLI model, TTS voice, and toggle the tool list. Reuses the
   existing settings UI + model/provider pickers; calls `/mode/*`. (A tray menu
   can't host a 20-tool toggle editor, so creation/editing lives here.)

5. **Tray mode list** (`desktop-tauri/.../main.rs`, Rust) — the existing
   "Conversation mode" submenu becomes a list of **modes** (from `GET /modes`)
   with a ✓ on the active one; selecting POSTs `/mode`. A "Manage modes…" item
   opens the web editor.

### Data model — `~/.jarvis/modes.json`

```json
{
  "active": "deepseek",
  "modes": [
    {
      "id": "deepseek",
      "label": "DeepSeek",
      "voice_mode": "cloud",
      "voice_model": "deepseek-v4-flash",
      "cli_model": "deepseek-v4-pro",
      "tts_provider": "kokoro:af_bella",
      "tts_voice": "af_bella",
      "allowed_tools": null
    },
    {
      "id": "claude",
      "label": "Claude",
      "voice_mode": "cloud",
      "voice_model": "claude-haiku-4-5",
      "cli_model": "claude-sonnet-4-6",
      "tts_provider": "kokoro:af_bella",
      "tts_voice": "af_bella",
      "allowed_tools": null
    },
    {
      "id": "local",
      "label": "Local (on-device)",
      "voice_mode": "local",
      "voice_model": null,
      "cli_model": "ollama-qwen3-30b-a3b",
      "tts_provider": "kokoro:af_heart",
      "tts_voice": "af_heart",
      "allowed_tools": null
    }
  ]
}
```

Built-in seeds: **DeepSeek**, **Claude**, and **Local (on-device)** — each
internally consistent (DeepSeek mode runs DeepSeek end to end, Claude runs
Claude end to end, Local runs the on-device stack). Seeded but editable.

`voice_mode` (`cloud` \| `local`) is part of a mode: selecting **Local** writes
`voice-mode=local`, which flips the agent's STT+LLM+TTS to on-device
(faster-whisper + local LLM + Kokoro) via the existing `_local_voice_mode` path
in `jarvis_agent.py`. When `voice_mode=local`, `voice_model` is ignored (the
local path owns model selection), so it's `null`. For cloud modes,
`voice_model`/`cli_model` must be valid registry ids (`SPEECH_MODELS` for voice,
`JARVIS_MODEL_DEFINITIONS` for CLI).

## Data flow

**Select a mode:** tray/web `POST /mode {id}` → `conversation_modes.apply(id)`
resolves the preset → writes `voice-mode`, `voice-model`, `cli-model`,
`tts-provider`, `voice-tts-voice`, `mode-allowed-tools`, sets `active` → restart
the voice agent → next session: `_local_voice_mode` / `read_speech_model` pick
the new stack, the dispatcher/TTS read the new files, `load_all_livekit_tools`
filters by the new allowlist.

**Create/edit a mode:** web editor collects `{label, voice_model, cli_model,
tts_provider, tts_voice, allowed_tools}` → `POST /mode/create|update` →
appended/updated in `modes.json`. Delete blocks removing the active mode (must
switch first).

## Error handling

- **Invalid model id in a mode** → `read_speech_model` already falls back to
  `DEFAULT_SPEECH_MODEL`; surface a warning, don't crash.
- **Empty/over-restrictive allowlist** → `CORE_TOOLS` always kept.
- **`modes.json` missing/corrupt** → reseed built-ins (corrupt file backed up to
  `.bak`), log loudly.
- **Concurrent writes** → file lock (same pattern as `file_memory.py`).
- **Delete active mode** → 409, require switching first.

## Testing

- `conversation_modes.py`: save/load round-trip; `resolve` returns the right
  fields; `apply` writes exactly the expected files; atomic write under a lock;
  reseed on missing/corrupt file.
- Allowlist filter: `load_all_livekit_tools` with a mode allowlist loads only
  allowed tools + `CORE_TOOLS`; `null` loads all (no regression).
- `/mode*` endpoints: select applies + triggers the restart path;
  create/update/delete persist; delete-active → 409.
- Web editor: a thin integration test that POST `/mode/create` round-trips.

## Out of scope / future

- Per-mode persona/prompt overlay (`SOUL.md` per mode).
- Modes for the `gemini`/`openai` out-of-process backends.
- Cross-machine mode sync.

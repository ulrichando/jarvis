# Jarvis voice that "sounds like Claude"

**Status:** shipped 2026-04-23
**Scope:** swap the Jarvis desktop voice TTS from Groq Orpheus (`daniel`) to a
local Kokoro-FastAPI container (`bm_george`) to get a warm, measured,
slightly-British male voice similar to the Claude.ai voice mode.

## Problem

Two issues, one change.

1. Orpheus (`daniel`) didn't sound like Claude — closer to an American voice
   actor than the calm British-inflected voice used in Claude.ai.
2. Groq's streaming WAV response returns a malformed RIFF header (chunk size
   `0xFFFFFFFF`). WebKit2GTK's `<audio>` decoder stutters and pops at frame
   boundaries, which Ulrich heard as "cranky" audio. A buffer-and-rewrite
   workaround (`fixAndServeWav`) was already in place but the root cause was
   the container format, not the helper.

## Solution

Run **Kokoro-FastAPI** (`ghcr.io/remsky/kokoro-fastapi-cpu:latest`) as a
local Docker service on `127.0.0.1:8880`. It exposes an OpenAI-compatible
`/audio/speech` endpoint, supports MP3 output (self-framing over HTTP — no
container-header workaround needed), and ships with 60+ voices including
`bm_george` (British male).

STT stays on Groq (Whisper-large-v3-turbo). Only TTS moves.

## Architecture

```
mic → webview → /turn (speech sidecar :8766)
                 ├── STT   → Groq Whisper      (unchanged)
                 ├── chat  → proxy :4000       (unchanged)
                 └── TTS   → Kokoro :8880  ←───── NEW
                              │
                              └─ MP3 back → webview <audio>
```

- Speech sidecar: `src/voice-agent/desktop-tauri/server/speech.ts`
- Launcher: `src/voice-agent/desktop-tauri/scripts/launch.sh` — ensures kokoro-tts
  container is running before the sidecar starts.
- Container: `docker run -d --name kokoro-tts --restart unless-stopped -p 127.0.0.1:8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:latest`

## Configuration

All tunable via env vars; defaults match the shipped setup so nothing in
`.env.local` is required.

| env var | default | purpose |
|---|---|---|
| `JARVIS_TTS_BASE`   | `http://127.0.0.1:8880/v1` | TTS provider base URL |
| `JARVIS_TTS_KEY`    | `not-needed`               | bearer token (Kokoro ignores it) |
| `JARVIS_TTS_MODEL`  | `kokoro`                   | model id sent to the provider |
| `JARVIS_TTS_VOICE`  | `bm_george`                | voice id |
| `JARVIS_TTS_FORMAT` | `mp3`                      | `mp3` / `wav` / `opus` / `flac` / `aac` / `pcm` |

## Code changes

- [`src/voice-agent/desktop-tauri/server/speech.ts`](../../../src/voice-agent/desktop-tauri/server/speech.ts)
  - Split TTS config from STT (new `TTS_BASE` / `TTS_KEY` / `TTS_FORMAT`
    constants). Voice default changed from `daniel` → `bm_george`, model
    `canopylabs/orpheus-v1-english` → `kokoro`.
  - New `tts(text, voice)` helper centralises the upstream call.
  - `fixAndServeWav` replaced with `serveAudio` — passes MP3 through
    untouched, still rewrites WAV headers if `TTS_FORMAT=wav` (rollback
    path to Groq).
  - Startup log shows actual provider endpoints for both STT and TTS.

- [`src/voice-agent/desktop-tauri/scripts/launch.sh`](../../../src/voice-agent/desktop-tauri/scripts/launch.sh)
  - New block before speech-sidecar start: if the kokoro-tts container
    exists but isn't responding, `docker start` it and wait on /health.
  - No auto-create — the `docker run` is a one-shot setup step done by
    hand (see Configuration above).

## Performance

- **CPU inference on i9-10885H:** ~0.54 RTF (end-to-end through the sidecar,
  ~2.7s for a 5-second reply). Acceptable for a voice assistant where the
  LLM latency dominates.
- **GPU path available** (not enabled): install `nvidia-container-toolkit`,
  swap the image to `kokoro-fastapi-gpu:latest`, expect ~0.1 RTF.

## Rollback

Set in `src/cli/.env.local`:

```sh
JARVIS_TTS_BASE=https://api.groq.com/openai/v1
JARVIS_TTS_KEY=$GROQ_API_KEY
JARVIS_TTS_MODEL=canopylabs/orpheus-v1-english
JARVIS_TTS_VOICE=daniel
JARVIS_TTS_FORMAT=wav
```

Restart the speech sidecar. `serveAudio` will apply `fixAndServeWav` on WAV
format, restoring pre-Kokoro behaviour. No code revert needed.

## Out of scope

- XTTS-v2 / voice cloning of an actual Claude voice sample — legally murky
  and not required to match the vibe.
- Changes to `src/cli/` (enforced CLI boundary).
- Changes to `src/os/desktop/` (misty-scone, separate copy of the stack).
- Client-side changes to `useSpeech.js` — MP3 is handled natively by
  `<audio>`, no decoder change needed.

## Verification

- `curl http://127.0.0.1:8880/health` → `{"status":"healthy"}`
- `curl -X POST http://127.0.0.1:8766/tts -d '{"text":"..."}'` returns
  `Content-Type: audio/mpeg`, clean ffprobe parse (no "Packet corrupt"
  warnings), 128kbps MP3, 24kHz mono.
- End-to-end `/turn` flow produces audible `bm_george` voice in the webview
  with no crackle or clipping.

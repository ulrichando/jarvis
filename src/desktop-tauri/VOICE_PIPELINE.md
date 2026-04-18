# JARVIS Desktop Voice Pipeline — Reference

Working setup as of 2026-04-18. Always-listening voice assistant with agent-backed task execution.

## Architecture

```
 ┌──────────────────┐   fetch /turn    ┌──────────────────┐
 │ Desktop Tauri UI │ ───────────────▶ │  Speech Sidecar  │
 │  (React+Silero)  │ ◀─────────────── │  (Bun :8766)     │
 └──────────────────┘   JSON+TTS URL   └────────┬─────────┘
         │                                      │ 1. STT  → Groq Whisper
         │ <audio>.src =                        │ 2. LLM  → CLI agent (subprocess)
         │   /tts/play/:id                      │ 3. TTS  → Groq Orpheus
         │ (streaming)                          │
         │                                      ▼
 ┌──────────────────┐   -p "prompt"    ┌──────────────────┐
 │ jarvis-cli       │ ◀─────────────── │ CLI start.sh     │
 │ (full agent loop)│                  │ (tools, MCP)     │
 └────────┬─────────┘                  └──────────────────┘
          │ /v1/messages
          ▼
 ┌──────────────────┐
 │ Proxy :4000      │ ──▶ Groq / DeepSeek / etc.
 └──────────────────┘
```

- **Proxy (4000)** — `src/cli/src/proxy/server.ts`. Anthropic-compat → OpenAI-compat. Fails loud if API key missing.
- **Bridge (8765)** — `src/cli/src/bridge/server.ts`. Legacy WS bridge for browser frontend. Desktop voice no longer depends on it.
- **Speech sidecar (8766)** — `src/desktop-tauri/server/speech.ts`. The one that matters for voice.
- **Desktop** — Tauri 2 + React, at `src/desktop-tauri/`.

## Voice turn flow

1. **Silero VAD** (in browser) detects speech start → UI goes *listening* (purple).
2. User stops talking → Silero emits `onSpeechEnd(Float32Array)` after ~160 ms of silence.
3. Hook encodes floats → 16 kHz mono WAV → POST `/turn`.
4. Sidecar:
   - **STT**: multipart to Groq `whisper-large-v3-turbo`, locked `language=en`, `temperature=0`.
   - Drop filler hallucinations (`"Thank you for watching"`, non-Latin chars, <3 words).
   - Reject echo (>55 % word overlap with last reply).
   - **Agent**: spawns `start.sh <provider> -p "<wrapped transcript>"` with stdin redirected from `/dev/null`. Returns first stdout line.
   - **Cache reply text** by UUID in memory.
5. Sidecar returns JSON `{ heard, reply, ttsId }`.
6. Hook points `<audio>.src` at `/tts/play/:ttsId`. Sidecar proxies Groq `canopylabs/orpheus-v1-english` WAV → streams to browser → plays progressively (~200 ms to first audio).
7. Barge-in: Silero keeps running during TTS. If speech detected, TTS is paused mid-word and a fresh recording starts.

## Key tuning values

Hook — `src/desktop-tauri/src/hooks/useSpeech.js`:

| Param | Value | Meaning |
|---|---|---|
| `positiveSpeechThreshold` | 0.5 | Silero confidence to flag speech START |
| `negativeSpeechThreshold` | 0.35 | confidence threshold to flag silence |
| `redemptionFrames` | 5 | frames (×32 ms) of silence before end-of-speech fires → ~160 ms lag |
| `minSpeechFrames` | 3 | minimum speech frames to treat as real utterance |
| `preSpeechPadFrames` | 10 | padding before speech start (captures onset) |
| `bargeInSilero` | true | cut TTS when user talks over JARVIS |

Sidecar env knobs:

| Env | Default | Purpose |
|---|---|---|
| `JARVIS_VOICE_AGENT` | `1` | `0` = plain LLM chat, `1` = full CLI agent with tools |
| `JARVIS_VOICE_PROVIDER` | `groq` | first positional to start.sh — picks provider default model |
| `JARVIS_CHAT_MODEL` | `deepseek-chat` | used only when voice-agent=0 |
| `JARVIS_STT_MODEL` | `whisper-large-v3-turbo` | Groq STT model |
| `JARVIS_STT_LANGUAGE` | `en` | lock STT to English — prevents language-auto-detect garbage |
| `JARVIS_TTS_MODEL` | `canopylabs/orpheus-v1-english` | Groq TTS |
| `JARVIS_TTS_VOICE` | `daniel` | Groq Orpheus voices: autumn, diana, hannah, austin, daniel, troy |
| `JARVIS_AGENT_TIMEOUT_MS` | `60000` | kill the CLI subprocess if it hangs |

## Reactor color → state map

Defined at the top of `animate()` in `src/desktop-tauri/src/components/ArcReactor.jsx`:

| Reactor state | Hex | Trigger |
|---|---|---|
| idle / ready | `glowInt` (theme cyan) | waiting |
| listening | `#a78bfa` purple | you talking (Silero `voiceActive` or recording) |
| thinking | `#fbbf24` amber | `processing` (agent running) |
| speaking | `#67e8f9` cyan | TTS audio playing |
| offline | `#f87171` red | `!navigator.onLine` OR bridge WS down |
| booting | `#0f172a` dim navy | startup |

Priority in App.jsx: `offline > speaking > voiceActive > (recording or processing) > idle`.

## Voice-mode agent preamble

Every transcript is wrapped in this header before the CLI agent sees it (sidecar `VOICE_PREAMBLE`):

```
You are JARVIS, responding by voice.
Rules:
- Answer ONLY what the user asked, in ONE short sentence (≤ 20 words).
- Plain spoken English. No markdown, no lists, no code blocks.
- Do NOT mention git status, modified files, recent commits, or the project
  structure unless the user explicitly asked about them.
- Do NOT summarise context you were given. Do NOT repeat the user back.
- If a tool is needed, call it silently and report only the result.

User said:
<transcript>
```

Without this, the CLI's default context injection (CLAUDE.md + `gitStatus`) makes the agent volunteer project summaries every turn.

## WebKit2GTK gotchas

1. **Transparent window** needs: xfwm4 compositing on, `transparent: true` in `tauri.conf.json`, no `WEBKIT_DISABLE_DMABUF_RENDERER=1` (breaks ARGB).
2. **Mic permission** — WebKit blocks `getUserMedia` by default. Rust fix in `main.rs`:
   ```rust
   settings.set_enable_media_stream(true);
   settings.set_enable_webrtc(true);
   settings.set_media_playback_requires_user_gesture(false);
   wv.connect_permission_request(|_wv, req| { req.allow(); true });
   ```
3. **Tauri asset protocol serves `.wasm` as `text/html`** — ONNX Runtime refuses. Workaround: sidecar serves VAD assets from `/vad/*` with correct Content-Type. Hook sets `baseAssetPath: base + '/vad/'`.
4. **CORS preflight** — GET+FormData works (simple request), POST+JSON requires preflight which WebKit sometimes drops. Prefer FormData for anything critical.
5. **Frontend `dist/` is embedded in the Rust binary at compile time.** Changing JS requires both `npm run build` AND `cargo build --release` (touch `src/main.rs` to force rebuild).
6. **Bun stdin to `-p` mode** — Bun's `stdin: 'ignore'` isn't enough; the CLI waits ~3 s for stdin. Wrap in `sh -c 'exec "$1" "$2" -p "$3" < /dev/null'`.

## Echo prevention

Three layers:
1. **Speaker-bleed threshold**: Silero `positiveSpeechThreshold` (0.5) is tuned so normal TTS leakage doesn't fire.
2. **Guard in hook**: `speakingRef.current` blocks duplicate `/turn` dispatches while TTS plays.
3. **Server-side dedup**: sidecar keeps last reply; drops incoming transcripts with >55 % word overlap.

## Latency budget (approx)

| Phase | Current | Was |
|---|---|---|
| Silero end-of-speech | ~160 ms | 450 ms (RMS) |
| Audio encode + upload | ~80 ms | 80 ms |
| Groq Whisper | ~250 ms | 250 ms |
| CLI agent (subprocess boot + run) | 1.5–3 s | 3–8 s (was DeepSeek, now Groq) |
| Groq Orpheus TTS generation | ~500 ms | — |
| TTS time-to-first-audio (streaming) | ~200 ms | 800 ms (was blob) |
| **End-to-end (short reply)** | **~2.5–3.5 s** | 6–9 s |

Next latency knob: **persistent agent in the bridge** (integration #3) — skips CLI boot per turn, cuts ~1–2 s.

## Start/stop cheat sheet

```bash
# Start full stack (proxy + bridge)
/home/ulrich/Documents/Projects/jarvis/src/cli/scripts/start-desktop.sh &

# Start speech sidecar only
/home/ulrich/Documents/Projects/jarvis/src/desktop-tauri/scripts/start-speech.sh &

# Relaunch desktop only (keeps bridge/proxy alive)
pkill -x jarvis-desktop
setsid /home/ulrich/Documents/Projects/jarvis/src/desktop-tauri/src-tauri/target/release/jarvis-desktop \
  </dev/null >/tmp/jarvis-desktop.log 2>&1 & disown

# Full rebuild (after JS + Rust changes)
cd /home/ulrich/Documents/Projects/jarvis/src/desktop-tauri
npm run build && cd src-tauri && touch src/main.rs && cargo build --release

# Tail everything
tail -f /tmp/jarvis-proxy.log /tmp/jarvis-bridge.log /tmp/jarvis-speech.log /tmp/jarvis-desktop.log
```

## Files I touched

```
src/cli/src/proxy/providers.ts   — fail-loud preflight on empty API key
src/cli/src/proxy/server.ts      — boot-time provider preflight
src/desktop-tauri/src-tauri/Cargo.toml          — added webkit2gtk dep
src/desktop-tauri/src-tauri/src/main.rs         — mic permission + autoplay
src/desktop-tauri/src/App.jsx                   — state priority, online detection
src/desktop-tauri/src/components/ArcReactor.jsx — state-color palette, voice-pulse
src/desktop-tauri/src/hooks/useSpeech.js        — Silero VAD + /turn flow
src/desktop-tauri/server/speech.ts              — STT+agent+TTS sidecar, VAD asset route
src/desktop-tauri/scripts/start-speech.sh       — sidecar launcher (loads .env.local)
src/desktop-tauri/public/                       — Silero ONNX + ORT wasm + worklet
```

Did not touch anywhere else in `src/cli/` — only proxy hardening was authorised.

# Pre-Realtime Snapshot — 2026-05-15

Captured **before** integrating OpenAI Realtime API (`gpt-realtime-mini`)
into the JARVIS voice agent. Single-page revert reference: if the
Realtime prototype regresses on quality, cost, or reliability, this
documents exactly what the text-based pipeline looked like at the
moment of the swap.

Git checkpoint: **`80f836fe`** (master tip immediately before the
Realtime work begins).

---

## Architecture today (text-LLM + separate STT/TTS)

```
┌─────────────────────────────────────────────────────────────────┐
│  user mic ───► PortAudio ──► PipeWire ──► voice-client          │
│                                                                  │
│                voice-client ──── LiveKit room ────► voice-agent │
│                                                                  │
│                       voice-agent's AgentSession:                │
│                       ┌──────────────────────────┐               │
│                       │ vad = Silero (prewarmed) │               │
│                       │ stt = Groq Whisper Turbo │               │
│                       │ llm = deepseek-v4-pro    │               │
│                       │       (tray-switchable)  │               │
│                       │ tts = Groq Orpheus + Edge│               │
│                       │       (FallbackAdapter)  │               │
│                       │ + tts_text_transforms[]  │               │
│                       └──────────┬───────────────┘               │
│                                  │                                │
│                                  ▼                                │
│                ◄──── audio reply on LiveKit room ──── voice-agent│
│                                                                  │
│                voice-client ──► PortAudio ──► PipeWire ──► spkr │
└─────────────────────────────────────────────────────────────────┘
```

## Active configuration (live values at snapshot time)

| Setting | File | Value |
|---|---|---|
| Speech LLM | `~/.jarvis/voice-model` | `deepseek-v4-pro` |
| CLI / tool model | `~/.jarvis/cli-model` | `gpt-5-mini` |
| TTS provider | `~/.jarvis/tts-provider` | `groq:troy` |
| LISTENING_RMS_THRESHOLD | `.env` | 25000 |
| Audio devices (env) | `.env` | `JARVIS_AUDIO_INPUT_DEVICE=pulse`, `JARVIS_AUDIO_OUTPUT_DEVICE=pulse` |
| PipeWire default sink | wpctl | Built-in Audio Analog Stereo |
| PipeWire default source | wpctl | Built-in Audio Analog Stereo |
| WirePlumber auto-profile | `/etc/wireplumber/wireplumber.conf.d/99-auto-profile.conf` | enabled (Analog Stereo Duplex profile) |

## AgentSession build site

`src/voice-agent/jarvis_agent.py:4526` — the canonical AgentSession
construction. Key knobs that the Realtime swap touches:

```python
session = AgentSession(
    max_tool_steps=15,
    vad=ctx.proc.userdata["vad"],          # → REMOVED in realtime (built-in)
    stt=_build_breakered_stt(),            # → REMOVED in realtime (built-in)
    llm=llm_arg,                           # → REPLACED by RealtimeModel(...)
    tts=tts_arg,                           # → REMOVED in realtime (built-in)
    turn_handling={...},                   # → KEPT (interruption + endpointing)
    tts_text_transforms=[                  # → REMOVED in realtime (no text→TTS step)
        stamp_first_token,
        strip_function_call_leakage,
        strip_voice_closers,
        strip_meta_silence,
        strip_archaic_openers,
        strip_preambles,
        normalize_numbers,
        cap_sir_count,
        "filter_markdown",
        "filter_emoji",
    ],
)
```

## What carries over to Realtime mode (unchanged)

- All FunctionTools (`transfer_to_*`, `delegate`, `bash`, `read`,
  `edit`, `write`, `screenshot`, `ask_user_question`, `monitor_start`,
  memory tools, etc.) — RealtimeModel exposes the same tool API.
- Subagent system — sub-agents continue to use their own text LLM +
  text-based pipeline unless explicitly migrated.
- Memory layer (`pipeline/memory_extractor.py` + consolidator) —
  runs on turn boundary, off-band from LLM internals.
- chat_ctx recall — passes through as initial conversation items.
- The supervisor instruction text (`prompts/supervisor.md`) — passes
  verbatim as `instructions` kwarg to RealtimeModel.
- Direct tools (bash/read/edit/write) — registered the same way.
- Subagent tool gate (`subagents/agent.py`) and other sanitizers
  continue to operate (sanitizers may be no-ops on the audio path).

## Pricing comparison

| | Current (text + Orpheus) | gpt-realtime-mini |
|---|---|---|
| LLM | DeepSeek text $0.27/M in, $1.10/M out | $10/M audio in ($0.30/M cached), $20/M audio out |
| TTS | Groq Orpheus — free on Groq tier | (bundled) |
| STT | Groq Whisper — free on Groq tier | (bundled) |
| ~Per hour active | **~$0.30/hr** | **~$3/hr (cached) / ~$8/hr full)** |

10–25× cost increase for the Realtime path; trade-off for native voice
quality, sub-second latency, and native interruption handling.

## Revert procedure (if Realtime prototype is rejected)

The Realtime prototype is now a tray switch (commit `9c979f75`), so
revert is one click:

1. **Tray click**: right-click → Models → Voice mode ▸ → "Use text LLM
   + Orpheus TTS (cheap)". The voice-client writes `~/.jarvis/voice-mode
   = text` and bounces `jarvis-voice-agent.service`. Within ~5 s the
   text pipeline is live again. No code changes needed.
2. **CLI equivalent** (if the tray isn't running): `echo text >
   ~/.jarvis/voice-mode && systemctl --user restart
   jarvis-voice-agent.service`.
3. **Env override** for debug (forces text mode regardless of file):
   `JARVIS_REALTIME_MODE=0` in `.env` or `local-api-token.env`.
4. **Full code revert** (if the prototype itself is buggy and needs
   to come out): `git revert 9c979f75` (safe, leaves a revert commit).
   Or hard-reset to the snapshot tip:
   ```bash
   git reset --hard 80f836fe       # destructive
   git push --force-with-lease     # only if you really mean it
   ```

`~/.jarvis/voice-mode` defaults to `text` if the file is missing or
contains an unrecognized value — fresh installs and typos both land
on the cheap path. Even with the realtime code merged, the text-mode
behavior is the no-action default.

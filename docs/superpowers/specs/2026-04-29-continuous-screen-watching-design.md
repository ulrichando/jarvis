# Continuous Screen Watching — Design

**Date:** 2026-04-29
**Status:** Approved (brainstorming complete, awaiting implementation plan)

## Goal

Let JARVIS see the user's screen continuously throughout a voice session — like Gemini Live or ChatGPT Advanced Voice with vision — without the current 60-second tool-invocation cap and without burning tokens when idle.

## Non-goals

- **Continuous narration.** JARVIS does not describe what it sees on its own. The existing `live_screen` tool keeps that capability for explicit "narrate the next N seconds" requests, but the new mode is silent.
- **Remote/cross-device streaming.** The agent runs on the same machine as the screen; we don't need WebRTC video transport. (LiveKit-video-track architecture is YAGNI for now.)
- **Replacing one-shot tools.** `screenshot()` and `live_screen` stay; this is additive.

## User experience

- Voice session opens → JARVIS's eyes are open. No additional command.
- User talks normally. When they ask something screen-related ("what am I looking at", "help me with this code", "why did this fail"), the model calls the new `recent_screen()` tool and answers based on what was just on screen.
- Voice session closes → buffer stops, frames are gone.
- A tray indicator shows when the buffer is active.
- A hotkey can pause the buffer mid-session (for entering passwords, etc.).

## Architecture

```
LiveKit room (audio only)
        │
        ▼
┌────────────────────────────────┐
│  jarvis_agent.py (Python)      │
│                                │
│  ┌──────────────────────────┐  │
│  │ ScreenBuffer (background)│  │  ← starts when session opens
│  │ • mss capture every 1.5s │  │  ← stops when session closes
│  │ • rolling deque, last 10 │  │  ← idle: $0, just RAM
│  │   frames (~15s context)  │  │
│  └────────────┬─────────────┘  │
│               │                │
│  ┌────────────▼─────────────┐  │
│  │ recent_screen() @tool    │  │  ← model decides when to call
│  │ • returns last N frames  │  │  ← only THEN do tokens get spent
│  │ • → JARVIS_VISION_BACKEND│  │
│  │   (gemini Flash / ollama)│  │
│  └──────────────────────────┘  │
└────────────────────────────────┘
```

Key principle: **capture is always running, frames never leave the box until the model calls `recent_screen()`.** Idle cost is RAM only; per-question cost is one vision-model call.

## Components

### 1. `ScreenBuffer` (new background task in `jarvis_computer_use.py`)

Responsibilities:
- Captures the primary monitor every 1.5 seconds using `mss` (faster than the current `scrot` shell-out).
- Downsamples each frame to ~1024px wide before storing.
- Maintains a `collections.deque(maxlen=10)` of `(timestamp, jpeg_bytes)` tuples.
- Provides `get_recent(n)` returning the last `n` frames.
- Provides `pause()` / `resume()` for the privacy hotkey.

Lifecycle:
- Starts when the LiveKit voice session enters the active state.
- Stops when the session closes — task cancelled, deque cleared.
- Auto-pauses on screen lock (detect via X11/Wayland session-lock signal).

Storage: in-memory only. Never persisted to disk.

### 2. `recent_screen()` function tool (new, in `jarvis_computer_use.py`)

```python
@function_tool
async def recent_screen(
    n_frames: int = 1,      # 1 = "what's on screen now", 2-3 = "what changed"
    focus: str = "",        # optional hint passed to the vision model
) -> str:
    """Look at what's recently been on the user's screen.

    The agent continuously buffers screenshots in the background.
    This tool returns a description of the most recent frame(s).
    Use when the user asks about their screen, references 'this',
    asks for help with something visible, or you need context for
    what they're doing.
    """
```

- Reads frames from `ScreenBuffer.get_recent(n_frames)`.
- Sends them to the vision backend chosen by `JARVIS_VISION_BACKEND` (existing env var: `auto` / `ollama` / `gemini`).
- Returns the description string.
- If the buffer is empty (e.g., paused, or just started) returns a clear message indicating that.

### 3. System prompt update

The voice-agent prompt needs a short paragraph telling the model:
- "You can see the user's screen via the `recent_screen()` tool."
- "Call it whenever the user references something visible ('this', 'my screen', 'this code'), asks for help with something they're doing, or when their question would be ambiguous without screen context."
- "Don't call it for unrelated questions ('what time is it')."

### 4. Tray integration

- Existing tray "Start Screen Sharing" menu item is **rewired** to toggle the buffer instead of launching the narration flow.
- Tray icon shows a "👁️" overlay (or color change) when the buffer is active.
- Right-click menu adds an explicit "Pause watching" / "Resume watching" item.

### 5. Privacy hotkey

- Global hotkey `Super+Shift+P` calls `ScreenBuffer.pause()` / `resume()`.
- Visual feedback: tray icon changes to a struck-through eye while paused.
- Bound via the existing tray-watcher process (file-driven IPC, same pattern as Camera-Source submenu).

## Vision provider policy

Reuse the existing `JARVIS_VISION_BACKEND` env var without changes:
- `auto` (default): try Gemini Flash, fall back to Ollama qwen2.5vl on quota / network errors.
- `gemini`: Gemini Flash only.
- `ollama`: local qwen2.5vl only (offline-capable).

`recent_screen()` calls `_gemini_describe()` or the Ollama equivalent that already exists. No new vision-backend code needed.

## What changes in existing code

| File | Change |
|------|--------|
| `src/voice-agent/jarvis_computer_use.py` | Add `ScreenBuffer` class. Add `recent_screen()` `@function_tool`. |
| `src/voice-agent/jarvis_agent.py` | Start `ScreenBuffer` on session connect, stop on disconnect. Register `recent_screen` in the tool list. Update system prompt. |
| Tray watcher (existing) | Rewire "Start Screen Sharing" to toggle buffer. Add eye-overlay icon state. Bind `Super+Shift+P`. |
| `live_screen` tool | **Kept as-is.** Repositioned in the system prompt for explicit "narrate for N seconds" use cases. |

No changes to LiveKit transport (audio-only is fine).

## Cost model

| Scenario | Old (`live_screen` polling) | New design |
|----------|------------------------------|------------|
| Voice session, idle on screen | ~$1/hour (poll every 2s) | $0 (buffer is local) |
| Voice session, 1 question/min about screen | same | ~60 vision calls/hour ≈ $0.05/hour |
| 60s+ continuous watching | impossible (capped) | unlimited |

## Risks & open considerations

- **mss permissions on Wayland.** `mss` works on X11 reliably; Wayland needs PipeWire or `xdg-desktop-portal`. Need to verify on user's Arch setup. Fallback path: keep `scrot` as a compatibility option.
- **Model over-calling the tool.** If the system prompt is loose, the model may call `recent_screen()` on every turn, defeating the cost benefit. Mitigation: clear, restrictive prompt language + log tool-call frequency for tuning.
- **Buffer memory.** 10 frames × ~200KB JPEG = ~2MB. Negligible.
- **Privacy.** User must always know when buffer is on. Tray indicator + pause hotkey are non-negotiable.
- **Frame staleness.** With a 1.5s interval, the "recent" frame the model sees can be up to 1.5s old. Acceptable for conversational use.

## Out of scope (explicit)

- LiveKit video tracks for cross-device screen streaming.
- Multi-monitor capture (only primary monitor v1).
- Recording / saving frames to disk.
- Replacing the existing `screenshot()` or `live_screen` tools.
- Web client (browser) screen sharing — Tauri/desktop only v1.

# Desktop Computer Use — Design Spec

**Date:** 2026-04-28
**Status:** Approved
**Goal:** Give desktop Jarvis the ability to see the screen and control mouse/keyboard — AI agent operates the computer like a human would.

## Architecture

Three components, one orchestration layer:

```
Gemini 3.1 Flash Live           Voice Agent (Groq/DeepSeek)       Hardware Layer
┌─────────────────────┐         ┌──────────────────────────┐      ┌──────────────────┐
│ Screen share via     │──UI──▶  │ Decides next action       │      │ xdotool:          │
│ WebRTC               │  desc   │ based on UI description   │──▶   │  mousemove, click │
│ Continuous visual    │         │ Calls tools: click, type, │      │  type, key, scroll│
│ stream               │         │ scroll, wait, screenshot  │      │                  │
│                      │         │                          │◀──│  ydotool (Wayland  │
│ Model: gemini-3.1-   │         │ Existing tool framework + │ cmd │  fallback)        │
│   flash-live         │         │ new computer-use tools    │ out │                  │
└─────────────────────┘         └──────────────────────────┘      └──────────────────┘
```

**Gemini is "eyes." Groq/DeepSeek is "brain." xdotool is "hands."**

The voice agent (jarvis_agent.py) remains the orchestrator. Gemini Live provides continuous screen description; the agent feeds those descriptions to the text LLM, which decides actions and calls execution tools.

## Tools

Added to the existing tool set in jarvis_agent.py:

| Tool | Parameters | What it does |
|---|---|---|
| `computer_use` | `task`: string | Starts a session. Connects to Gemini Live screencast. Returns first UI description. |
| `computer_stop` | none | Ends the session. Stops the Live stream. Returns summary. |
| `click` | `x`, `y`, `button` (default "left"), `count` (default 1) | Moves cursor and clicks. Returns cursor position + updated screen desc. |
| `type_text` | `text`, `enter` (default false) | Types at current cursor. Returns typed text + updated screen desc. |
| `scroll` | `amount` (positive = down) | Scrolls at cursor position. |
| `drag` | `start_x`, `start_y`, `end_x`, `end_y` | Click-drag from point A to B. |
| `key_press` | `keys` (e.g. "ctrl+t", "alt+f4") | Presses key combination. |
| `wait` | `ms` (default 500) | Pauses for UI to settle. Returns updated screen desc. |
| `screenshot` | none | One-shot screenshot → Gemini describe. For cases where Live stream resolution isn't enough. |

Only one `computer_use` session can run at a time. Scoped to a single user request.

## Agent Loop

```
User asks something that needs computer control
  │
  ▼
computer_use(task) → Gemini Live stream starts → first UI desc returns
  │
  ▼
Loop:
  Groq/DeepSeek receives: task + current UI desc + tool results
  → Decides next action (click, type, scroll, wait, etc.)
  → Agent executes tool (xdotool)
  → Waits for UI reaction (default 500ms)
  → Gemini Live returns updated screen description
  → Repeat
  │
  ▼
Groq/DeepSeek calls computer_stop → stream ends → agent reports result to user
```

**Safety guards:**
- 3 consecutive identical failures → stop and explain
- User says "stop" or "cancel" → immediate stop
- 30 seconds with no visible UI change → stop and explain what's stuck
- Each tool returns `{ success, error, cursor_at, screenshot_desc }` so the LLM knows if it worked

## Future: Hardware Control

These are out of scope for the initial implementation but part of the architecture:

- **Camera control** — `/dev/video0` via v4l2 for brightness/exposure/focus, plus raw frame access for `face_recognition` library
- **Facial recognition** — opencv-python + face_recognition, reads from webcam frames, exposes as a tool (`face_identify`, `face_register`)
- **Audio devices** — PulseAudio/PipeWire for mic/speaker routing

## Integration Points

- **jarvis_agent.py** — New tools added to the tool registry, Gemini Live client class added
- **jarvis_voice_client.py** — No changes (existing mic/speaker ownership is fine)
- **Desktop Tauri app** — No changes (computer-use is voice-agent-side, user sees results via existing chat panel or voice)
- **Dependencies** — `google-genai` (Gemini SDK), `xdotool` (already on system), `ydotool` (optional Wayland fallback)

## Out of Scope

- Proactive agent (agent initiates actions without user request)
- Multi-app orchestration beyond what the LLM can reason through
- Windows/macOS support (Linux + Hyprland first)
- Browser-specific agent (Playwright/Puppeteer — separate concern)

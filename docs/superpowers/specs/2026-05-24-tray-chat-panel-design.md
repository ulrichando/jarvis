# Tray chat panel — text-to-voice-agent surface

**Date:** 2026-05-24
**Status:** spec, pre-implementation
**Author:** Ulrich + Claude
**Scope:** `src/voice-agent/desktop-tauri/src-tauri/src/main.rs`, `src/voice-agent/desktop-tauri/src/App.jsx`, `src/voice-agent/desktop-tauri/src/components/VoiceChatPanel.jsx` (new), `src/voice-agent/voice_client_http_api.py`, `src/voice-agent/jarvis_agent.py`, new pytest coverage in `src/voice-agent/tests/`.

## TL;DR

Add a discoverable chat surface accessible from the desktop tray icon so the user can type to the voice agent — same supervisor LLM, same Orpheus voice, same conversation context — and hear the reply spoken aloud while seeing it as text in the panel. Use case: STT mishears, ambient noise, or a quiet environment where speaking is awkward.

The panel is its own small React component (`VoiceChatPanel.jsx`), wired **directly** to the voice client at `127.0.0.1:8767`. The bridge at `127.0.0.1:8765` is **not involved**: that bridge is the CLI agent's surface and remains untouched. The existing `ChatPanel.jsx` (the CLI-agent-flavored rich panel reachable today via Ctrl+H) is **not modified**.

Three small pieces of glue close the round-trip:

1. **Send:** panel `POST /user-input` → existing voice-client handler → existing `{type:"user_input", text}` data-channel publish → voice agent's existing `data_received` handler calls `session.generate_reply(user_input=text)`.
2. **Reply (audio):** the existing TTS pipeline voices the reply via Orpheus — no change.
3. **Reply (text):** voice agent's existing `conversation_item_added` handler gets ~6 lines of additive code to publish `{type:"assistant_says", text, ts_ms}` on the LiveKit data channel; voice client subscribes to those packets and re-emits them as Server-Sent Events on a new `/events` route; panel uses `EventSource` to render them.

Mic auto-mutes while the panel input is focused, restoring its prior state on blur/close, so typing doesn't race with ambient mic capture.

## Why now

The user has been frustrated by repeated STT mishears in noisy contexts and by ambient leak in the AEC chain (see [project_aec_echo_stt_regression.md] and [project_stt_recognition_not_echo.md] in MEMORY). Voice-only input means a single mishear costs a full back-and-forth to correct. A text input that hits the same voice-agent brain — and gets a voice reply — provides a fallback that doesn't fork JARVIS's conversation state between two LLMs (CLI vs. voice supervisor).

The existing `ChatPanel.jsx` is reachable via Ctrl+H, but it routes through the bridge to the CLI agent. That's a different brain, a different conversation history, no shared memory with the voice supervisor. So even though the panel exists, "text → voice agent" doesn't, and the user can't get there from the tray.

## Why this shape (and what was rejected)

| Option | Pros | Cons | Decision |
|---|---|---|---|
| **Reuse `ChatPanel.jsx`, add a `target` prop** | One panel, less code | Risk of breaking existing CLI-agent UX; the streaming/tool-call UI is dead code for voice flow | Rejected — risk-isolation matters here |
| **New `VoiceChatPanel.jsx`, voice-only** | Existing CLI panel untouched; surface tailored to voice-agent's actual events | One more component | **Chosen** |
| **Tray-inline single-line input** | Smallest UI footprint | Tauri tray menus don't natively support inline text inputs; would need a tiny custom window; no reply visibility | Rejected |
| **Route through the bridge** | Single WS connection from the panel | Mixes CLI-agent and voice-agent traffic on a path the user explicitly said to leave alone | Rejected |
| **WS endpoint on the voice client for events** | Bidirectional | Send path is HTTP POST already; receive is server→client only — WS is overkill | Rejected in favor of SSE |
| **Audio-only reply (no panel text)** | Zero voice-agent changes | Panel feels half-empty; no scroll-back of what JARVIS said | Rejected — the user asked for text bubble |
| **Tool-call / streaming UI parity with the bridge panel** | Visual richness | Voice agent doesn't surface those events; wiring them up is a separate, large project | Rejected — out of scope |

## What gets touched (and what doesn't)

### Tauri / desktop

`src/voice-agent/desktop-tauri/src-tauri/src/main.rs` — restore the "Open Chat Panel" menu item that was removed 2026-05-10. The `open_chat` click handler at line ~1700 is still wired and emits `tray-toggle-chat`, but we want this new entry to emit a **new** event `tray-toggle-voice-chat` so the bridge-flavored `ChatPanel.jsx` (which listens for `tray-toggle-chat`) is left alone. One new `MenuItemBuilder::with_id("open_voice_chat", "Open Chat Panel")` line, inserted near the other tray entries, plus a small match-arm beside `open_chat` that emits `tray-toggle-voice-chat`. Total: ~10 lines of Rust.

`src/voice-agent/desktop-tauri/src/App.jsx` — add a `voiceChatOpen` state and a `<VoiceChatPanel>` mount, with listeners for `tray-toggle-voice-chat` / `tray-open-voice-chat` / `tray-close-voice-chat`. The existing `chatOpen` state and `<ChatPanel>` mount are not changed. Hotkey routing:
- Tray menu item → opens `VoiceChatPanel` (new).
- Ctrl+H → opens existing `ChatPanel` (unchanged).
- Ctrl+Shift+Space — left pointing at the existing panel for now (no change). The user can rebind later if they want; tray-menu discoverability is the main goal of this work.

`src/voice-agent/desktop-tauri/src/components/VoiceChatPanel.jsx` — NEW. Minimal footprint:
- Floating overlay, draggable header, resize handle (mirrors `ChatPanel.jsx`'s patterns so it feels native — share the same drag/resize helpers via copy-paste, not extraction, since the existing patterns are working and risk-frozen).
- Message list with two bubble shapes: `user` (right-aligned, accent-colored) and `jarvis` (left-aligned, neutral).
- One-line text input + send button.
- Mic auto-mute toggle (default ON) shown as an icon in the header — when ON, mic is muted while the input is focused.
- No streaming tokens, no tool-call rendering, no usage/context bar, no history sidebar (those don't apply to the voice-agent flow today).
- Optimistic local append of the user's message on send. SSE arrival of `assistant_says` appends the JARVIS bubble.
- Send pathway: `fetch('http://127.0.0.1:8767/user-input', {method:'POST', body:JSON.stringify({text})})`. No bridge token (the voice client doesn't require one for local-loopback POSTs as of this writing; see "Auth posture" below).
- Receive pathway: `new EventSource('http://127.0.0.1:8767/events')` opened on mount, closed on unmount. Filters by `data.type === "assistant_says"`.

### Voice agent

`src/voice-agent/jarvis_agent.py` — the existing `@session.on("conversation_item_added")` handler at line 4974 already extracts `role` and `text` from the new item and runs the barge-in-truncation logic. **Right after the barge-in truncation block**, add a small additive block that, if `role == "assistant"` and the resulting `text` is non-empty, publishes the data packet:

```python
# Mirror the spoken reply to subscribers (the tray chat panel is the
# first known consumer). Idempotent — only publish once per item.
if role == "assistant" and text and not getattr(item, "_jarvis_published_says", False):
    try:
        import json as _json_pub
        payload = _json_pub.dumps({
            "type": "assistant_says",
            "text": text,
            "ts_ms": int(time.monotonic() * 1000),
        }).encode("utf-8")
        try:
            item._jarvis_published_says = True
        except Exception:
            pass
        asyncio.create_task(
            ctx.room.local_participant.publish_data(payload, reliable=True)
        )
    except Exception as _e:
        logger.debug(f"[chat-panel] assistant_says publish failed: {_e!r}")
```

`asyncio` and `time` are module-top imports (`jarvis_agent.py:35` and `:41`). `json` is *not* imported at module top in this file — the convention is `import json as _json[_suffix]` inline (e.g. `jarvis_agent.py:2004`, `:2077`, `:2549`, `:5511`); the snippet follows that idiom with `_json_pub` to avoid shadowing any other local `_json` in the same function. The `ctx` reference is closed over from the enclosing `entrypoint(ctx: JobContext)` function — same closure pattern the existing `_on_data` handler relies on.

This fires for **every** assistant turn, regardless of whether the user spoke or typed — the SSE side has no subscriber during pure voice mode, so it's a no-op. The marker prevents double-publish if the handler re-fires for the same item.

### Voice client

The SSE wiring spans two files because the HTTP server and the LiveKit room loop live in different modules. The seam between them is one method on `VoiceClientHttpApi`:

```python
def enqueue_event(self, event: dict) -> None:
    """Broadcast a JSON event to all live SSE subscribers.

    Called from jarvis_voice_client.py's data_received hook. Safe to
    call from inside the LiveKit asyncio loop — uses put_nowait and
    drops oldest on QueueFull so a stuck panel doesn't pin memory.
    """
    for q in list(self._sse_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(event)
            except asyncio.QueueEmpty:
                pass
```

`src/voice-agent/voice_client_http_api.py` gains:

1. **Subscriber state on the instance**: `self._sse_subscribers: set[asyncio.Queue] = set()` initialized in `__init__`. Process-wide singleton via the existing `VoiceClientHttpApi` singleton constructed at startup in `jarvis_voice_client.py:1135`.

2. **A new GET route `/events`**: returns an SSE stream. Each connection adds an `asyncio.Queue(maxsize=64)` to `self._sse_subscribers`; on disconnect (client closes, `asyncio.CancelledError`, or `ConnectionResetError`), the `finally` removes it. Each subscriber receives `data: {json}\n\n` lines.

3. **The `enqueue_event` method** (above) exposed for external callers.

`src/voice-agent/jarvis_voice_client.py` gains:

4. **A new `data_received` handler** registered alongside the existing `room.on("track_subscribed")` / `room.on("participant_connected")` decorators at lines 607-660. It does NOT currently have one (only the voice agent does — at `jarvis_agent.py:5564`). Skeleton:

```python
@room.on("data_received")
def _on_data_received(packet) -> None:
    try:
        msg = json.loads(packet.data.decode("utf-8"))
    except Exception:
        return
    if not isinstance(msg, dict):
        return
    if msg.get("type") == "assistant_says":
        text = (msg.get("text") or "").strip()
        if text:
            http_api.enqueue_event({
                "type": "assistant_says",
                "text": text,
                "ts_ms": msg.get("ts_ms"),
            })
```

The handler filters only for `assistant_says` packets; other types (`user_input`, `speak`, `stop` — which the voice client itself publishes outward) won't appear here anyway because LiveKit's `data_received` only fires for packets from REMOTE participants (not self-published loopback). The filter is defensive belt-and-suspenders.

Skeleton of the SSE route:

```python
async def events(self, req: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await resp.prepare(req)
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    self._sse_subscribers.add(queue)
    try:
        while True:
            event = await queue.get()
            line = f"data: {json.dumps(event)}\n\n".encode("utf-8")
            await resp.write(line)
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        self._sse_subscribers.discard(queue)
    return resp
```

The data-channel hook lives in `jarvis_voice_client.py`'s `_on_data_received` (or equivalent), guarded by a try/except so a malformed packet doesn't kill the loop. It enqueues into every subscriber and drops the oldest entry if a queue is full (back-pressure: a stuck panel doesn't pin memory; it loses the oldest events).

### Mic auto-mute

Inside `VoiceChatPanel.jsx`:

- Receive `voiceMuted` + `setVoiceMuted` as props from App.jsx (which already owns this state at `App.jsx:81`). Don't POST `/mute` directly — go through App's existing setter so `useVoiceClient({muted: voiceMuted})` propagates the change to the voice client through the existing single source of truth.
- On input `onFocus`: remember the current `voiceMuted` in a ref, call `setVoiceMuted(true)` if it wasn't already true.
- On input `onBlur` or panel close: `setVoiceMuted(savedPriorValue)` to restore.
- The header gets a small lock icon showing the auto-mute is active; the user can disable auto-mute via a toggle for keyboard-tap-quiet environments.

The existing `useVoiceClient` hook (at `useVoiceClient.js:166`) POSTs `{mute: voiceMuted}` to `/mute` whenever the prop changes — matching the `voice_client_http_api.mute` body shape (`body.get("mute", ...)`). So setting App's state is sufficient; no extra fetch from VoiceChatPanel.

### What is NOT touched

- The bridge at `127.0.0.1:8765` (`src/cli/src/bridge/`).
- The existing `ChatPanel.jsx` and its bridge WS wiring.
- The tray indicator — colors, ring, icon, poll rate, states are FROZEN per [.claude/rules/desktop-tauri.md]. Only the menu list gets one additional item; nothing about the visual indicator changes.
- Voice mode end-to-end behavior — the new `assistant_says` publish fires whether the user spoke or typed, but the SSE consumer is the only listener.
- `/speak` (TTS-only), `/stop`, `/screen-share`, etc. — left alone.
- The CLI agent and its surface.

## Data flow (annotated)

```
User types "remind me to buy milk" + Enter
   ↓
VoiceChatPanel: append user bubble locally
   ↓
fetch POST :8767/user-input {text}
   ↓
voice_client_http_api.user_input:
   room.local_participant.publish_data({"type":"user_input","text":...})
   ↓
LiveKit room (data channel)
   ↓
jarvis_agent.entrypoint._on_data:
   {"type":"user_input"} → asyncio.create_task(_user_input_when_ready(text))
   ↓
_user_input_when_ready: session.generate_reply(user_input=text)
   ↓
AgentSession LLM (Claude Haiku/Sonnet) processes
   ↓
TTS pipeline (Groq Orpheus) → audio output → user hears it
   ↓
session emits conversation_item_added (role="assistant", content=text)
   ↓
existing _on_item handler: runs barge-in truncation, memory sync, etc.
   ↓
NEW additive block: if role=="assistant" and text and not published:
   publish_data({"type":"assistant_says","text":...,"ts_ms":...})
   ↓
LiveKit data channel → voice client _on_data_received
   ↓
filter type=="assistant_says" → enqueue on every SSE subscriber
   ↓
GET /events SSE: writes "data: {...}\n\n"
   ↓
EventSource in VoiceChatPanel.jsx → appends Jarvis bubble
```

## Auth posture

The voice client's HTTP API (`voice_client_http_api.py`) accepts unauthenticated local-loopback POSTs today — see `/mute`, `/speak`, `/stop`, `/user-input`, all of which take only a JSON body. The new `/events` SSE matches this posture: no token required, bind to `127.0.0.1` only (this is the existing default in `voice_client_http_api.py:STATUS_PORT`).

The Tauri panel runs in the same user session as the voice client, so the loopback assumption holds. If we ever expose the voice client off-localhost, all of these routes need a bearer-token gate together — the bridge's auth pattern (`JARVIS_REQUIRE_LOCAL_AUTH=1` + `~/.jarvis/local-api-token.env`, see CLAUDE.md) is the model to follow. That's deferred and tracked as a future hardening item, not blocking this work.

## Error handling

**Voice client not running** (the service is down or restarting):
- `fetch /user-input` returns network error → panel shows an inline status line "Voice agent is offline (will retry)" and keeps the user's typed bubble visible. No retry queue — the user can press Enter again.
- `EventSource /events` enters its native `onerror` and retries with built-in backoff. Connection-status dot in the panel header turns amber until reconnect.

**Voice client connected to LiveKit but agent not joined** (the existing 503 from `/user-input` when `room is None or not self.state.connected`):
- Panel shows "Voice agent isn't connected to a session yet" and disables send for 2 s, then re-enables. The user can wait or retry.

**`session.generate_reply` not ready** (rare; observed when the agent is still booting):
- Voice agent's existing `_user_input_when_ready` polls for up to 3 s before logging "session.generate_reply unavailable after 3s — dropping" and silently giving up. The panel has no signal of this today.
- Mitigation: extend `_user_input_when_ready` so that if it ultimately gives up, it publishes a synthetic `{"type":"assistant_says","text":"(Couldn't process that — agent wasn't ready. Try again.)","ts_ms":...}` data packet so the user sees a panel bubble and isn't stuck waiting.

**Malformed SSE message arrives** (defensive):
- Panel's `EventSource.onmessage` wraps the JSON.parse in try/catch and ignores parse failures.

**SSE subscriber queue full**:
- Voice client drops the oldest event from the queue, enqueues the new one. Stuck panel ≠ stuck producer.

**Voice agent publishes `assistant_says` while no SSE clients exist**:
- Voice client receives the data packet and enqueues on zero subscribers — the iteration over `self._sse_subscribers` is a no-op. Zero overhead in the steady-state voice-only mode.

## Testing

### Unit / pytest (`src/voice-agent/tests/`)

`test_voice_client_events_sse.py` (new):
- Boots `JarvisVoiceClient` HTTP API in a test harness with a stub LiveKit room.
- Subscribes a test SSE client to `/events`.
- Programmatically enqueues an `assistant_says` event onto the subscriber's queue.
- Asserts the SSE client receives a well-formed `data: {...}\n\n` frame.
- Asserts subscriber set adds/removes on connect/disconnect.
- Asserts queue back-pressure: when full, oldest is dropped.

`test_assistant_says_publish.py` (new):
- Constructs a fake `conversation_item_added` event with a Mock `item` whose `role="assistant"` and `content` returns "hello world".
- Asserts `publish_data` is called once with the right shape.
- Asserts a second call to the handler on the SAME item does NOT re-publish (idempotency marker).
- Asserts items with `role="user"` or empty text do NOT publish.

`test_user_input_when_ready_fallback.py` (extends existing or new):
- When `_user_input_when_ready` exhausts its 3 s wait, asserts a synthetic `assistant_says` "agent wasn't ready" packet is published.

### Manual smoke

1. Start voice agent + Tauri.
2. Right-click tray icon → "Open Chat Panel" → panel opens.
3. Type "what time is it" + Enter → JARVIS speaks the time + the reply appears as a text bubble.
4. Focus the input → check tray indicator goes muted; blur → restored.
5. Type while voice mode is in flight (JARVIS speaking) → barge-in fires as today, then JARVIS responds to the typed turn.
6. Close panel mid-reply → audio continues, panel reopens with the latest message still in the list (state is preserved in component memory; not persisted on close yet — see "Out of scope").
7. Kill the voice agent service → panel shows "offline (will retry)" within 2 s; restart → SSE auto-reconnects, sends resume.

### Verification gate (Stop hook)

Per [.claude/rules/regression-prevention.md]:
- Voice-agent edits → `cd src/voice-agent && .venv/bin/python -m pytest tests/` must pass.
- Desktop-tauri edits → `cd src/voice-agent/desktop-tauri && npm run build` must pass, then `cargo build --release` to re-embed the new `dist/`.
- Live behavior verification: restart the voice agent (after checking `turn_telemetry.db` for in-flight session per CLAUDE.md), exercise the panel manually with the steps above.

## Out of scope (explicit)

- Persisting the panel's message list across sessions. Today it lives in component state; closing the panel loses the visible history. Adding persistence (mirror to a SQLite table, or scroll back through the existing turn_telemetry) is a separate spec.
- Reaching the voice agent over non-localhost (LAN, remote). The auth posture for that is documented above but not implemented.
- Sharing the panel between the voice-agent flow and the CLI-agent flow. They stay separate components.
- Voice-mode parity in the panel: showing tool calls, streaming tokens, todo blocks. Voice agent doesn't emit those events to a consumer surface today.
- Replacing Ctrl+Shift+Space's target panel. The hotkey continues to open the existing rich `ChatPanel`. The user can repoint it later if they want.

## Implementation order

> **Two systemd units to restart**: the voice agent and the voice client are separate processes managed by `jarvis-voice-agent.service` (runs `jarvis_agent.py`) and `jarvis-voice-client.service` (runs `jarvis_voice_client.py`). Step 1 below changes the agent; steps 2–3 change the client; both need a restart before the SSE path is live.

1. **Voice agent** — extend `conversation_item_added` to publish `assistant_says` data packets with idempotency marker. Add pytest. **No restart yet** (additive — publish_data fires whether or not anyone is listening; no behavior change in voice mode).

2. **Voice client (HTTP API)** — add `_sse_subscribers` set to `VoiceClientHttpApi.__init__`, add `GET /events` route, add `enqueue_event(event)` method. Add pytest.

3. **Voice client (room loop)** — register `room.on("data_received")` in `jarvis_voice_client.py` alongside the existing `room.on` decorators, filter for `assistant_says`, call `http_api.enqueue_event(...)`. Add pytest.

4. **Restart both services**: first check `~/.local/share/jarvis/turn_telemetry.db` for `ts_utc` in the last 60 s (per CLAUDE.md operational rule — ask user if so). Then:
   ```
   systemctl --user restart jarvis-voice-agent.service
   systemctl --user restart jarvis-voice-client.service
   ```
   Verify both came up healthy: `curl -s http://127.0.0.1:8767/status | jq .connected`.

5. **Tauri — new component**. Add `VoiceChatPanel.jsx`, wire to App.jsx state (new `voiceChatOpen` state + props for `voiceMuted` / `setVoiceMuted`), add `tray-toggle-voice-chat` / `tray-open-voice-chat` / `tray-close-voice-chat` listeners. `npm run build` (vite, ~7 s) to confirm.

6. **Tauri — restore tray entry**. Add `MenuItemBuilder::with_id("open_voice_chat", "Open Chat Panel")` in `main.rs`, insert into MenuBuilder chain, add match arm in tray click handler emitting `tray-toggle-voice-chat`. `cargo build --release` (re-embeds new `dist/` per the desktop-tauri two-step rule).

7. **Mic auto-mute** wiring in `VoiceChatPanel.jsx` via App's existing `voiceMuted` state. Manual smoke.

8. **Synthetic "agent wasn't ready" fallback** in `_user_input_when_ready` so the panel doesn't hang silently when the agent is mid-boot. Pytest. Restart `jarvis-voice-agent.service`.

Each step is independently testable and additive — the previous behavior is unchanged if any later step is skipped. Steps 1–3 can be developed in parallel (different files) but must restart together.

## Files changing (summary)

| File | Status | Approx LOC |
|---|---|---|
| `src/voice-agent/jarvis_agent.py` | edit | +10 (additive block in existing handler) |
| `src/voice-agent/voice_client_http_api.py` | edit | +60 (SSE route + subscriber set) |
| `src/voice-agent/jarvis_voice_client.py` | edit | +20 (data-channel hook → subscriber enqueue) |
| `src/voice-agent/tests/test_voice_client_events_sse.py` | new | ~100 |
| `src/voice-agent/tests/test_assistant_says_publish.py` | new | ~80 |
| `src/voice-agent/desktop-tauri/src-tauri/src/main.rs` | edit | +12 (menu item + match arm) |
| `src/voice-agent/desktop-tauri/src/App.jsx` | edit | +25 (state, mount, listeners) |
| `src/voice-agent/desktop-tauri/src/components/VoiceChatPanel.jsx` | new | ~200 |
| `docs/superpowers/specs/2026-05-24-tray-chat-panel-design.md` | new | this file |

Total new code: ~520 LOC including tests. Net deletions: zero — everything is additive.

## References

- CLAUDE.md operational rules — restart caution, no Co-Authored-By trailers, tray indicator FROZEN.
- [.claude/rules/regression-prevention.md] — SCOPE/OUT/WHY declaration, verification gate.
- [.claude/rules/desktop-tauri.md] — tray indicator frozen, `npm run build` + `cargo build --release` two-step.
- `src/voice-agent/voice_client_http_api.py:246` — existing `/user-input` handler this design hangs off of.
- `src/voice-agent/jarvis_agent.py:4974` — existing `conversation_item_added` handler the publish hook is added to.
- `src/voice-agent/jarvis_agent.py:5564` — existing `_on_data` dispatch this design reuses.
- `src/voice-agent/desktop-tauri/src-tauri/src/main.rs:1495` — comment from the 2026-05-10 menu trim explaining how to restore an "Open Chat Panel" entry.
- `src/voice-agent/desktop-tauri/src/components/ChatPanel.jsx` — the existing bridge-backed rich panel; not modified, used as a structural reference for drag/resize patterns.

# Tray Chat Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a discoverable text-input chat surface accessible from the desktop tray icon so the user can type to the voice agent (same supervisor LLM, same Orpheus voice, same conversation context) and hear the reply spoken aloud while seeing it as a text bubble in the panel.

**Architecture:** New React component `VoiceChatPanel.jsx` wired **directly** to the voice client at `127.0.0.1:8767` — bypassing the bridge entirely. Send is `POST /user-input` (existing); receive is a new SSE route `/events` fed by a new `data_received` handler in the voice client that subscribes to `assistant_says` data packets the voice agent now publishes on every assistant turn.

**Tech Stack:** Python 3.13 (voice-agent) with aiohttp + livekit-agents; React 18 (Tauri webview, vite); Rust (Tauri 2 tray menu); SSE for one-way agent→panel events; HTTP POST for panel→agent. No new dependencies.

---

## File map

**Modify (5 files):**
- `src/voice-agent/jarvis_agent.py` — extend `conversation_item_added` handler at line ~4974 (add ~15 lines: idempotent publish_data of `assistant_says`)
- `src/voice-agent/voice_client_http_api.py` — add `_sse_subscribers` set + `GET /events` route + `enqueue_event(event)` method (~60 lines)
- `src/voice-agent/jarvis_voice_client.py` — register `room.on("data_received")` handler near the existing `room.on(...)` decorators at lines 607-660 (~20 lines)
- `src/voice-agent/desktop-tauri/src/App.jsx` — add `voiceChatOpen` state, `<VoiceChatPanel>` mount, event listeners (~25 lines)
- `src/voice-agent/desktop-tauri/src-tauri/src/main.rs` — restore tray menu entry + match arm emitting `tray-toggle-voice-chat` (~12 lines)

**Create (3 files):**
- `src/voice-agent/desktop-tauri/src/components/VoiceChatPanel.jsx` — minimal floating panel (~220 lines)
- `src/voice-agent/tests/test_assistant_says_publish.py` — pytest for the publish hook (~110 lines)
- `src/voice-agent/tests/test_voice_client_events_sse.py` — pytest for SSE route + subscriber set + enqueue_event (~140 lines)

---

## Operational guardrails (read before starting any task)

- **Two systemd services.** `jarvis-voice-agent.service` runs `jarvis_agent.py`; `jarvis-voice-client.service` runs `jarvis_voice_client.py`. Restarting one does NOT restart the other.
- **Restart caution (CLAUDE.md).** Before any `systemctl --user restart jarvis-voice-agent.service` OR `...jarvis-voice-client.service`, check `~/.local/share/jarvis/turn_telemetry.db` for `ts_utc` within the last 60 s. If a session is active, ask the user before restarting.
- **No Co-Authored-By trailers, no "🤖 Generated with Claude Code" attribution** in any commit or PR body. Ever.
- **Desktop two-step (.claude/rules/desktop-tauri.md).** `npm run build` alone does NOT ship JS changes — Tauri embeds `dist/` into the Rust binary at compile time. After any JS change, run `cargo build --release` (or `npm run tauri dev` for dev). Release builds require both.
- **Tray indicator is FROZEN.** The 7 voice-state colors, the magenta share ring, the icon, and the React→Rust poll rate are FINAL. This plan adds ONE menu item to the menu list; it does NOT touch the indicator. If anything in `tray_image_for`, `apply_sharing_ring`, the state set, or `icons/tray.png` looks like it needs changing, STOP and ask.
- **`src/cli/` is off-limits** for this work. Do not modify the bridge at `127.0.0.1:8765` or anything under `src/cli/`.
- **TDD where it pays off.** Voice-agent + voice-client changes get pytest first. The React component does not — desktop-tauri has no React test infra; manual smoke + `npm run build` typecheck are the gate.

---

## Task 1: Voice agent — `assistant_says` publish in `conversation_item_added`

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` (right after the barge-in-truncation block inside `_on_item` at line ~4974)
- Test: `src/voice-agent/tests/test_assistant_says_publish.py` (new)

**What this task does:** Extends the existing `conversation_item_added` handler so every assistant turn ALSO publishes a `{"type":"assistant_says", "text", "ts_ms"}` data packet on the LiveKit room. The voice client (Task 3) will pick it up. Idempotent via an `_jarvis_published_says` attribute on the item so a re-fire of the same item doesn't double-publish.

### Sub-tasks

- [ ] **Step 1.1: Confirm working directory + skim the existing handler**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
sed -n '4970,5070p' jarvis_agent.py | head -50
```

Confirm `_on_item` is defined inside `entrypoint(ctx)` and extracts `role` + `text` from `ev.item`. Note the barge-in truncation block — your new code goes RIGHT AFTER it but BEFORE the `_mark_thinking_end` / silent-mode autoflip / memory-extractor blocks.

- [ ] **Step 1.2: Create the failing test file**

Create `src/voice-agent/tests/test_assistant_says_publish.py`:

```python
"""Tests for the assistant_says data-channel publish added to
jarvis_agent._on_item (conversation_item_added handler).

The handler is registered inside entrypoint(ctx) so we can't import
and call it directly — but the publish logic is small and easily
extracted to a module-level helper for testability. This file tests
the helper, and Task 4's smoke test exercises the wired handler."""
from __future__ import annotations

import asyncio
import json
import unittest.mock as mock

import pytest


@pytest.mark.asyncio
async def test_publishes_for_assistant_with_text():
    """role=assistant + non-empty text → publish_data fires once."""
    from jarvis_agent import maybe_publish_assistant_says

    room = mock.MagicMock()
    room.local_participant.publish_data = mock.AsyncMock()
    item = mock.MagicMock(spec=["_jarvis_published_says"])
    # Strip the marker so first call publishes.
    if hasattr(item, "_jarvis_published_says"):
        del item._jarvis_published_says

    await maybe_publish_assistant_says(
        room=room, item=item, role="assistant", text="hello world"
    )
    room.local_participant.publish_data.assert_awaited_once()
    call_args = room.local_participant.publish_data.await_args
    payload_bytes = call_args.args[0]
    payload = json.loads(payload_bytes.decode("utf-8"))
    assert payload["type"] == "assistant_says"
    assert payload["text"] == "hello world"
    assert "ts_ms" in payload
    assert isinstance(payload["ts_ms"], int)


@pytest.mark.asyncio
async def test_idempotent_does_not_double_publish():
    """Same item passed twice → publish_data fires exactly once."""
    from jarvis_agent import maybe_publish_assistant_says

    room = mock.MagicMock()
    room.local_participant.publish_data = mock.AsyncMock()
    item = mock.MagicMock(spec=["_jarvis_published_says"])
    if hasattr(item, "_jarvis_published_says"):
        del item._jarvis_published_says

    await maybe_publish_assistant_says(
        room=room, item=item, role="assistant", text="first"
    )
    await maybe_publish_assistant_says(
        room=room, item=item, role="assistant", text="first"
    )
    assert room.local_participant.publish_data.await_count == 1


@pytest.mark.asyncio
async def test_skips_user_role():
    """role=user → no publish."""
    from jarvis_agent import maybe_publish_assistant_says

    room = mock.MagicMock()
    room.local_participant.publish_data = mock.AsyncMock()
    item = mock.MagicMock(spec=["_jarvis_published_says"])

    await maybe_publish_assistant_says(
        room=room, item=item, role="user", text="hello"
    )
    room.local_participant.publish_data.assert_not_called()


@pytest.mark.asyncio
async def test_skips_empty_text():
    """text="" → no publish."""
    from jarvis_agent import maybe_publish_assistant_says

    room = mock.MagicMock()
    room.local_participant.publish_data = mock.AsyncMock()
    item = mock.MagicMock(spec=["_jarvis_published_says"])

    await maybe_publish_assistant_says(
        room=room, item=item, role="assistant", text=""
    )
    room.local_participant.publish_data.assert_not_called()


@pytest.mark.asyncio
async def test_swallows_publish_exceptions():
    """If publish_data raises, helper logs and returns — does not propagate."""
    from jarvis_agent import maybe_publish_assistant_says

    room = mock.MagicMock()
    room.local_participant.publish_data = mock.AsyncMock(
        side_effect=RuntimeError("room closed")
    )
    item = mock.MagicMock(spec=["_jarvis_published_says"])

    # Should not raise.
    await maybe_publish_assistant_says(
        room=room, item=item, role="assistant", text="boom"
    )
```

- [ ] **Step 1.3: Run the test to verify it fails**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_assistant_says_publish.py -v
```

Expected: ImportError / AttributeError on `maybe_publish_assistant_says` (the helper doesn't exist yet).

- [ ] **Step 1.4: Add the helper to jarvis_agent.py**

Find a sensible module-level home for the helper — directly above the `async def entrypoint` definition is appropriate (it's a helper that the entrypoint's closure-registered handler calls). Add:

```python
async def maybe_publish_assistant_says(
    *,
    room: "rtc.Room",
    item: object,
    role: str | None,
    text: str | None,
) -> None:
    """Mirror an assistant chat item to the LiveKit data channel as
    `{"type": "assistant_says", "text", "ts_ms"}`. Idempotent — tags
    the item with `_jarvis_published_says=True` on first publish and
    no-ops on subsequent calls for the same item.

    Used by the conversation_item_added handler to feed the desktop
    tray chat panel (and any other future SSE subscriber). Errors are
    logged at debug and swallowed — a publish failure must not break
    voice-mode chat-ctx accounting.
    """
    if role != "assistant":
        return
    if not (text or "").strip():
        return
    if getattr(item, "_jarvis_published_says", False):
        return
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
            # Read-only item (e.g. __slots__) — best effort. May
            # double-publish on re-fire; LiveKit + the SSE subscriber
            # set both tolerate that.
            pass
        await room.local_participant.publish_data(payload, reliable=True)
    except Exception as _e:
        logger.debug(f"[chat-panel] assistant_says publish failed: {_e!r}")
```

- [ ] **Step 1.5: Run the test to verify it passes**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_assistant_says_publish.py -v
```

Expected: 5 passed.

- [ ] **Step 1.6: Wire the helper into the conversation_item_added handler**

Open `src/voice-agent/jarvis_agent.py` and find the existing `_on_item` handler (around line 4974). Right AFTER the barge-in truncation block and BEFORE the `_mark_thinking_end` / silent-mode-autoflip blocks, add:

```python
            # Mirror assistant turns to the LiveKit data channel for
            # subscribers like the tray chat panel. Idempotent; no-ops
            # for user turns + empty text. Fire-and-forget — the helper
            # swallows publish errors.
            asyncio.create_task(
                maybe_publish_assistant_says(
                    room=ctx.room, item=item, role=role, text=text,
                )
            )
```

Match the existing indentation level of the surrounding code in `_on_item` (8 spaces inside the `try:` block).

- [ ] **Step 1.7: Run the FULL voice-agent test suite to verify no regression**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/ -x -q
```

Expected: all tests pass. If anything fails that didn't fail before this change, the new line broke something — STOP and investigate.

- [ ] **Step 1.8: Commit**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_assistant_says_publish.py
git commit -m "$(cat <<'EOF'
feat(voice-agent): publish assistant_says on conversation_item_added

Add maybe_publish_assistant_says helper + wire it into the existing
_on_item handler so every assistant turn mirrors to the LiveKit data
channel as {type:assistant_says, text, ts_ms}. Idempotent via an
_jarvis_published_says marker on the item.

Consumer (voice client SSE) lands in next commits; this commit is a
no-op in production until something subscribes.
EOF
)"
```

---

## Task 2: Voice client HTTP API — SSE subscriber state + `/events` route + `enqueue_event`

**Files:**
- Modify: `src/voice-agent/voice_client_http_api.py` (add subscriber set in `__init__`, register `/events` route in `build_app`, add `events` and `enqueue_event` methods)
- Test: `src/voice-agent/tests/test_voice_client_events_sse.py` (new)

**What this task does:** Adds the in-process pub-sub plumbing to `VoiceClientHttpApi`. A `set[asyncio.Queue]` holds live SSE subscribers; the new `/events` route streams `data: {json}\n\n` lines from each subscriber's queue; the new `enqueue_event(event)` method broadcasts to all subscribers with put_nowait + drop-oldest on QueueFull. Task 3 wires the data-channel hook that calls `enqueue_event`.

### Sub-tasks

- [ ] **Step 2.1: Read the existing class to anchor the diff**

Run:
```bash
sed -n '77,150p' /home/ulrich/Documents/Projects/jarvis/src/voice-agent/voice_client_http_api.py
```

Note: the class is `VoiceClientHttpApi`, `__init__` takes keyword-only args, `build_app` registers all routes. `asyncio` + `json` are imported at module top (lines 36-37). `aiohttp.web` is the framework (line 43).

- [ ] **Step 2.2: Create the failing test file**

Create `src/voice-agent/tests/test_voice_client_events_sse.py`:

```python
"""Tests for the SSE /events route + enqueue_event broadcast added to
VoiceClientHttpApi.

Boots the aiohttp app in a test client harness, subscribes a fake SSE
consumer, and verifies that enqueue_event surfaces as a data: line
on the wire. Also verifies subscriber add/remove on connect/disconnect
and queue back-pressure (drop oldest on full)."""
from __future__ import annotations

import asyncio
import json
import logging
import unittest.mock as mock

import pytest
from aiohttp.test_utils import TestClient, TestServer


def _make_api():
    """Construct a VoiceClientHttpApi with stub deps for HTTP-only tests."""
    from voice_client_http_api import VoiceClientHttpApi
    state = mock.MagicMock()
    return VoiceClientHttpApi(
        state=state,
        get_mic_pub=lambda: None,
        get_room=lambda: None,
        get_screen_share=lambda: None,
        restart_agent_unit=mock.AsyncMock(),
        log=logging.getLogger("test"),
    )


@pytest.mark.asyncio
async def test_subscribers_added_on_connect_and_removed_on_disconnect():
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        assert len(api._sse_subscribers) == 0
        async with client.get("/events") as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/event-stream")
            # Wait briefly for the route to register its queue.
            for _ in range(20):
                if len(api._sse_subscribers) == 1:
                    break
                await asyncio.sleep(0.01)
            assert len(api._sse_subscribers) == 1
        # After client closes, finally block should remove subscriber.
        for _ in range(20):
            if len(api._sse_subscribers) == 0:
                break
            await asyncio.sleep(0.01)
        assert len(api._sse_subscribers) == 0


@pytest.mark.asyncio
async def test_enqueue_event_emits_data_line():
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        async with client.get("/events") as resp:
            # Wait for subscriber to register.
            for _ in range(20):
                if len(api._sse_subscribers) == 1:
                    break
                await asyncio.sleep(0.01)
            api.enqueue_event({"type": "assistant_says", "text": "hi"})
            # Read one SSE frame.
            line = await asyncio.wait_for(resp.content.readline(), timeout=2.0)
            assert line.startswith(b"data: ")
            payload = json.loads(line[len(b"data: "):].decode("utf-8").strip())
            assert payload == {"type": "assistant_says", "text": "hi"}


@pytest.mark.asyncio
async def test_enqueue_event_with_no_subscribers_is_noop():
    api = _make_api()
    # No SSE clients connected.
    api.enqueue_event({"type": "assistant_says", "text": "nobody home"})
    # Should not raise. Nothing else to assert — subscriber set is empty.
    assert len(api._sse_subscribers) == 0


@pytest.mark.asyncio
async def test_queue_full_drops_oldest():
    """Back-pressure: when a subscriber's queue is full, oldest is dropped
    and newest is kept (LIFO-ish bounded behavior)."""
    api = _make_api()
    # Inject a tiny queue directly to force overflow.
    q: asyncio.Queue = asyncio.Queue(maxsize=2)
    api._sse_subscribers.add(q)
    api.enqueue_event({"i": 1})
    api.enqueue_event({"i": 2})
    api.enqueue_event({"i": 3})  # overflow — should drop {i:1} and keep 2, 3
    items = [q.get_nowait(), q.get_nowait()]
    indices = sorted(item["i"] for item in items)
    assert indices == [2, 3]


@pytest.mark.asyncio
async def test_cors_preflight_for_events():
    """OPTIONS /events should hit the existing CORS wildcard."""
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        async with client.options("/events") as resp:
            assert resp.status == 200
            assert resp.headers.get("Access-Control-Allow-Origin") == "*"
```

- [ ] **Step 2.3: Run the test to verify it fails**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_voice_client_events_sse.py -v
```

Expected: failures on missing `_sse_subscribers`, `enqueue_event`, and the `/events` route.

- [ ] **Step 2.4: Add the subscriber state to `__init__`**

In `src/voice-agent/voice_client_http_api.py`, find the `__init__` method and at the bottom (after `self.log = log`), add:

```python
        # SSE subscribers — see /events route + enqueue_event below.
        # Each entry is an asyncio.Queue owned by one live HTTP response
        # writer. Modified only from the asyncio loop (no locking needed).
        self._sse_subscribers: set[asyncio.Queue] = set()
```

- [ ] **Step 2.5: Add the `/events` route to `build_app`**

In `build_app`, just before `app.router.add_route("OPTIONS", "/{tail:.*}", self.cors)`, add:

```python
        app.router.add_get("/events",      self.events)
```

- [ ] **Step 2.6: Add the `events` handler method**

Add the `events` method to the `VoiceClientHttpApi` class. A good location is right above `cors` at the bottom of the class:

```python
    async def events(self, req: web.Request) -> web.StreamResponse:
        """GET /events → Server-Sent Events stream of voice-agent events.

        Today the only published event type is `assistant_says` (each
        assistant turn emits one). Subscribers register an
        asyncio.Queue; on disconnect, the queue is removed.

        Frames are `data: {json}\\n\\n` per SSE spec. Per-subscriber
        queue is bounded to 64 events; on overflow `enqueue_event`
        drops oldest.
        """
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
        self.log.info(f"[events] subscriber connected ({len(self._sse_subscribers)} total)")
        try:
            while True:
                event = await queue.get()
                line = f"data: {json.dumps(event)}\n\n".encode("utf-8")
                await resp.write(line)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        except Exception as e:
            self.log.warning(f"[events] subscriber write failed: {type(e).__name__}: {e}")
        finally:
            self._sse_subscribers.discard(queue)
            self.log.info(f"[events] subscriber disconnected ({len(self._sse_subscribers)} remaining)")
        return resp
```

- [ ] **Step 2.7: Add the `enqueue_event` method**

Add right below `events`:

```python
    def enqueue_event(self, event: dict) -> None:
        """Broadcast a JSON event to every live SSE subscriber.

        Safe to call from any callback running on the asyncio event
        loop (sync, non-blocking). On QueueFull, drops the oldest item
        and enqueues the new one so a stuck panel doesn't pin memory.

        Called from jarvis_voice_client.py's data_received hook when an
        `assistant_says` packet arrives from the agent participant.
        """
        # Snapshot the subscriber set — enqueue can race with /events'
        # finally block, and iterating a mutating set raises.
        for q in list(self._sse_subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except asyncio.QueueEmpty:
                    # Concurrently drained — give up; next event will retry.
                    pass
```

- [ ] **Step 2.8: Run the tests to verify they pass**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_voice_client_events_sse.py -v
```

Expected: 5 passed.

- [ ] **Step 2.9: Run the FULL voice-agent test suite**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/ -x -q
```

Expected: all tests pass.

- [ ] **Step 2.10: Commit**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/voice_client_http_api.py src/voice-agent/tests/test_voice_client_events_sse.py
git commit -m "$(cat <<'EOF'
feat(voice-client): SSE /events route + enqueue_event broadcaster

Add VoiceClientHttpApi._sse_subscribers + GET /events route that
streams data: {json}\n\n frames per SSE spec, with bounded per-
subscriber queues (drop-oldest on overflow). enqueue_event method
exposed for the data-channel hook landing in the next commit.

Still a no-op end-to-end — nothing calls enqueue_event yet.
EOF
)"
```

---

## Task 3: Voice client room loop — `data_received` handler

**Files:**
- Modify: `src/voice-agent/jarvis_voice_client.py` (register `room.on("data_received")` near other `room.on(...)` decorators at lines 607-660)

**What this task does:** Adds the LiveKit data-channel consumer that bridges agent → voice-client → SSE. Filters for `assistant_says` packets only and calls `http_api.enqueue_event(...)`. After this commit, both restarts happen.

### Sub-tasks

- [ ] **Step 3.1: Locate the existing `room.on` block + `http_api` reference**

Run:
```bash
grep -n "room.on(\|http_api =\|VoiceClientHttpApi(" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_voice_client.py | head -20
```

Note `http_api = VoiceClientHttpApi(...)` is at line ~1135 inside `run_once`. The `room.on(...)` decorators are clustered around lines 607-660 inside a different function. You need to add the data_received handler in the same scope where both `room` and `http_api` are visible — likely AFTER `http_api` is constructed and the room is established. Skim a few lines around 1135 + 660 to confirm visibility.

- [ ] **Step 3.2: Read the existing `_on_data` in the agent for reference**

Run:
```bash
sed -n '5564,5605p' /home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py
```

Mirror this shape (sync def, defensive JSON parse, dict-shape check). The voice-client side does NOT use `_asyncio` aliasing — top-level `import asyncio` per `voice_client_http_api.py:36` is the pattern, but `jarvis_voice_client.py` may differ — check its imports.

Run:
```bash
grep -n "^import\|^from" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_voice_client.py | head -20
```

Use whatever names are at module-top in that file.

- [ ] **Step 3.3: Add the data-received handler**

In `src/voice-agent/jarvis_voice_client.py`, in the scope where both `room` (the rtc.Room) and `http_api` (the VoiceClientHttpApi singleton) are accessible — i.e., AFTER `http_api = VoiceClientHttpApi(...)` is constructed — register:

```python
    @room.on("data_received")
    def _on_data_received(packet) -> None:
        """Forward assistant_says packets from the agent participant to
        the SSE subscribers (the tray chat panel).

        LiveKit's data_received fires only for packets from REMOTE
        participants — self-published data (the /user-input + /speak
        + /stop emits this voice-client makes) does not loop back here,
        so the filter is defense-in-depth only.
        """
        try:
            import json as _json_dr
            msg = _json_dr.loads(packet.data.decode("utf-8"))
        except Exception:
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") != "assistant_says":
            return
        text = (msg.get("text") or "").strip()
        if not text:
            return
        try:
            http_api.enqueue_event({
                "type": "assistant_says",
                "text": text,
                "ts_ms": msg.get("ts_ms"),
            })
        except Exception as e:
            log.debug(f"[chat-panel] enqueue_event failed: {e!r}")
```

If `log` is named differently in this file, match the local logger name. If `room` is named differently in this scope (e.g. `ctx.room` or `self.room`), match it.

- [ ] **Step 3.4: Smoke-test the import**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -c "import jarvis_voice_client; print('ok')"
```

Expected: `ok` with no traceback.

- [ ] **Step 3.5: Run the full voice-agent test suite**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/ -x -q
```

Expected: all tests pass.

- [ ] **Step 3.6: Commit**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/jarvis_voice_client.py
git commit -m "$(cat <<'EOF'
feat(voice-client): data_received hook forwards assistant_says to SSE

Register room.on(data_received) alongside the existing track_subscribed
+ participant_connected decorators. Filters for {type:assistant_says}
packets from the agent participant and broadcasts via the new
http_api.enqueue_event method.

End-to-end live after this commit pending service restart — the panel
side lands next.
EOF
)"
```

---

## Task 4: Restart both services + verify the agent→client→SSE pipe end-to-end

**Files:** none modified — operational + verification only.

### Sub-tasks

- [ ] **Step 4.1: Pre-restart check — is a session active?**

Run:
```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db \
  "SELECT datetime(ts_utc, 'unixepoch') AS ts, role, substr(text, 1, 40) FROM turns ORDER BY ts_utc DESC LIMIT 3"
```

If the most recent `ts` is within the last 60 s, STOP and ask the user before restarting. Otherwise proceed.

- [ ] **Step 4.2: Restart both services**

Run:
```bash
systemctl --user restart jarvis-voice-agent.service
systemctl --user restart jarvis-voice-client.service
```

- [ ] **Step 4.3: Verify both are running**

Run:
```bash
systemctl --user is-active jarvis-voice-agent.service jarvis-voice-client.service
```

Expected: `active\nactive`.

- [ ] **Step 4.4: Verify the voice client `/status` endpoint**

Run:
```bash
sleep 3 && curl -s http://127.0.0.1:8767/status | python3 -m json.tool
```

Expected: JSON status object including `"connected": true` (or `false` if the agent hasn't joined the room yet — wait another 5 s and retry).

- [ ] **Step 4.5: Verify `/events` SSE stream is alive**

Run in one terminal (foreground):
```bash
curl -N http://127.0.0.1:8767/events
```

Leave this running. The terminal will hang on the open stream.

- [ ] **Step 4.6: Trigger a synthetic user_input from another terminal**

Run:
```bash
curl -s -X POST http://127.0.0.1:8767/user-input \
  -H 'Content-Type: application/json' \
  -d '{"text":"Say the word ping out loud."}'
```

Expected response: `{"queued":true,"chars":...}`.

Within ~2 s you should:
- Hear JARVIS speak (audio out).
- See a `data: {"type":"assistant_says","text":"...","ts_ms":...}` line appear in the `curl -N /events` terminal.

If no SSE line appears, check `~/.local/share/jarvis/logs/voice-agent.log` and `voice-client.log` for `[chat-panel]` or `[events]` lines.

- [ ] **Step 4.7: Stop the SSE listener**

Ctrl-C the `curl -N` terminal. In the service logs, you should see `[events] subscriber disconnected (0 remaining)`.

- [ ] **Step 4.8: No commit — this task is verification only.**

If everything passed, proceed to Task 5. If something failed, fix it in the appropriate prior task and re-run Step 4.5 + 4.6.

---

## Task 5: New `VoiceChatPanel.jsx` React component

**Files:**
- Create: `src/voice-agent/desktop-tauri/src/components/VoiceChatPanel.jsx`

**What this task does:** A minimal floating chat overlay — header, message list (user + jarvis bubbles), text input, send button. Sends via `POST /user-input` directly; subscribes to `EventSource('/events')` for `assistant_says` events. No streaming, no tool-call UI, no history sidebar. No bridge. Uses the same dark-theme palette + drag/resize patterns as the existing `ChatPanel.jsx`.

### Sub-tasks

- [ ] **Step 5.1: Create the component file**

Create `src/voice-agent/desktop-tauri/src/components/VoiceChatPanel.jsx`:

```jsx
import { useState, useRef, useEffect, useCallback } from 'react'

// ── Theme tokens (match ChatPanel.jsx for visual consistency) ──
const SURFACE   = '#0d1117'
const SURFACE_2 = '#151b23'
const BORDER    = 'rgba(255,255,255,0.08)'
const BORDER_STRONG = 'rgba(255,255,255,0.14)'
const TEXT      = '#e6edf3'
const TEXT_DIM  = '#8b949e'
const TEXT_MUTE = '#6e7681'
const ACCENT    = '#4493f8'
const ACCENT_BG = 'rgba(68,147,248,0.14)'

const VC_BASE = 'http://127.0.0.1:8767'

// ── Inline SVG icons ─────────────────────────────────────────────────
const Icon = {
  Close: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
  ),
  Send: (p) => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M22 2 11 13"/><path d="m22 2-7 20-4-9-9-4 20-7Z"/></svg>
  ),
  Lock: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
  ),
  LockOpen: (p) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/></svg>
  ),
}

export default function VoiceChatPanel({
  isOpen,
  onClose,
  voiceMuted,
  setVoiceMuted,
}) {
  const [messages, setMessages] = useState([
    { role: 'jarvis', text: 'Type to me. I will reply with my voice.' },
  ])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [sseConnected, setSseConnected] = useState(false)
  const [autoMute, setAutoMute] = useState(true)
  const [status, setStatus] = useState(null)
  const messagesContainerRef = useRef(null)
  const inputRef = useRef(null)
  const priorMutedRef = useRef(false)

  // ── Mount fade (200 ms) ──────────────────────────────────────────
  const [mounted, setMounted] = useState(isOpen)
  useEffect(() => {
    if (isOpen) setMounted(true)
    else {
      const t = setTimeout(() => setMounted(false), 200)
      return () => clearTimeout(t)
    }
  }, [isOpen])

  // ── SSE subscription to /events ──────────────────────────────────
  useEffect(() => {
    if (!isOpen) return
    const es = new EventSource(`${VC_BASE}/events`)
    es.onopen = () => setSseConnected(true)
    es.onerror = () => setSseConnected(false)
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data)
        if (data.type === 'assistant_says' && data.text) {
          setMessages((prev) => [...prev, { role: 'jarvis', text: data.text }])
        }
      } catch {
        // ignore malformed frames
      }
    }
    return () => es.close()
  }, [isOpen])

  // ── Auto-scroll on new message ───────────────────────────────────
  useEffect(() => {
    const c = messagesContainerRef.current
    if (c) c.scrollTop = c.scrollHeight
  }, [messages])

  // ── Focus input on open ──────────────────────────────────────────
  useEffect(() => {
    if (isOpen) setTimeout(() => inputRef.current?.focus(), 100)
  }, [isOpen])

  // ── Send via /user-input ─────────────────────────────────────────
  const sendMessage = useCallback(async () => {
    const text = input.trim()
    if (!text || sending) return
    setInput('')
    setMessages((prev) => [...prev, { role: 'user', text }])
    setSending(true)
    setStatus(null)
    try {
      const res = await fetch(`${VC_BASE}/user-input`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      })
      if (res.status === 503) {
        setStatus('Voice agent not connected to a session yet.')
      } else if (!res.ok) {
        setStatus(`Send failed: HTTP ${res.status}`)
      }
    } catch (e) {
      setStatus('Voice agent offline.')
    } finally {
      setSending(false)
    }
  }, [input, sending])

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }
    if (e.key === 'Escape') onClose()
  }

  // ── Mic auto-mute on focus / restore on blur ─────────────────────
  const onInputFocus = useCallback(() => {
    if (!autoMute) return
    priorMutedRef.current = !!voiceMuted
    if (!voiceMuted) setVoiceMuted(true)
  }, [autoMute, voiceMuted, setVoiceMuted])

  const onInputBlur = useCallback(() => {
    if (!autoMute) return
    if (voiceMuted !== priorMutedRef.current) setVoiceMuted(priorMutedRef.current)
  }, [autoMute, voiceMuted, setVoiceMuted])

  // Restore mute state on close.
  useEffect(() => {
    if (!isOpen && autoMute && voiceMuted !== priorMutedRef.current) {
      setVoiceMuted(priorMutedRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen])

  if (!mounted) return null

  const statusColor = sseConnected ? '#3fb950' : '#d29922'

  return (
    <div
      className={`fixed flex z-999 overflow-hidden transition-opacity duration-150 ${
        isOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'
      }`}
      style={{
        left: 'calc(50% - 240px)',
        top:  'calc(50% - 280px)',
        width: 480,
        height: 560,
        background: SURFACE,
        border: `1px solid ${BORDER}`,
        borderRadius: '12px',
        boxShadow: '0 20px 60px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.02)',
        fontFamily: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
        color: TEXT,
        isolation: 'isolate',
        willChange: 'transform, opacity',
        transform: 'translateZ(0)',
        flexDirection: 'column',
      }}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <style>{`
        @keyframes msg-in { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
      `}</style>

      {/* Header */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '12px 16px', borderBottom: `1px solid ${BORDER}`, userSelect: 'none',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{
            width: '8px', height: '8px', borderRadius: '50%',
            background: statusColor, boxShadow: `0 0 6px ${statusColor}80`,
          }} title={sseConnected ? 'SSE connected' : 'Reconnecting…'} />
          <span style={{ fontSize: '14px', fontWeight: 600, color: TEXT, letterSpacing: '-0.01em' }}>
            Jarvis (voice)
          </span>
        </div>
        <div style={{ display: 'flex', gap: '2px', alignItems: 'center' }}>
          <HeaderButton
            title={autoMute ? 'Mic auto-mute ON (click to disable)' : 'Mic auto-mute OFF (click to enable)'}
            onClick={() => setAutoMute(v => !v)}
            active={autoMute}
          >
            {autoMute ? <Icon.Lock /> : <Icon.LockOpen />}
          </HeaderButton>
          <HeaderButton title="Close (Esc)" onClick={onClose}>
            <Icon.Close />
          </HeaderButton>
        </div>
      </div>

      {/* Messages */}
      <div
        ref={messagesContainerRef}
        style={{
          flex: 1, overflowY: 'auto', padding: '18px 20px',
          display: 'flex', flexDirection: 'column', gap: '14px',
          scrollbarWidth: 'thin',
        }}
      >
        {messages.map((msg, i) => (
          <div key={i} style={{
            display: 'flex', flexDirection: 'column',
            alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start',
            animation: 'msg-in 200ms ease',
          }}>
            {msg.role === 'user' ? (
              <div style={{
                maxWidth: '78%',
                padding: '10px 14px', borderRadius: '14px 14px 4px 14px',
                background: ACCENT_BG, border: `1px solid ${BORDER}`,
                fontSize: '14px', lineHeight: 1.55, color: TEXT,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}>
                {msg.text}
              </div>
            ) : (
              <div style={{
                maxWidth: '92%',
                fontSize: '14px', lineHeight: 1.6, color: TEXT,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}>
                {msg.text}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Status line */}
      {status && (
        <div style={{
          padding: '6px 16px', fontSize: '12px', color: '#d29922',
          borderTop: `1px solid ${BORDER}`,
        }}>{status}</div>
      )}

      {/* Input */}
      <div style={{ padding: '12px 16px', borderTop: `1px solid ${BORDER}` }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: '8px',
          background: SURFACE_2, border: `1px solid ${BORDER_STRONG}`,
          borderRadius: '10px', padding: '6px 6px 6px 14px',
        }}>
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            onFocus={onInputFocus}
            onBlur={onInputBlur}
            placeholder="Type to Jarvis…"
            autoComplete="off"
            style={{
              flex: 1, background: 'transparent', border: 'none', outline: 'none',
              color: TEXT, fontSize: '14px', fontFamily: 'inherit', padding: '8px 0',
            }}
          />
          <button
            onClick={sendMessage}
            disabled={sending || !input.trim()}
            style={{
              background: input.trim() && !sending ? ACCENT : 'transparent',
              border: 'none',
              color: input.trim() && !sending ? '#fff' : TEXT_MUTE,
              cursor: input.trim() && !sending ? 'pointer' : 'default',
              padding: '8px 10px', borderRadius: '8px',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              opacity: sending ? 0.5 : 1,
            }}
            title="Send message"
          >
            <Icon.Send />
          </button>
        </div>
      </div>
    </div>
  )
}

function HeaderButton({ children, onClick, title, active }) {
  const [hover, setHover] = useState(false)
  const bg = active
    ? 'rgba(68,147,248,0.14)'
    : hover ? 'rgba(255,255,255,0.06)' : 'transparent'
  const color = active ? '#4493f8' : hover ? TEXT : TEXT_DIM
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title={title}
      style={{
        background: bg, border: 'none', color, cursor: 'pointer',
        padding: '6px 8px', borderRadius: '6px',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      {children}
    </button>
  )
}
```

- [ ] **Step 5.2: Smoke-test the vite build**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent/desktop-tauri
npm run build 2>&1 | tail -20
```

Expected: vite build completes without errors (note: the component is NOT imported anywhere yet, so it'll be tree-shaken out — but vite still parses it).

- [ ] **Step 5.3: Commit**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/desktop-tauri/src/components/VoiceChatPanel.jsx
git commit -m "$(cat <<'EOF'
feat(desktop): new VoiceChatPanel component (voice-agent direct)

Minimal floating chat overlay that talks directly to the voice client
at 127.0.0.1:8767 — POST /user-input on send, EventSource /events for
assistant_says replies. No bridge involvement.

Not yet wired into App.jsx — that lands in the next commit.
EOF
)"
```

---

## Task 6: Wire `VoiceChatPanel` into `App.jsx`

**Files:**
- Modify: `src/voice-agent/desktop-tauri/src/App.jsx` (add `voiceChatOpen` state, render the component, add tray-event listeners)

### Sub-tasks

- [ ] **Step 6.1: Read the relevant App.jsx context**

Run:
```bash
grep -n "ChatPanel\|chatOpen\|setChatOpen\|tray-toggle-chat\|tray-open-chat\|tray-close-chat\|import.*from './components" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/desktop-tauri/src/App.jsx | head -30
```

Note the existing `chatOpen` state at line 80, the existing `<ChatPanel>` render at line ~297, the existing tray-event listeners at lines 186-194.

- [ ] **Step 6.2: Add the VoiceChatPanel import**

In `src/voice-agent/desktop-tauri/src/App.jsx`, find the existing `import ChatPanel from './components/ChatPanel.jsx'` line and add directly below it:

```jsx
import VoiceChatPanel from './components/VoiceChatPanel.jsx'
```

- [ ] **Step 6.3: Add the `voiceChatOpen` state**

Just below the existing `const [chatOpen, setChatOpen] = useState(false)` line, add:

```jsx
  const [voiceChatOpen, setVoiceChatOpen] = useState(false)
```

- [ ] **Step 6.4: Add open/close callbacks**

Just below the existing `openChat` / `closeChat` callbacks, add:

```jsx
  const openVoiceChat  = useCallback(() => setVoiceChatOpen(true),  [])
  const closeVoiceChat = useCallback(() => setVoiceChatOpen(false), [])
```

- [ ] **Step 6.5a: Add a ref to track voiceChatOpen for the tray-toggle handler**

Find the existing `chatOpenRef` block (around lines 181-182):

```jsx
  const chatOpenRef = useRef(chatOpen)
  useEffect(() => { chatOpenRef.current = chatOpen }, [chatOpen])
```

Directly below it, add the mirror for voice-chat:

```jsx
  const voiceChatOpenRef = useRef(voiceChatOpen)
  useEffect(() => { voiceChatOpenRef.current = voiceChatOpen }, [voiceChatOpen])
```

This pattern (ref synced via separate useEffect) is what the existing code uses so the tray-toggle listener doesn't have to re-subscribe on every state change.

- [ ] **Step 6.5b: Add three new tray-event listeners**

Inside the existing `useEffect` that calls `listen('tray-open-chat', ...)` etc. (around lines 186-194), add three more listener registrations alongside the existing `unlisten1`/`unlisten2`/`unlisten3`/`unlisten4`:

```jsx
    const unlistenV1 = listen('tray-open-voice-chat',   () => openVoiceChat())
    const unlistenV2 = listen('tray-close-voice-chat',  () => closeVoiceChat())
    const unlistenV3 = listen('tray-toggle-voice-chat', () => {
      if (voiceChatOpenRef.current) closeVoiceChat()
      else                          openVoiceChat()
    })
```

- [ ] **Step 6.5c: Extend the cleanup function**

Read the cleanup return in that useEffect (just before line 201 / the `}, [openChat, closeChat])` line) to see the exact unlisten pattern. The existing code resolves the promise — typical Tauri shape is something like:

```jsx
    return () => {
      unlisten1.then(fn => fn())
      unlisten2.then(fn => fn())
      unlisten3.then(fn => fn())
      unlisten4.then(fn => fn())
    }
```

Add the three new ones inside the same return:

```jsx
      unlistenV1.then(fn => fn())
      unlistenV2.then(fn => fn())
      unlistenV3.then(fn => fn())
```

If the existing pattern differs (e.g. uses `await` or a different shape), match it exactly. Also extend the dependency array (`[openChat, closeChat]`) to `[openChat, closeChat, openVoiceChat, closeVoiceChat]`.

- [ ] **Step 6.6: Render the panel**

Find the existing `<ChatPanel ... />` JSX (around line 297). Directly after the `{chatOpen && <ChatPanel .../>}` block, add:

```jsx
      {voiceChatOpen && (
        <VoiceChatPanel
          isOpen={voiceChatOpen}
          onClose={closeVoiceChat}
          voiceMuted={voiceMuted}
          setVoiceMuted={setVoiceMuted}
        />
      )}
```

- [ ] **Step 6.7: Build to verify**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent/desktop-tauri
npm run build 2>&1 | tail -20
```

Expected: vite build completes; the component is now reachable from `App.jsx`, so it's actually included in the bundle.

- [ ] **Step 6.8: Commit**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/desktop-tauri/src/App.jsx
git commit -m "$(cat <<'EOF'
feat(desktop): wire VoiceChatPanel into App.jsx via tray events

Add voiceChatOpen state, tray-{open,close,toggle}-voice-chat listeners,
and the panel render. Existing ChatPanel + Ctrl+H + Ctrl+Shift+Space
behavior untouched.

Tray menu entry that fires tray-toggle-voice-chat lands in the next
commit.
EOF
)"
```

---

## Task 7: Restore the tray menu entry

**Files:**
- Modify: `src/voice-agent/desktop-tauri/src-tauri/src/main.rs` (add the `MenuItemBuilder::with_id("open_voice_chat", "Open Chat Panel")` + the match arm)

### Sub-tasks

- [ ] **Step 7.1: Read the existing tray-menu-build region**

Run:
```bash
sed -n '1494,1530p' /home/ulrich/Documents/Projects/jarvis/src/voice-agent/desktop-tauri/src-tauri/src/main.rs
```

And the match-arm region:
```bash
sed -n '1685,1730p' /home/ulrich/Documents/Projects/jarvis/src/voice-agent/desktop-tauri/src-tauri/src/main.rs
```

Confirm the comment at line ~1495 documenting how to restore "Open Chat Panel". The existing `open_chat` match arm emits `tray-toggle-chat` (the OLD event for the existing ChatPanel) — DO NOT reuse that id. The new entry uses `open_voice_chat` and emits `tray-toggle-voice-chat`.

- [ ] **Step 7.2: Declare the menu item**

Right above `let mute_item = MenuItemBuilder::with_id("mute", ...)` (around line 1503), add:

```rust
            // Re-introduced 2026-05-24 (see docs/superpowers/specs/
            // 2026-05-24-tray-chat-panel-design.md). Opens the new
            // VoiceChatPanel that talks directly to the voice agent
            // via :8767 — NOT the bridge-flavored ChatPanel.
            let voice_chat_item = MenuItemBuilder::with_id(
                "open_voice_chat", "Open Chat Panel",
            ).build(app)?;
```

- [ ] **Step 7.3: Insert it into the MenuBuilder chain**

Find the MenuBuilder construction (look near where `mute_item`, `share_item`, etc. are added via `.item(&...)`). Add `voice_chat_item` near the top of that chain — most natural before `mute_item`:

```rust
                .item(&voice_chat_item)
```

Match the existing indentation + chaining style. If you can't easily find the chain, run:

```bash
grep -n "\.item(&mute_item)\|MenuBuilder::new" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/desktop-tauri/src-tauri/src/main.rs | head -5
```

- [ ] **Step 7.4: Add the match arm**

Find the click-handler match block (around line 1700, where `"open_chat" => { ... }` lives). Add a new arm alongside it (before `_ => {}` default if present):

```rust
                        "open_voice_chat" => {
                            if let Some(w) = app_handle.get_webview_window("main") {
                                let _ = w.emit("tray-toggle-voice-chat", ());
                                println!("[JARVIS] voice-chat toggle requested via tray");
                            }
                        }
```

The exact `app_handle.get_webview_window("main")` shape should match what the existing `"open_chat" => { ... }` arm does — copy its window-fetch + emit pattern. If the existing arm names variables differently (e.g. uses a captured handle), match its names.

- [ ] **Step 7.5: Build & verify**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent/desktop-tauri
cargo build --release 2>&1 | tail -25
```

Expected: `cargo build --release` completes (this may take 1-3 minutes — Tauri's Rust build is not fast). No errors. Warnings about unused imports / variables are fine if they were there before.

- [ ] **Step 7.6: Restart the desktop app to load the new binary**

The desktop binary is launched by the user separately (it's not a systemd unit). Ask the user to:
1. Close the current JARVIS desktop window (or quit from the tray menu).
2. Re-launch from `bin/jarvis-desktop` or however they normally start it.

The new binary is at `src/voice-agent/desktop-tauri/src-tauri/target/release/jarvis-desktop` (or similar — check `tauri.conf.json`'s `productName` for the actual name).

- [ ] **Step 7.7: Smoke test — open the panel from the tray**

Right-click the tray icon → click "Open Chat Panel". Expected: a small floating panel appears centered on screen, with the header "Jarvis (voice)", an SSE-connected green dot, a friendly greeting, and a text input at the bottom.

If two "Open Chat Panel" entries appear or the wrong one opens, the existing comment-only "Open Chat Panel" reference at line ~1501 may have been uncommented — re-check Step 7.2 to confirm only ONE entry exists.

- [ ] **Step 7.8: Commit**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/desktop-tauri/src-tauri/src/main.rs
git commit -m "$(cat <<'EOF'
feat(desktop): restore "Open Chat Panel" tray menu entry

Removed 2026-05-10 when the floating chat overlay was deemed
non-primary; brought back as the entry point for the new
VoiceChatPanel. Emits tray-toggle-voice-chat (distinct from the
existing tray-toggle-chat that drives ChatPanel). Tray indicator
itself is untouched.
EOF
)"
```

---

## Task 8: End-to-end smoke test (the load-bearing manual verification)

**Files:** none modified.

### Sub-tasks

- [ ] **Step 8.1: Confirm services are up**

Run:
```bash
systemctl --user is-active jarvis-voice-agent.service jarvis-voice-client.service
curl -s http://127.0.0.1:8767/status | python3 -c 'import sys,json; d=json.load(sys.stdin); print("connected=", d.get("connected"))'
```

Both active; `connected=True`.

- [ ] **Step 8.2: Round-trip with mic muted**

1. Right-click tray → "Open Chat Panel" → panel opens.
2. Status dot in header is GREEN (SSE connected).
3. Click into the text input. Watch the tray indicator — it should turn to the muted state (mic auto-mute fired).
4. Type "Say hello back" + Enter.
5. **Audio:** JARVIS speaks "Hello" (or similar).
6. **Panel:** within ~2 s of the audio finishing, a new Jarvis bubble appears with the text JARVIS spoke.
7. Click outside the input (blur) — tray indicator returns to its pre-focus state.

If audio plays but no bubble appears: check `voice-client.log` for `[chat-panel] enqueue_event failed` or `[events]` lines. If neither appears, the data-channel hook may not be receiving — re-verify Task 4 Step 4.6.

If a bubble appears but no audio: voice agent is publishing but TTS broke. Out of scope for this work; investigate separately.

- [ ] **Step 8.3: Round-trip without auto-mute**

1. Click the lock icon in the panel header to disable auto-mute.
2. Click into the input. Tray indicator should NOT change.
3. Type "What did I just disable?" + Enter.
4. JARVIS speaks the reply + bubble appears.

- [ ] **Step 8.4: Offline behavior**

```bash
systemctl --user stop jarvis-voice-client.service
```

In the panel:
1. The SSE green dot should turn amber within ~5 s (EventSource onerror).
2. Type something + Enter. A "Voice agent offline" status line appears under the messages.

Then:
```bash
systemctl --user start jarvis-voice-client.service
```

3. Within ~5 s the green dot returns. Send again — works.

- [ ] **Step 8.5: Sanity check the existing ChatPanel still works**

Press Ctrl+H. The existing rich ChatPanel should open (different look — has history sidebar). Press Ctrl+H again to close. Both panels coexist independently.

- [ ] **Step 8.6: No commit — this task is verification only.**

If everything passed, the feature ships. If anything failed, fix it in the appropriate prior task.

---

## Task 9 (optional polish): Synthetic "agent wasn't ready" fallback in `_user_input_when_ready`

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` (existing `_user_input_when_ready` function at line ~5541)

**What this task does:** Today if the voice agent is mid-boot when a /user-input arrives, `_user_input_when_ready` silently drops the request after 3 s. The panel has no signal of this. This task adds a fallback `assistant_says` publish so the panel shows a bubble explaining what happened.

### Sub-tasks

- [ ] **Step 9.1: Read the existing function**

Run:
```bash
sed -n '5541,5575p' /home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py
```

Note the final `logger.warning(...)` line — that's where we add the fallback publish.

- [ ] **Step 9.2: Add a test**

Append to `src/voice-agent/tests/test_assistant_says_publish.py`:

```python
@pytest.mark.asyncio
async def test_fallback_publishes_when_ready_times_out():
    """When _user_input_when_ready exhausts its 3 s wait, it should
    publish a synthetic assistant_says explaining the timeout."""
    from jarvis_agent import maybe_publish_assistant_says
    # The fallback path lives inside _user_input_when_ready which is
    # closed over `session`/`ctx` from entrypoint — we can't unit-test
    # it directly here. This test asserts the helper's call surface
    # (which the fallback uses) accepts a synthetic item correctly.
    room = mock.MagicMock()
    room.local_participant.publish_data = mock.AsyncMock()
    item = mock.MagicMock(spec=["_jarvis_published_says"])

    await maybe_publish_assistant_says(
        room=room, item=item, role="assistant",
        text="(Couldn't process that — agent wasn't ready. Try again.)",
    )
    room.local_participant.publish_data.assert_awaited_once()
    payload = json.loads(
        room.local_participant.publish_data.await_args.args[0].decode("utf-8")
    )
    assert "agent wasn't ready" in payload["text"]
```

- [ ] **Step 9.3: Run the test to verify it passes**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_assistant_says_publish.py::test_fallback_publishes_when_ready_times_out -v
```

Expected: PASS (this test exercises only the helper surface, which already works).

- [ ] **Step 9.4: Add the fallback to `_user_input_when_ready`**

In `jarvis_agent.py`, find the `_user_input_when_ready` function. Replace the final dropped-utterance warning:

```python
        logger.warning(
            f"session.generate_reply unavailable after 3s — dropping: {text[:60]}"
        )
```

With:

```python
        logger.warning(
            f"session.generate_reply unavailable after 3s — dropping: {text[:60]}"
        )
        # Tell the panel side what happened — otherwise the chat sits
        # with the user's typed bubble and no reply, looking broken.
        try:
            import json as _json_fb
            payload = _json_fb.dumps({
                "type": "assistant_says",
                "text": "(Couldn't process that — agent wasn't ready. Try again.)",
                "ts_ms": int(time.monotonic() * 1000),
            }).encode("utf-8")
            await ctx.room.local_participant.publish_data(payload, reliable=True)
        except Exception as _e:
            logger.debug(f"[chat-panel] timeout fallback publish failed: {_e!r}")
```

- [ ] **Step 9.5: Run full pytest**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/ -x -q
```

Expected: all pass.

- [ ] **Step 9.6: Pre-restart check**

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db \
  "SELECT datetime(ts_utc, 'unixepoch') AS ts FROM turns ORDER BY ts_utc DESC LIMIT 1"
```

If within 60 s, ask the user first.

- [ ] **Step 9.7: Restart the voice agent**

```bash
systemctl --user restart jarvis-voice-agent.service
```

- [ ] **Step 9.8: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_assistant_says_publish.py
git commit -m "$(cat <<'EOF'
feat(voice-agent): fallback assistant_says when generate_reply unavailable

If _user_input_when_ready exhausts its 3s wait (rare — agent mid-boot),
publish a synthetic assistant_says explaining the timeout so the tray
chat panel doesn't sit silent with the user's typed bubble.
EOF
)"
```

---

## End-of-feature: SCOPE/OUT/VERIFY summary

When all tasks complete, run the project's end-of-task summary (per `.claude/rules/regression-prevention.md`):

```
CHANGED:
  - src/voice-agent/jarvis_agent.py — assistant_says publish + timeout fallback
  - src/voice-agent/voice_client_http_api.py — /events SSE route + enqueue_event
  - src/voice-agent/jarvis_voice_client.py — data_received hook → enqueue_event
  - src/voice-agent/tests/test_assistant_says_publish.py — new
  - src/voice-agent/tests/test_voice_client_events_sse.py — new
  - src/voice-agent/desktop-tauri/src/components/VoiceChatPanel.jsx — new
  - src/voice-agent/desktop-tauri/src/App.jsx — voiceChatOpen state + listener + render
  - src/voice-agent/desktop-tauri/src-tauri/src/main.rs — "Open Chat Panel" tray entry
  - docs/superpowers/specs/2026-05-24-tray-chat-panel-design.md — spec (committed earlier)
  - docs/superpowers/plans/2026-05-24-tray-chat-panel.md — this plan

NOT CHANGED:
  - src/cli/ — bridge + CLI agent untouched per CLAUDE.md
  - src/voice-agent/desktop-tauri/src/components/ChatPanel.jsx — existing rich panel left alone
  - Tray indicator (colors / ring / states / poll / icon) — FROZEN per .claude/rules/desktop-tauri.md
  - Voice mode end-to-end audio path — additive publish only
  - Ctrl+H + Ctrl+Shift+Space hotkeys — still open the existing ChatPanel

VERIFY:
  - pytest src/voice-agent/tests/ — full suite passes (incl. 5 new in test_assistant_says_publish.py, 5 new in test_voice_client_events_sse.py)
  - npm run build (src/voice-agent/desktop-tauri) — vite build green
  - cargo build --release (src/voice-agent/desktop-tauri/src-tauri) — Rust build green
  - Manual smoke (Task 8) — round-trip + offline + ChatPanel parity all green
```

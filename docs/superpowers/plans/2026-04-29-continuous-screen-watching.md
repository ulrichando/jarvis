# Continuous Screen Watching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 60s-capped `live_screen` tool invocation with an always-on, silent screen buffer that the model queries on demand via a new `recent_screen()` tool — so JARVIS can see the user's screen for the full duration of a voice session without burning tokens when idle.

**Architecture:** A background `ScreenBuffer` task in the Python voice agent captures the screen every 1.5s into an in-memory rolling deque (last 10 frames). Capture starts when the LiveKit voice session enters active state and stops on disconnect. A new `recent_screen()` `@function_tool` reads frames from the buffer and sends them to the existing `JARVIS_VISION_BACKEND` (Gemini Flash / Ollama qwen2.5vl). LiveKit transport stays audio-only — frames never leave the box until the model calls the tool.

**Tech Stack:** Python 3, livekit-agents 1.5+, scrot (X11), PIL/Pillow, google-genai SDK, pytest. Tray UX in Rust/Tauri (sentinel-file IPC pattern that already exists).

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `src/voice-agent/jarvis_computer_use.py` | Modify | Add `ScreenBuffer` class + `recent_screen()` `@function_tool`. Reuses existing `_take_screenshot()` and `_gemini_describe()`. |
| `src/voice-agent/jarvis_agent.py` | Modify | Start `ScreenBuffer` after `session.start()`, stop on shutdown. Register `recent_screen` in `tools=[]`. Update system prompt. |
| `src/voice-agent/tests/test_computer_use.py` | Modify | Add `TestScreenBuffer` and `TestRecentScreen` test classes. |
| `src/voice-agent/desktop-tauri/src-tauri/src/main.rs` | Modify | Replace "Start Screen Sharing" tray item with a "Watch Screen" toggle that writes/removes a sentinel file. Add eye-state icon. |
| `src/voice-agent/jarvis_agent.py` (lock detection) | Modify | DBus listener on `org.freedesktop.ScreenSaver` → pause buffer on lock. |

No changes to the LiveKit transport, no changes to the existing `live_screen` / `screenshot` / `watch_screen` tools (only the system-prompt language describing them shifts).

---

## Task 1: ScreenBuffer skeleton (TDD)

**Files:**
- Modify: `src/voice-agent/jarvis_computer_use.py` (append new class near the bottom of the screenshot section, before `_gemini_live_describe`)
- Test: `src/voice-agent/tests/test_computer_use.py` (append new test class)

- [ ] **Step 1: Write the failing tests**

Append to `src/voice-agent/tests/test_computer_use.py`:

```python
# ── ScreenBuffer ──────────────────────────────────────────────────────


class TestScreenBuffer:
    """ScreenBuffer captures frames in the background and serves the
    most recent N on demand. Idle cost is RAM only — frames never leave
    the box until recent_screen() sends them to the vision model."""

    def test_initial_state_empty(self):
        import jarvis_computer_use as cu
        buf = cu.ScreenBuffer(interval_s=0.05, max_frames=3)
        assert buf.get_recent(1) == []
        assert buf.is_paused is False
        assert buf.is_running is False

    def test_capture_populates_deque(self):
        import jarvis_computer_use as cu
        with patch("jarvis_computer_use._take_screenshot",
                   return_value=(b"\xff\xd8\xffFAKE", "image/jpeg")):
            buf = cu.ScreenBuffer(interval_s=0.05, max_frames=3)
            run(buf.start())
            run(asyncio.sleep(0.2))  # ~3-4 captures at 0.05s interval
            run(buf.stop())
        frames = buf.get_recent(3)
        assert 1 <= len(frames) <= 3
        for ts, data, mime in frames:
            assert isinstance(ts, float)
            assert data == b"\xff\xd8\xffFAKE"
            assert mime == "image/jpeg"

    def test_deque_respects_max_frames(self):
        import jarvis_computer_use as cu
        with patch("jarvis_computer_use._take_screenshot",
                   return_value=(b"X", "image/jpeg")):
            buf = cu.ScreenBuffer(interval_s=0.01, max_frames=2)
            run(buf.start())
            run(asyncio.sleep(0.15))  # plenty of captures
            run(buf.stop())
        assert len(buf.get_recent(99)) <= 2

    def test_pause_stops_capture_resume_restarts(self):
        import jarvis_computer_use as cu
        calls = []
        def fake_shot():
            calls.append(time.monotonic())
            return (b"X", "image/jpeg")
        with patch("jarvis_computer_use._take_screenshot", side_effect=fake_shot):
            buf = cu.ScreenBuffer(interval_s=0.02, max_frames=10)
            run(buf.start())
            run(asyncio.sleep(0.05))
            buf.pause()
            n_at_pause = len(calls)
            run(asyncio.sleep(0.1))
            assert len(calls) == n_at_pause, "capture continued while paused"
            buf.resume()
            run(asyncio.sleep(0.05))
            run(buf.stop())
        assert len(calls) > n_at_pause

    def test_get_recent_returns_newest_last(self):
        import jarvis_computer_use as cu
        with patch("jarvis_computer_use._take_screenshot",
                   return_value=(b"X", "image/jpeg")):
            buf = cu.ScreenBuffer(interval_s=0.02, max_frames=5)
            run(buf.start())
            run(asyncio.sleep(0.1))
            run(buf.stop())
        frames = buf.get_recent(3)
        timestamps = [ts for ts, _, _ in frames]
        assert timestamps == sorted(timestamps), "frames out of chronological order"

    def test_capture_failure_does_not_kill_loop(self):
        import jarvis_computer_use as cu
        calls = [0]
        def flaky():
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("scrot exploded")
            return (b"X", "image/jpeg")
        with patch("jarvis_computer_use._take_screenshot", side_effect=flaky):
            buf = cu.ScreenBuffer(interval_s=0.02, max_frames=5)
            run(buf.start())
            run(asyncio.sleep(0.1))
            run(buf.stop())
        assert len(buf.get_recent(99)) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/voice-agent && python -m pytest tests/test_computer_use.py::TestScreenBuffer -v`
Expected: 6 failures with `AttributeError: module 'jarvis_computer_use' has no attribute 'ScreenBuffer'`

- [ ] **Step 3: Implement ScreenBuffer**

Add to `src/voice-agent/jarvis_computer_use.py`, just before the `_gemini_live_describe` function (search for `async def _gemini_live_describe` to find the spot):

```python
# ── ScreenBuffer ──────────────────────────────────────────────────────
# Always-on rolling capture for the "JARVIS sees my screen" mode.
# Lifecycle: started on LiveKit voice-session connect, stopped on
# disconnect. Idle cost is RAM only — frames never leave the box until
# the model calls recent_screen(). See docs/superpowers/specs/
# 2026-04-29-continuous-screen-watching-design.md for rationale.

from collections import deque

# Default capture rate: every 1.5s. Matches the prior _live_screen_polling
# default. Slow enough that scrot CPU is negligible (~1-2% on Arch / X11);
# fast enough that "what changed in the last 5 seconds" has 3-4 frames.
_SCREEN_BUFFER_INTERVAL_S = float(
    os.environ.get("JARVIS_SCREEN_BUFFER_INTERVAL_S", "1.5")
)
# Default retention: 10 frames × 1.5s = ~15s of context. ~2 MB RAM at
# our JPEG quality settings.
_SCREEN_BUFFER_MAX_FRAMES = int(
    os.environ.get("JARVIS_SCREEN_BUFFER_MAX_FRAMES", "10")
)


class ScreenBuffer:
    """Background screenshot capturer with a rolling in-memory deque.

    Use:
        buf = ScreenBuffer()
        await buf.start()
        ...
        frames = buf.get_recent(3)   # newest last
        ...
        await buf.stop()

    Captures are isolated in a background asyncio task. Failures in
    individual frames are logged and the loop keeps going — one bad
    scrot call must not kill the buffer.
    """

    def __init__(
        self,
        interval_s: float = _SCREEN_BUFFER_INTERVAL_S,
        max_frames: int = _SCREEN_BUFFER_MAX_FRAMES,
    ) -> None:
        self.interval_s = max(0.01, float(interval_s))
        self.max_frames = max(1, int(max_frames))
        self._deque: deque[tuple[float, bytes, str]] = deque(maxlen=self.max_frames)
        self._task: asyncio.Task | None = None
        self._paused = False
        self._stop_evt = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def is_paused(self) -> bool:
        return self._paused

    async def start(self) -> None:
        if self.is_running:
            return
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._loop(), name="screen-buffer")

    async def stop(self) -> None:
        self._stop_evt.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._deque.clear()

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def get_recent(self, n: int) -> list[tuple[float, bytes, str]]:
        """Return up to `n` most recent (timestamp, jpeg_bytes, mime) tuples,
        oldest-first (newest last)."""
        if n <= 0:
            return []
        # deque is left-old, right-new; slice the rightmost n
        snap = list(self._deque)
        return snap[-n:]

    async def _loop(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._stop_evt.is_set():
            if not self._paused:
                try:
                    img_bytes, mime = await loop.run_in_executor(
                        None, _take_screenshot
                    )
                    self._deque.append((time.monotonic(), img_bytes, mime))
                except Exception as e:
                    logger.warning(f"[screen-buffer] capture failed: {e}")
            try:
                await asyncio.wait_for(
                    self._stop_evt.wait(), timeout=self.interval_s
                )
            except asyncio.TimeoutError:
                pass  # normal — interval elapsed without stop
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd src/voice-agent && python -m pytest tests/test_computer_use.py::TestScreenBuffer -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/jarvis_computer_use.py src/voice-agent/tests/test_computer_use.py
git commit -m "feat(voice-agent): ScreenBuffer — background rolling screenshot capture

In-memory deque (default 10 frames @ 1.5s interval = ~15s context).
Pause/resume support. Capture failures are logged and the loop
survives. No model calls — frames stay local until recent_screen()
ships them. Foundation for the always-on screen-watch mode."
```

---

## Task 2: `recent_screen()` function tool (TDD)

**Files:**
- Modify: `src/voice-agent/jarvis_computer_use.py` (add module-level singleton + the @function_tool, near the existing `live_screen` tool around line 794)
- Test: `src/voice-agent/tests/test_computer_use.py` (append `TestRecentScreen`)

- [ ] **Step 1: Write the failing tests**

Append to `src/voice-agent/tests/test_computer_use.py`:

```python
# ── recent_screen tool ────────────────────────────────────────────────


class TestRecentScreen:
    """recent_screen pulls frames from the global ScreenBuffer singleton
    and asks the vision backend to describe them. No buffer ⇒ a clear
    'eyes are off' message rather than an error."""

    def test_no_buffer_returns_eyes_off_message(self):
        import jarvis_computer_use as cu
        # Ensure singleton is None
        cu._SCREEN_BUFFER = None
        result = run(cu.recent_screen.original_func(n_frames=1))
        assert "screen" in result.lower() or "eyes" in result.lower()
        # Must NOT call the vision backend in this case
        # (no easy assertion — but the test passing without mocks proves it)

    def test_empty_buffer_returns_eyes_off_message(self):
        import jarvis_computer_use as cu
        buf = cu.ScreenBuffer(interval_s=10.0, max_frames=5)  # never fires
        cu._SCREEN_BUFFER = buf
        try:
            result = run(cu.recent_screen.original_func(n_frames=1))
            assert "screen" in result.lower() or "no" in result.lower() or "off" in result.lower()
        finally:
            cu._SCREEN_BUFFER = None

    def test_calls_vision_backend_with_latest_frame(self):
        import jarvis_computer_use as cu
        buf = cu.ScreenBuffer(interval_s=10.0, max_frames=5)
        # Inject a frame manually instead of running the loop
        buf._deque.append((1.0, b"FRAME-A", "image/jpeg"))
        cu._SCREEN_BUFFER = buf
        try:
            with patch(
                "jarvis_computer_use._gemini_describe",
                new=AsyncMock(return_value="A terminal window is visible"),
            ) as mock_describe:
                result = run(cu.recent_screen.original_func(n_frames=1))
            assert "terminal" in result
            assert mock_describe.call_count == 1
            args, kwargs = mock_describe.call_args
            # First positional arg is the image bytes
            assert args[0] == b"FRAME-A"
        finally:
            cu._SCREEN_BUFFER = None

    def test_clamps_n_frames_to_buffer_size(self):
        import jarvis_computer_use as cu
        buf = cu.ScreenBuffer(interval_s=10.0, max_frames=5)
        buf._deque.append((1.0, b"A", "image/jpeg"))
        buf._deque.append((2.0, b"B", "image/jpeg"))
        cu._SCREEN_BUFFER = buf
        try:
            with patch(
                "jarvis_computer_use._gemini_describe",
                new=AsyncMock(return_value="ok"),
            ):
                # Request 99 frames — should not crash
                result = run(cu.recent_screen.original_func(n_frames=99))
            assert isinstance(result, str)
        finally:
            cu._SCREEN_BUFFER = None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/voice-agent && python -m pytest tests/test_computer_use.py::TestRecentScreen -v`
Expected: 4 failures with `AttributeError: module 'jarvis_computer_use' has no attribute 'recent_screen'` (and `_SCREEN_BUFFER`).

- [ ] **Step 3: Implement the singleton + tool**

In `src/voice-agent/jarvis_computer_use.py`, add right after the `ScreenBuffer` class:

```python
# Module-level singleton. jarvis_agent.py owns lifecycle (start on
# session connect, stop on disconnect). recent_screen() reads from it.
_SCREEN_BUFFER: ScreenBuffer | None = None


def get_screen_buffer() -> ScreenBuffer | None:
    """Return the active ScreenBuffer or None when watching is off."""
    return _SCREEN_BUFFER


def set_screen_buffer(buf: ScreenBuffer | None) -> None:
    """Owned by jarvis_agent.py. Called once on session start, again
    with None on shutdown."""
    global _SCREEN_BUFFER
    _SCREEN_BUFFER = buf
```

Then add the tool itself, just after the existing `live_screen` definition (search for `async def live_screen` — add the new tool below the closing of that function):

```python
@function_tool
async def recent_screen(n_frames: int = 1, focus: str = "") -> str:
    """Look at what's recently been on the user's screen.

    JARVIS continuously buffers screenshots in the background while a
    voice session is active. This tool returns a description of the
    most recent frame(s).

    Use this whenever the user:
      - asks about their screen ("what am I looking at", "what's this")
      - references something visible ("this code", "this error", "here")
      - asks for help with something they're doing
      - asks a question that's ambiguous without screen context

    Do NOT call this for unrelated questions ("what time is it",
    "play music"). It costs a vision-model call.

    For an explicit "narrate what's happening for N seconds" request,
    use live_screen() instead.

    Args:
        n_frames: How many recent frames to consider (1..3, default 1).
                  Use 1 for "what's on screen now". Use 2-3 for
                  "what changed".
        focus:    Optional short hint to the vision model — e.g.
                  "the error in the terminal" or "the Figma file".
    """
    n_frames = max(1, min(int(n_frames), 3))
    buf = get_screen_buffer()
    if buf is None or not buf.is_running:
        return "(screen-watch is off — eyes are closed right now)"
    if buf.is_paused:
        return "(screen-watch is paused for privacy)"
    frames = buf.get_recent(n_frames)
    if not frames:
        return "(buffer is empty — try again in a moment, or check "
        "that scrot/X11 is working)"

    # Build the prompt. For 1 frame use the standard describe prompt;
    # for multiple frames, ask about change.
    if len(frames) == 1:
        prompt = (
            f"What's on the user's screen right now? Be concise. "
            f"{('Focus: ' + focus) if focus else ''}"
        )
        _, img_bytes, mime = frames[-1]
    else:
        prompt = (
            f"These are {len(frames)} screenshots taken ~"
            f"{int(buf.interval_s)}s apart, oldest first. Describe "
            f"what's on screen and what changed. Be concise. "
            f"{('Focus: ' + focus) if focus else ''}"
        )
        # For multi-frame, describe the latest only (vision backends
        # in this codebase take a single image). The "what changed"
        # capability is a v2 — for v1, latest frame + a hint that
        # several were taken is enough context for most questions.
        _, img_bytes, mime = frames[-1]

    try:
        desc = await _gemini_describe(img_bytes, mime_type=mime, prompt=prompt)
        logger.info(
            f"[computer-use] recent_screen(n={n_frames}, focus={focus!r}) "
            f"→ {len(desc)} chars"
        )
        return desc.strip() or "(vision model returned empty response)"
    except Exception as e:
        logger.warning(f"[computer-use] recent_screen failed: {e}")
        return f"(recent_screen failed: {e})"
```

Note: `function_tool` from livekit-agents wraps the async function. The tests use `recent_screen.original_func(...)` to call past the wrapper — verify this attribute exists by checking how existing tests invoke `live_screen`. If the wrapper exposes a different attribute, adjust the tests accordingly.

- [ ] **Step 4: Verify wrapper attribute name**

Run: `cd src/voice-agent && python -c "from jarvis_computer_use import live_screen; print([a for a in dir(live_screen) if not a.startswith('_')][:20])"`

If `original_func` is wrong, find the right name (likely `func`, `fn`, or `__wrapped__`) and update the tests in Step 1 before proceeding.

- [ ] **Step 5: Run tests to verify pass**

Run: `cd src/voice-agent && python -m pytest tests/test_computer_use.py::TestRecentScreen -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/jarvis_computer_use.py src/voice-agent/tests/test_computer_use.py
git commit -m "feat(voice-agent): recent_screen() — model-decides screen lookup

Reads from the ScreenBuffer singleton, sends the latest frame to
the configured vision backend (JARVIS_VISION_BACKEND). Returns a
clear off/paused/empty message instead of crashing when the buffer
isn't ready. No 60s cap — this is instantaneous because the frames
are already in memory."
```

---

## Task 3: Wire ScreenBuffer to the LiveKit session lifecycle

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` — three spots:
  1. Imports near line 83 — add `recent_screen`, `ScreenBuffer`, `set_screen_buffer`
  2. `tools=[]` list around line 3510 — register `recent_screen`
  3. Inside `entrypoint(ctx)` after `session.start()` — start the buffer; register a shutdown callback to stop it

- [ ] **Step 1: Update imports**

Find the import block (around line 80-90) that imports `screenshot, live_screen` from `jarvis_computer_use`. It looks like:

```python
from jarvis_computer_use import (
    ...
    screenshot,
    live_screen,
    ...
)
```

Change it to also import:

```python
from jarvis_computer_use import (
    ...
    screenshot,
    live_screen,
    recent_screen,
    ScreenBuffer,
    set_screen_buffer,
    ...
)
```

- [ ] **Step 2: Register the tool in tools=[]**

In the `tools=[...]` list around line 3510-3543, add `recent_screen` right after `live_screen`:

```python
            tools=[
                ...
                screenshot,
                live_screen,
                recent_screen,    # ← add this line
                webcam_capture,
                ...
            ],
```

- [ ] **Step 3: Start the buffer after session.start()**

In `entrypoint(ctx)`, find where `session.start(...)` is awaited (the call wrapping the `tools=[...]` we just edited). After that `await session.start(...)` returns, add:

```python
    # ── Screen buffer (always-on while voice session is active) ──────
    # Captures the screen every ~1.5s into a small in-memory deque.
    # The model queries it via recent_screen() when context warrants.
    # Idle cost is RAM only; frames never leave the box until the model
    # explicitly asks. See docs/superpowers/specs/2026-04-29-continuous
    # -screen-watching-design.md.
    screen_buffer = ScreenBuffer()
    await screen_buffer.start()
    set_screen_buffer(screen_buffer)
    logger.info("[screen-buffer] started — JARVIS's eyes are open")

    async def _stop_screen_buffer() -> None:
        try:
            set_screen_buffer(None)
            await screen_buffer.stop()
            logger.info("[screen-buffer] stopped")
        except Exception as e:
            logger.warning(f"[screen-buffer] stop error: {e}")

    ctx.add_shutdown_callback(_stop_screen_buffer)
```

The exact name `ctx.add_shutdown_callback` matches the LiveKit `JobContext` API. If the surrounding code uses a different cleanup hook, follow the existing pattern (e.g., `_run_analyzer_bg` is fire-and-forget; the shutdown for ours needs to be deterministic to ensure the deque clears).

- [ ] **Step 4: Hand-test the lifecycle**

This step has no automated test — we're integrating with LiveKit's runtime. Instead, verify by running:

```bash
cd src/voice-agent
python -c "
import asyncio
import jarvis_computer_use as cu

async def main():
    buf = cu.ScreenBuffer(interval_s=0.5, max_frames=3)
    cu.set_screen_buffer(buf)
    await buf.start()
    print('running:', buf.is_running)
    await asyncio.sleep(2)
    print('frames:', len(buf.get_recent(99)))
    cu.set_screen_buffer(None)
    await buf.stop()
    print('after stop:', buf.is_running, len(buf.get_recent(99)))

asyncio.run(main())
"
```

Expected output:
```
running: True
frames: 3
after stop: False 0
```

If frames is 0, scrot likely failed — check `which scrot` and that `$DISPLAY` is set.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "feat(voice-agent): start ScreenBuffer per voice session

Buffer starts after session.start() and stops on shutdown via
ctx.add_shutdown_callback. recent_screen() registered alongside
the existing screenshot/live_screen tools. No cap on session
duration — buffer runs as long as the voice session does."
```

---

## Task 4: System-prompt update — when to call `recent_screen` vs others

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` — the system-prompt block (search for the `screenshot()` doc text around line 863-895 and the `D1.` style entries)

- [ ] **Step 1: Locate the existing tool-doc block**

Run: `grep -n "D1\." /home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py | head -5`

Then read ~30 lines around the `D1.` entry to find the structure. The block lists `D1` `screenshot`, then likely other Dn tools.

- [ ] **Step 2: Add a new entry for `recent_screen` and reposition `live_screen`**

Add a new `Dn` block describing `recent_screen` (insert right after D1 `screenshot`), and update the `live_screen` entry (find it — it's already in the prompt) so it's positioned for the explicit-narration use case only.

Replace the existing `screenshot`-section guidance with:

```
D1. `screenshot()` — one-shot: take ONE frame right now, describe
    it, return. Use ONLY when the screen-watch buffer is off (e.g.,
    the user explicitly asked JARVIS to stop watching).

D2. `recent_screen(n_frames=1, focus="")` — primary screen lookup.
    The buffer is always rolling while we're in a voice session, so
    this tool is essentially free latency-wise. Call it whenever:
      - "what's on my screen"            → recent_screen()
      - "what am I looking at"           → recent_screen()
      - "help me with this code/error"   → recent_screen(focus="the error")
      - "this isn't working"             → recent_screen()
      - "what changed"                   → recent_screen(n_frames=3)
    DO NOT call it for unrelated questions ("what time is it").

D3. `live_screen(duration_s)` — explicit narration mode. Use ONLY
    when the user says "narrate what I'm doing for the next X
    seconds" or "watch me for a minute and tell me what you see".
    For everything else, prefer recent_screen().
```

(Find and remove the *old* numbered references to make room. Keep the existing `D3, D4, D5...` for face_register etc. — just renumber as needed so the list stays sequential.)

- [ ] **Step 3: Hand-verify by reading the rendered prompt**

Run: `grep -A 30 "D1\." /home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py | head -40`

Visually confirm: D1 = screenshot (off-only), D2 = recent_screen (primary), D3 = live_screen (narration only). All other Dn entries should still flow correctly.

- [ ] **Step 4: Commit**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "feat(voice-agent): system prompt routes screen questions to recent_screen

recent_screen is the new default for 'what's on my screen' / 'help me
with this' — buffer is already rolling so it's instant. live_screen
narrows to explicit narration ('watch me for 30s'). screenshot
becomes the off-buffer fallback."
```

---

## Task 5: Tray "Watch Screen" toggle (Tauri/Rust)

> **Note:** This task touches `src/voice-agent/desktop-tauri/`. Per the user's project conventions, after editing JS/Rust under that path, the Tauri binary needs `cargo build --release` for changes to ship in the installed app. (See [project_tauri_release_rebuild memory](memory/project_tauri_release_rebuild.md).)

**Files:**
- Modify: `src/voice-agent/desktop-tauri/src-tauri/src/main.rs`

- [ ] **Step 1: Find the existing "Start Screen Sharing" tray code**

Run: `grep -n "start-screen-share\|Start Screen Sharing" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/desktop-tauri/src-tauri/src/main.rs`

Read 30 lines around each match to understand the existing menu-item registration and the file-write pattern (`~/.jarvis/start-screen-share`).

- [ ] **Step 2: Add a parallel toggle for the new buffer**

Add a *new* tray item "Watch Screen" with a checkable state. On click:
- If currently OFF: write empty file `~/.jarvis/screen-watch-on`. Update menu item label to "Stop Watching Screen". Update icon to the eye-overlay variant (you'll add this asset next).
- If currently ON: delete `~/.jarvis/screen-watch-on`. Restore the original tray icon. Restore label to "Watch Screen".

(Leave the existing "Start Screen Sharing" menu item intact for the explicit-narration use case — they're different features now.)

Pseudocode-level Rust outline (adapt to the codebase's actual menu-builder pattern):

```rust
// near the existing start-screen-share menu item
let watch_screen_path = home_dir().unwrap().join(".jarvis").join("screen-watch-on");

let watch_item = CustomMenuItem::new("watch_screen", "Watch Screen");
// add to menu...

// in the menu-event handler:
"watch_screen" => {
    if watch_screen_path.exists() {
        let _ = fs::remove_file(&watch_screen_path);
        item_handle.set_title("Watch Screen").ok();
        // restore default icon
    } else {
        let _ = fs::create_dir_all(watch_screen_path.parent().unwrap());
        let _ = fs::write(&watch_screen_path, "");
        item_handle.set_title("Stop Watching Screen").ok();
        // set eye-overlay icon
    }
}
```

- [ ] **Step 3: Add the eye-overlay tray icon asset**

Reuse the existing `tray.png` as a base and produce `tray-watching.png`. Quickest path with ImageMagick:

```bash
cd src/voice-agent/desktop-tauri/src-tauri/icons
convert tray.png -gravity center -fill '#22c55e' -draw "circle 200,200 220,220" tray-watching.png
```

(Adjust coordinates for the actual icon size — `identify tray.png` first.) Goal: an unmistakable visible difference at 16-22px tray sizes.

Register the new icon in `tauri.conf.json`'s `tauri.bundle.icon` array and reference it from main.rs when toggling.

- [ ] **Step 4: Wire the toggle file to ScreenBuffer pause/resume**

In `src/voice-agent/jarvis_agent.py`, add a watcher loop next to the existing `_watch_screen_share` task. It polls `~/.jarvis/screen-watch-on` once per second:
- If file exists and `screen_buffer.is_paused`: call `screen_buffer.resume()`.
- If file does NOT exist and not paused: call `screen_buffer.pause()`.

```python
    # ── Tray watch-screen toggle ─────────────────────────────────────
    # The tray's "Watch Screen" item writes/removes ~/.jarvis/screen-
    # watch-on. When absent, the buffer pauses (no captures, no RAM
    # churn) but the singleton stays alive so recent_screen can still
    # report "watching is paused" cleanly.
    WATCH_TOGGLE = Path.home() / ".jarvis" / "screen-watch-on"
    async def _watch_screen_toggle() -> None:
        # Default state: buffer paused until the user opts in via tray.
        # If you want the opposite (default on), invert this line.
        if not WATCH_TOGGLE.exists():
            screen_buffer.pause()
        while True:
            try:
                await asyncio.sleep(1.0)
                want_on = WATCH_TOGGLE.exists()
                if want_on and screen_buffer.is_paused:
                    screen_buffer.resume()
                    logger.info("[screen-buffer] resumed (tray toggle)")
                elif not want_on and not screen_buffer.is_paused:
                    screen_buffer.pause()
                    logger.info("[screen-buffer] paused (tray toggle)")
            except Exception as e:
                logger.warning(f"[screen-buffer] toggle watcher error: {e}")

    asyncio.create_task(_watch_screen_toggle())
```

Decide before merging: **default on or off?**
- Default ON (toggle file absent ⇒ buffer paused) is privacy-conservative.
- Default OFF (always running unless tray item explicitly stops it) matches the spec's "always on while voice session is open."

Per the spec, default is **always on**. Remove the `if not WATCH_TOGGLE.exists(): screen_buffer.pause()` line and treat the tray as a *pause* control, not an opt-in. (User can still explicitly pause via tray.)

- [ ] **Step 5: Build the Tauri binary**

```bash
cd src/voice-agent/desktop-tauri
npm run build
cargo tauri build --release
```

Or, if the project uses `bun`/`pnpm`, swap accordingly. The release rebuild is required because `npm run build` alone doesn't re-embed the JS bundle into the Rust binary.

- [ ] **Step 6: Hand-test**

Restart the voice agent and the desktop app. Verify:
- Tray shows "Watch Screen" item.
- Clicking it toggles the eye-overlay icon.
- `~/.jarvis/screen-watch-on` appears/disappears.
- Voice agent log shows "[screen-buffer] paused/resumed (tray toggle)".

- [ ] **Step 7: Commit**

```bash
git add src/voice-agent/desktop-tauri/src-tauri/src/main.rs \
        src/voice-agent/desktop-tauri/src-tauri/icons/tray-watching.png \
        src/voice-agent/desktop-tauri/src-tauri/tauri.conf.json \
        src/voice-agent/jarvis_agent.py
git commit -m "feat(tray): Watch Screen toggle pauses/resumes ScreenBuffer

Tray item writes/removes ~/.jarvis/screen-watch-on; voice agent
polls and pauses/resumes the buffer accordingly. Eye-overlay icon
makes 'JARVIS is watching' visible at all times. Default is always-
on once a voice session starts; the toggle is a pause control."
```

---

## Task 6: Auto-pause on screen lock (Linux)

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` — add a DBus listener task next to `_watch_screen_toggle`

- [ ] **Step 1: Add the DBus listener task**

Linux desktops (GNOME, KDE, Hyprland with screensaver compatibility) emit `ActiveChanged(bool)` on the `org.freedesktop.ScreenSaver` interface. Use `dbus-next` (already a common dep — check `requirements.txt` first; if missing, add it).

```bash
grep -i "dbus-next\|jeepney\|dbus" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/requirements.txt
```

If neither is present, add `dbus-next>=0.2.3` to `requirements.txt` and `pip install -r requirements.txt`.

Then add to `entrypoint(ctx)` (next to the toggle watcher):

```python
    # ── Auto-pause on screen lock ────────────────────────────────────
    async def _watch_lock_state() -> None:
        try:
            from dbus_next.aio import MessageBus
            from dbus_next import BusType
            bus = await MessageBus(bus_type=BusType.SESSION).connect()
            introspection = await bus.introspect(
                "org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver"
            )
            obj = bus.get_proxy_object(
                "org.freedesktop.ScreenSaver",
                "/org/freedesktop/ScreenSaver",
                introspection,
            )
            iface = obj.get_interface("org.freedesktop.ScreenSaver")

            def _on_active_changed(active: bool) -> None:
                if active:
                    screen_buffer.pause()
                    logger.info("[screen-buffer] paused (screen locked)")
                else:
                    screen_buffer.resume()
                    logger.info("[screen-buffer] resumed (screen unlocked)")

            iface.on_active_changed(_on_active_changed)
            # Keep the task alive forever — DBus signals are async
            await asyncio.Event().wait()
        except Exception as e:
            logger.warning(
                f"[screen-buffer] DBus screen-lock listener disabled: {e}"
            )

    asyncio.create_task(_watch_lock_state())
```

If DBus isn't available on the user's setup the listener simply logs and exits — the buffer just keeps running through lock/unlock, which is suboptimal but not broken. The tray toggle remains as the user-facing fallback.

- [ ] **Step 2: Hand-test**

Lock the screen (e.g., `Super+L` or `loginctl lock-session`) and verify the agent log shows `[screen-buffer] paused (screen locked)`. Unlock — verify resume.

If no log appears, the user's desktop may not implement the `org.freedesktop.ScreenSaver` interface (Hyprland doesn't by default). In that case, document the limitation in the spec's "Risks" section and consider adding a Hyprland-specific path (`hyprlock` IPC) as a follow-up — out of scope here.

- [ ] **Step 3: Commit**

```bash
git add src/voice-agent/jarvis_agent.py src/voice-agent/requirements.txt
git commit -m "feat(screen-buffer): auto-pause on screen lock via DBus

Listens to org.freedesktop.ScreenSaver.ActiveChanged. Pauses the
buffer when the screen locks, resumes on unlock. Soft-fails when
DBus or the interface is unavailable — buffer keeps running, tray
toggle remains the user-facing fallback."
```

---

## Task 7: Privacy hotkey `Super+Shift+P`

**Files:**
- Modify: `src/voice-agent/desktop-tauri/src-tauri/src/main.rs` — register a global shortcut that toggles the same `~/.jarvis/screen-watch-on` file used by the tray.

- [ ] **Step 1: Find existing global-shortcut registrations in main.rs**

Run: `grep -n "global_shortcut\|globalShortcut\|register_global_shortcut" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/desktop-tauri/src-tauri/src/main.rs`

If shortcuts are already registered, follow the same pattern. If not, add the `tauri-plugin-global-shortcut` plugin (Tauri 2) — check `tauri.conf.json` and `Cargo.toml` first.

- [ ] **Step 2: Register `Super+Shift+P` to toggle the watch file**

```rust
use tauri_plugin_global_shortcut::{Code, Modifiers, Shortcut, ShortcutState};

// In setup():
app.handle().plugin(
    tauri_plugin_global_shortcut::Builder::new()
        .with_handler(|app, shortcut, event| {
            if event.state() == ShortcutState::Pressed {
                if shortcut.matches(Modifiers::SUPER | Modifiers::SHIFT, Code::KeyP) {
                    let path = home_dir().unwrap().join(".jarvis").join("screen-watch-on");
                    if path.exists() {
                        let _ = std::fs::remove_file(&path);
                    } else {
                        let _ = std::fs::create_dir_all(path.parent().unwrap());
                        let _ = std::fs::write(&path, "");
                    }
                }
            }
        })
        .build(),
)?;
app.global_shortcut().register(
    Shortcut::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::KeyP),
)?;
```

The voice agent's existing toggle watcher (Task 5 step 4) handles the actual pause/resume — the hotkey just flips the file.

- [ ] **Step 3: Rebuild + hand-test**

```bash
cd src/voice-agent/desktop-tauri
cargo tauri build --release
```

Restart the desktop app. Press `Super+Shift+P` while a voice session is active. Verify:
- Tray icon switches between eye-overlay and default
- Voice agent log shows pause/resume
- Pressing again toggles back

- [ ] **Step 4: Commit**

```bash
git add src/voice-agent/desktop-tauri/src-tauri/src/main.rs \
        src/voice-agent/desktop-tauri/src-tauri/Cargo.toml \
        src/voice-agent/desktop-tauri/src-tauri/tauri.conf.json
git commit -m "feat(tray): Super+Shift+P privacy hotkey toggles screen watching

Registers a global shortcut via tauri-plugin-global-shortcut. Flips
~/.jarvis/screen-watch-on; the voice agent's existing watcher does
the actual pause/resume. One key for 'eyes off right now'."
```

---

## Task 8: End-to-end smoke test

**Files:** none — this is a manual verification pass before declaring the feature complete.

- [ ] **Step 1: Start a voice session**

```bash
cd src/voice-agent
# whatever your usual startup is — likely: jarvis  (now with clear)
```

Watch the logs for:
- `[screen-buffer] started — JARVIS's eyes are open`
- No DBus / scrot errors

- [ ] **Step 2: Verify "always on" silence**

Don't say anything for 60 seconds. Watch logs.

Expected:
- No `recent_screen` calls in the log
- No vision-API calls
- The buffer keeps capturing (the `[screen-buffer]` task should not be quiet — but its captures are local, not logged at INFO)

If you see vision-API calls without speaking, the model is over-eager. Tighten the system prompt language in Task 4.

- [ ] **Step 3: Verify "ask works"**

Open a colorful page or a known terminal output. Say: "Jarvis, what's on my screen?"

Expected:
- Log shows `[computer-use] recent_screen(n=1, focus='') → N chars`
- JARVIS describes what's on screen out loud
- Latency from question to response is comparable to a one-shot `screenshot()` call (no extra capture step — frame was already in memory)

- [ ] **Step 4: Verify the tray toggle pauses and resumes**

Click "Stop Watching Screen" in the tray. Log: `[screen-buffer] paused (tray toggle)`.

Ask: "What's on my screen?"

Expected: JARVIS responds with something like "screen-watch is paused for privacy" rather than describing the screen.

Click "Watch Screen" in the tray. Ask again. Should describe normally.

- [ ] **Step 5: Verify the hotkey works**

Press `Super+Shift+P` while watching is on. Tray icon should change. Press again — back.

- [ ] **Step 6: Verify session-close cleanup**

Disconnect the voice client. In the agent log, look for: `[screen-buffer] stopped`.

Confirm: `python -c "import jarvis_computer_use as cu; print(cu.get_screen_buffer())"` returns `None`.

- [ ] **Step 7: Commit a follow-up note (optional)**

If the smoke test surfaces any rough edges (over-calling, latency on ollama backend, DBus on Hyprland), add an entry to the spec's "Risks & open considerations" section and commit:

```bash
git add docs/superpowers/specs/2026-04-29-continuous-screen-watching-design.md
git commit -m "docs: smoke-test findings for continuous screen watching"
```

---

## Self-Review Notes

**Spec coverage:** Each spec section maps to tasks:
- Architecture / ScreenBuffer → Task 1
- recent_screen tool → Task 2
- Lifecycle integration → Task 3
- System prompt update → Task 4
- Tray rewire + privacy hotkey → Tasks 5, 7
- Auto-pause on lock → Task 6
- Vision provider policy → already handled (reuses `_gemini_describe` which honors `JARVIS_VISION_BACKEND`)
- Repositioning live_screen → Task 4 (system prompt)

**Type consistency check:**
- `ScreenBuffer.start()` / `.stop()` are async, called with `await` consistently.
- `set_screen_buffer(buf | None)` typed and called consistently.
- `get_recent(n) → list[(float, bytes, str)]` consistent across class and tool.
- `recent_screen(n_frames, focus)` signature matches the spec and the Task 2 docstring.

**Out-of-scope items the spec called out and this plan honors:** no LiveKit video transport, no multi-monitor (only primary), no on-disk recording, no web-client screen sharing.

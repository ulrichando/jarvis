# Direct modes — non-blocking tool execution (stop tasks from killing the Live session)

**Date:** 2026-05-30
**Status:** proposed (design) — awaiting review
**Scope:** `bin/jarvis-gemini-tools` and `bin/jarvis-gpt-tools` (same bug in both `execute_tool` +
`drain_replies` tool paths).

## Problem (confirmed live 2026-05-30)

In the direct voice modes, EVERY substantial task drops the Live/Realtime session, so the result
never returns and JARVIS "stops responding". Two mechanisms, both in the tool-handling path:

1. **Sync handler blocks the event loop.** `handle_computer_use` is a *synchronous* function
   (`tools/computer_use.py:352`, registered as a sync lambda). `execute_tool` calls it inline as
   `result = handler(args)` — so it runs ON the event loop and blocks it for the tool's whole
   duration. A blocked loop can't service the WebSocket keepalive → `1011 keepalive ping timeout`.
2. **Long async tool parks the receive loop.** `_handle_browser_task` is async but long (~72 s
   observed). It runs inline inside `drain_replies`'s `async for msg in session.receive():` loop,
   so the receive iteration is parked for the tool's duration; the Live keepalive (serviced by
   reading) isn't serviced → `1011`. Observed: drop landed exactly 72 s into a `browser_task`.

Either way the session dies *mid-task*, the `tool_response` is never sent, and (with the new
reconnect) a fresh session starts with no memory of the task → the user perceives "no response".
Rapid task-drops also trip the reconnect storm-cap → revert-to-Claude.

**The reference fix already exists in-tree:** `tools/_adapter.py:143-158` (how the LiveKit
voice-agent invokes the same tools) does it correctly — `await handler(args)` for async handlers,
`await asyncio.to_thread(handler, args)` for sync handlers. The direct-mode `execute_tool` simply
never mirrored that. So sync tools that work fine under the voice-agent block the loop here.

## Goal

A tool call — however long — must NOT stall the Live keepalive or park the receive loop, so the
session stays alive and the `tool_response` is delivered on the SAME session. No behavior change
to tool semantics; only how/where they run.

## Design

### 1. `execute_tool` — never run a handler on the event loop
Mirror `_adapter`: async handlers are awaited; sync handlers run in a worker thread.
```python
handler = entry.handler
if inspect.iscoroutinefunction(handler):
    result = await handler(args or {})
else:
    result = await asyncio.to_thread(handler, args or {})   # sync tool off the loop
    if inspect.isawaitable(result):                          # sync-def returning a coroutine (rare)
        result = await result
```
(Keeps the existing error capture + size-truncation that follow.)

### 2. `drain_replies` — run the tool OFF the receive loop
Today the tool batch is awaited inline, parking `session.receive()`. Instead, spawn a runner task
and `continue` the receive loop so it keeps pumping (services the Live keepalive):
```python
if tool_call is not None and getattr(tool_call, "function_calls", None):
    asyncio.create_task(_run_tool_batch(session, list(tool_call.function_calls)),
                        name="tool-batch")
    continue
```
`_run_tool_batch` (a nested coro) does what the inline block did — run each tool (via the fixed
`execute_tool`), build `FunctionResponse`s, `await session.send_tool_response(...)` — plus:
- bump `last_activity` at start (so idle-revert doesn't fire mid-task — same as today),
- increment/decrement a `tool_running` COUNTER (below) instead of a bool,
- guard the `send_tool_response` in try/except: if the session dropped while the tool ran, log and
  drop the response (the reconnect path handles the session; a stale response can't be sent).

### 3. `tool_running` as a counter, not a bool
With tools now possibly overlapping, a single bool would clear too early. Track an int
`_tool_inflight` (or reuse a counter on `status`): increment when a batch starts, decrement in a
`finally`; `status.set_tool_running(_tool_inflight > 0)`. The idle-revert `is_tool_running` guard
then stays correct while any tool is in flight.

### 4. Apply the identical change to `bin/jarvis-gpt-tools`
Its `execute_tool` (line ~337) and `drain_replies` `response.function_call_arguments.done` handler
have the same inline-await structure. Same two fixes.

## Why this is safe
- `to_thread` for sync tools is exactly what the voice-agent already does for these same handlers
  (`_adapter.py:153`), so they are already exercised off-loop in production — no new thread-safety
  exposure. `_handle_browser_task` already offloads its blocking provider calls via `to_thread`
  internally (`tools/browser.py:316`).
- Spawning the tool batch as a task is the standard concurrent-tool pattern; the receive loop and
  the send are independent on the same session.

## Testability
- The pure-ish bit is small. Add a focused test that a SYNC handler registered in a tiny fake
  registry is invoked via `to_thread` (i.e. does not block) — or, more practically, a unit test of
  the counter logic + a static assertion that `execute_tool` no longer calls `handler(args)`
  directly on the loop. The decisive proof is LIVE: a long tool (`browser_task` / a sleep-y stub)
  runs to completion with the session staying up (NRestarts unchanged, no `1011`, the tool_response
  delivered) — run at a safe moment (gemini currently reverted to Claude, so the window is free).

## Risks / non-goals
- **Out-of-order / overlapping tool responses**: Gemini/OpenAI accept tool responses by call id, so
  completing out of order is fine; the counter prevents premature `tool_running=False`.
- **Session drops mid-tool anyway** (network, not loop-stall): the guarded send drops the stale
  response; the reconnect starts a fresh session. Same as today, minus the self-inflicted drops.
- **Not** changing tool semantics, the reconnect loop, or `Restart=always`.

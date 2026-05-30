# Direct-mode non-blocking tool execution — Implementation Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Spec:
> `docs/superpowers/specs/2026-05-30-direct-mode-nonblocking-tools-design.md` (has the exact code).

**Goal:** A long/sync tool call must not block the event loop or park the receive loop, so the
Live/Realtime session survives the task and the `tool_response` is delivered. Mirrors the proven
`tools/_adapter.py:143-158` pattern. Fixes "every task → loses connection" (1011 keepalive timeout).

**Files:** Modify `bin/jarvis-gemini-tools`, then `bin/jarvis-gpt-tools`. No new files.
**OUT:** the reconnect loop, `jarvis-mode`, `evolution/`, the tools themselves, voice-agent runtime.

---

### Task 1: `bin/jarvis-gemini-tools` — non-blocking tools

Read the file first. Apply the spec's three changes:

- [ ] **1a. `execute_tool` (≈line 350):** replace the `result = handler(args or {}); if inspect.isawaitable(result): result = await result` block with the spec §1 branch: `iscoroutinefunction(handler)` → `await handler(...)`; else → `await asyncio.to_thread(handler, args or {})` (then await if it returned a coroutine). Keep the surrounding try/except + size-truncation.
- [ ] **1b. Add a `tool_inflight = [0]` counter cell** at main scope next to `last_activity = [...]` (≈line 483), and define a nested `async def _run_tool_batch(session, function_calls)` next to the pump defs (≈after `drain_replies`). It does what the old inline tool block did (per-fc `execute_tool`, build `FunctionResponse`s, `await session.send_tool_response(...)`), PLUS: increments `tool_inflight[0]` + `status.set_tool_running(True)` at start; bumps `last_activity[0]` at start and per fc; wraps the `send_tool_response` in try/except (log "tool_response send failed (session dropped?)" on failure); and in a `finally` decrements `tool_inflight[0]` and sets `status.set_tool_running(tool_inflight[0] > 0)`.
- [ ] **1c. `drain_replies` tool branch (≈line 530):** replace the inline `set_tool_running(True)` + for-loop + `send_tool_response` + `set_tool_running(False)` with: `last_activity[0] = loop.time(); asyncio.create_task(_run_tool_batch(session, list(tool_call.function_calls)), name="tool-batch"); continue`. The receive loop must NOT await the tool.
- [ ] **1d.** `src/voice-agent/.venv/bin/python -m py_compile bin/jarvis-gemini-tools` → clean.
- [ ] **1e.** `grep -n 'to_thread\|_run_tool_batch\|create_task(_run_tool_batch\|tool_inflight' bin/jarvis-gemini-tools` → confirm: execute_tool uses to_thread for sync; drain_replies spawns the batch (does NOT `await execute_tool` inline anymore); counter present.
- [ ] **1f.** Commit: `fix(gemini-tools): run tools off the receive loop + sync tools in a thread (stop 1011 on long tasks)`

---

### Task 2: `bin/jarvis-gpt-tools` — same fix

Read the file. Its `execute_tool` (≈line 337) and the `response.function_call_arguments.done`
handler in `drain_replies` have the same inline structure (OpenAI uses
`conversation.item.create` of type `function_call_output` then `response.create` instead of
`send_tool_response` — preserve that send shape, just move it off the receive loop).

- [ ] **2a.** `execute_tool` → same `iscoroutinefunction` / `to_thread` branch as Task 1a.
- [ ] **2b.** Add the `tool_inflight` counter + a `_run_tool_batch`-equivalent that runs the tool(s) and sends the OpenAI function_call_output + `response.create` off the receive loop, with the guarded send + counter `finally`.
- [ ] **2c.** The `drain_replies` `function_call_arguments.done` branch spawns the runner task + does not await the tool inline. Preserve the existing `set_tool_running` / `last_activity` bumps via the counter.
- [ ] **2d.** `py_compile bin/jarvis-gpt-tools` → clean. **2e.** grep confirms to_thread + spawned runner + counter. **2f.** Commit: `fix(gpt-tools): run tools off the receive loop + sync tools in a thread (stop realtime drops on long tasks)`

---

### Task 3: Verify

- [ ] **3.1** `py_compile` both bins clean.
- [ ] **3.2** `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` → full suite still green (these are bin scripts not imported by the suite, but confirm nothing regressed).
- [ ] **3.3** Static confirm in BOTH files: `execute_tool` no longer calls `handler(args)` directly on the loop (uses `to_thread` for non-coroutine handlers); the tool batch is spawned (not awaited) in the receive loop; `tool_running` driven by the inflight counter.
- [ ] **3.4 LIVE test (coordinator, gemini currently free in Claude mode):** start gemini onto the
  new code; ask it (or stub) a LONG tool (`browser_task` ~30-70s). Confirm the session STAYS UP for
  the tool's whole duration — no `1011`, `NRestarts` unchanged, same PID, and the tool_response is
  delivered (JARVIS speaks the result). This is the decisive proof; the old code dropped at ~72s.

**Acceptance:** py_compile clean both; full suite green; static checks pass in both files; the live
long-tool test completes with the session intact (no 1011, NRestarts unchanged) and the result spoken.

# Plan 001 — Fix computer-use sidecar history trim splitting Anthropic tool_use/tool_result pairs

**Written against commit:** `8bede503`
**Category:** Correctness / bug
**Effort:** S (a few hours incl. test)
**Risk:** LOW — one helper, additive guard, covered by a new regression test
**Confidence:** MED-HIGH (logic is clear; not yet reproduced live — the test in this plan reproduces it)

---

## Why this matters

The web `/computer-use` sidecar (`src/voice-agent/computer_use_service.py`) keeps a
per-session conversation history so follow-up messages continue in context. Between
`/run` calls it persists an **image-free** copy of the adapter's message list, trimmed
to the last `_MAX_HISTORY = 40` messages by `_trim_history`.

For the **Anthropic** provider, tool results are carried in **`user`** messages
(`{"role": "user", "content": [{"type": "tool_result", ...}]}` — see
`pipeline/cu_adapters/anthropic_adapter.py::add_results`). `_trim_history` advances the
cut point to the first `role == "user"` message:

```python
# computer_use_service.py
def _trim_history(messages):
    if len(messages) <= _MAX_HISTORY:
        return messages
    start = len(messages) - _MAX_HISTORY
    while start < len(messages) and messages[start].get("role") != "user":
        start += 1
    return messages[start:] if start < len(messages) else messages[-_MAX_HISTORY:]
```

Because most `user` turns after the first are **tool_result turns**, the trimmed history
very often **begins with a `tool_result` whose matching `tool_use` (in the preceding
assistant turn) was cut off**. On the next `/run`, `import_history` restores that list,
`seed` appends a new user turn, and `next_step` sends it to Anthropic. The API rejects a
`tool_result` that does not follow a `tool_use` in the immediately preceding assistant
turn:

```
400 invalid_request_error: messages.0: unexpected `tool_result` block ...
```

**Trigger:** one long run reaches >40 stored messages (30 steps ≈ 60 messages before the
end-of-run trim to 40), then the user sends a second message in the same session. The
second run 400s and the SSE stream emits a generic "model call failed" error. The session
is effectively wedged until `session_id` changes.

**Scope of impact:** Anthropic path only. The **OpenAI** path is safe (tool results are
`role: "tool"`, and the trim lands on the `"Current screen:"` user image turn, not on an
orphan). The **Gemini** path exports `None` (in-process history only), so it never trims.

---

## Files in scope

- `src/voice-agent/computer_use_service.py` — `_trim_history` (only this function).
- `src/voice-agent/tests/test_cu_adapters.py` **or** a new
  `src/voice-agent/tests/test_computer_use_service_history.py` — add the regression test.

## Files explicitly OUT of scope

- `pipeline/cu_adapters/*.py` — do not change adapter message shapes; the fix belongs in
  the shared trim helper.
- The voice tool (`tools/computer_use.py`) — unaffected; it does not use `_trim_history`.
- `_MAX_HISTORY` value — leave at 40; this plan fixes the *split*, not the cap.

## WHY OUT

The adapters are provider-format-owning and correct as written. The bug is entirely in the
shared trim that runs on already-exported histories, so fixing it there covers all callers
without touching per-provider code.

---

## The fix (as implemented — refined during execution)

> A first attempt ("drop leading `tool_result` turns until the head is a clean user turn")
> **fails for a pure tool-loop**: mid-loop, *every* user turn is a `tool_result` turn — the
> only clean user turn is the seed — so dropping orphans drains the whole tail and lands the
> head on an `assistant` (also illegal as message 0). The reproducing test below caught
> this. The correct approach pins the seed as the head.

**Pin the session's first turn (the seed — the only clean user turn) as the head, then
append the most recent messages starting at an `assistant` boundary.** `assistant` (tool_use)
is always immediately followed by its `user` (tool_result), so opening the suffix on an
assistant boundary keeps every pair whole and never leaves an orphan `tool_result` at the
head. Provider-safe: the OpenAI export's head is the `system` message (also a valid head);
Gemini never persists (`export_history` → `None`).

```python
def _trim_history(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(messages) <= _MAX_HISTORY:
        return messages
    head = messages[0]
    budget = max(1, _MAX_HISTORY - 1)          # room for the pinned head
    suffix_start = len(messages) - budget
    while suffix_start < len(messages) and messages[suffix_start].get("role") != "assistant":
        suffix_start += 1
    if suffix_start >= len(messages):
        return [head]                          # no valid suffix; keep just the seed
    return [head, *messages[suffix_start:]]
```

`messages[0]` is the seed task turn (clean `user`) for the Anthropic path across every run,
because this trim pins it — so it stays a valid head on every subsequent `/run`.

---

## Test plan (write this first — it reproduces the bug)

Add to `tests/test_cu_adapters.py` (it already imports the service helpers) or a new file.
Follow the existing pytest style in that file (plain functions, direct asserts, no fixtures
beyond what's there).

```python
from computer_use_service import _trim_history, _MAX_HISTORY


def _anthropic_history(steps: int):
    """Simulate an Anthropic adapter message list after `steps` tool round-trips:
    seed user turn, then per step: assistant(tool_use) + user(tool_result)."""
    msgs = [{"role": "user", "content": [{"type": "text", "text": "do the thing"}]}]
    for i in range(steps):
        msgs.append({"role": "assistant",
                     "content": [{"type": "tool_use", "id": f"t{i}", "name": "computer_use",
                                  "input": {"action": "capture"}}]})
        msgs.append({"role": "user",
                     "content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                                  "content": [{"type": "text", "text": "ok"}]}]})
    return msgs


def test_trim_never_starts_with_orphan_tool_result():
    # 30 steps -> 61 messages, well over _MAX_HISTORY (40); forces a trim.
    trimmed = _trim_history(_anthropic_history(30))
    assert trimmed, "trim must not empty the history"
    head = trimmed[0]
    assert head.get("role") == "user"
    # The head must NOT carry a tool_result block (that would 400 on Anthropic).
    content = head.get("content") or []
    assert not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    assert len(trimmed) <= _MAX_HISTORY


def test_trim_short_history_untouched():
    msgs = _anthropic_history(3)  # 7 messages
    assert _trim_history(msgs) is msgs
```

`test_trim_never_starts_with_orphan_tool_result` **fails on the current code** (head is a
tool_result turn) and passes after the fix.

---

## Done criteria (machine-checkable)

```
cd src/voice-agent && .venv/bin/python -m pytest tests/test_cu_adapters.py -q
```
Expected: all existing tests still pass **and** the two new tests pass. Full suite as a
gate:
```
cd src/voice-agent && .venv/bin/python -m pytest tests/ -q
```
Expected: no new failures vs. baseline (~3,000+ tests green).

Live behavior is not required to close this (no service restart needed to verify the
logic — it is pure), but if you do restart the sidecar
(`systemctl --user restart jarvis-computer-use.service`), a >30-step run followed by a
second message in the same session should no longer 400.

---

## Maintenance note

`_trim_history` is shared by all three provider persistence paths. The added guard keys off
the Anthropic-only `type == "tool_result"` user-block shape, so it stays inert for
OpenAI/Gemini. If a future adapter starts putting tool results in `user` turns with a
different block type, extend `_is_tool_result_turn` rather than special-casing the trim.
Watch: any change to `_MAX_HISTORY` or to `add_results` message shapes should re-run the
two tests above.

# Plan 002 — Optimize the Anthropic computer-use step: prompt caching, effort, in-run image cap

**Written against commit:** `8bede503`
**Category:** Performance / cost
**Effort:** M (a day incl. tests)
**Risk:** MED — touches the per-step Anthropic request; guarded by SDK-support checks + tests
**Confidence:** HIGH on caching + image-cap (best-practice, verified absent); MED on effort (benchmarked recommendation, SDK-version-dependent)

---

## Why this matters

The web `/computer-use` sidecar drives a Claude agent loop of up to `MAX_STEPS = 30`
iterations (`computer_use_service.py`). Each iteration calls
`AnthropicCUAdapter.next_step` (`pipeline/cu_adapters/anthropic_adapter.py`), which sends
`system + tools + messages` to `client.messages.create`. Three optimizations from
Anthropic's current computer-use guidance are **entirely absent** (verified:
`grep -rn "cache_control\|effort\|output_config" pipeline/cu_adapters/ computer_use_service.py`
returns nothing):

1. **No prompt caching.** The stable prefix — the system prompt plus the `computer_use`
   tool schema (a ~700-token tool definition with a very long description) — is re-sent and
   **re-billed at full input price on every one of the 30 steps.** Anthropic's guidance
   ("Manage screenshot history for prompt caching"): place one `cache_control` breakpoint
   after the system prompt and tool definitions. A single breakpoint turns 30 full-price
   prefix reads into 1 write + 29 cache reads (~0.1× each).

2. **In-run screenshots accumulate unbounded.** `add_results` appends every post-action
   screenshot into `self.messages` and they **all ride along for the rest of the run** —
   images are only stripped at `export_history` (between runs), never within a run. By step
   30 the request carries ~30 screenshots (~1,000–1,800 input tokens *each* =
   ~30k–54k tokens of images per final call), compounding the no-cache cost and adding
   upload latency. The voice path keeps a single newest frame; the sidecar does not.

3. **No `effort` tuning.** Sonnet 4.6 (the sidecar default) runs at the API-default effort.
   Anthropic's benchmarked computer-use guidance: **Sonnet 4.6 / Opus 4.6 → `effort:
   "medium"`** (best accuracy-to-cost; `max` adds cost without accuracy on UI tasks);
   **Opus 4.7 → `high` default, `low` for throughput.** Running at default over-spends
   thinking tokens per step.

Combined, a 30-step run currently costs several times what it needs to, and is slower.

---

## Files in scope

- `src/voice-agent/pipeline/cu_adapters/anthropic_adapter.py` — `next_step`, `add_results`,
  and the tool/system construction in `__init__`.
- `src/voice-agent/tests/test_cu_adapters.py` — extend `test_anthropic_adapter_parses_tool_use`
  and add assertions for caching / image-cap / effort request shape.

## Files explicitly OUT of scope

- `pipeline/cu_adapters/openai_adapter.py`, `gemini_adapter.py` — different SDKs; caching /
  effort semantics differ. A separate follow-up may mirror this; do NOT change them here.
- `pipeline/cu_adapters/base.py` — the abstract interface does not need to change.
- `computer_use_service.py` loop — no change; the optimizations live inside the adapter.
- `tools/computer_use.py` schema — do not shorten the description here (that is a separate
  token-budget decision).

## WHY OUT

Each provider adapter owns its SDK's caching/effort surface; changing all three at once is a
multi-caller change that risks the OpenAI/Gemini paths. This plan scopes to Anthropic only —
the default provider and the one whose SDK support for these features is certain.

---

## The changes

### 1. Cache the stable system + tool prefix

In `__init__`, keep `self.system` (a str) but build the request-time system as a
cache-marked block, and mark the tool definition for caching. In `next_step`:

```python
async def next_step(self) -> StepResult:
    system_blocks = [{
        "type": "text",
        "text": self.system,
        "cache_control": {"type": "ephemeral"},
    }]
    # Mark the tool schema too — tools render before system, so a breakpoint on
    # the tool caches the tools prefix; the system breakpoint caches system as well.
    tool = {**self._tool, "cache_control": {"type": "ephemeral"}}
    resp = await asyncio.to_thread(
        self._client.messages.create,
        model=self.model, max_tokens=4096,
        system=system_blocks, messages=self.messages, tools=[tool],
        **self._extra_request_kwargs(),   # effort, see §3
    )
    ...
```

`cache_control` on both the tool and the last system block keeps the whole
`tools → system` prefix cached across the 30 steps. (Max 4 breakpoints per request; we use
2, leaving room.)

**Verification of the win:** log `resp.usage.cache_read_input_tokens` in DEBUG. After step
1 it should be > 0 on every subsequent step. If it stays 0, a silent invalidator is present
(most likely: `self.system` or the tool description is being rebuilt with varying content —
it is not, so this should just work).

### 2. Cap in-run screenshots to the last N

Reuse the existing `_strip_images` logic to keep images only on the most recent tool_result
turns. Add a bounded variant and call it at the end of `add_results`:

```python
_KEEP_LAST_IMAGES = int(os.environ.get("JARVIS_CU_KEEP_LAST_IMAGES", "3"))

def add_results(self, results: List[ToolResult]) -> None:
    blocks = []
    for r in results:
        content = [{"type": "text", "text": r.text}]
        if r.image_b64:
            content.append(_img_block(r.image_b64))
        blocks.append({"type": "tool_result", "tool_use_id": r.call_id, "content": content})
    self.messages.append({"role": "user", "content": blocks})
    self._prune_old_images()

def _prune_old_images(self) -> None:
    """Keep image blocks only on the most recent _KEEP_LAST_IMAGES tool_result
    turns; replace older screenshots with a text placeholder. Preserves
    tool_use/tool_result PAIRING (only the image block is dropped, the text
    stays), so the request never 400s on a dangling tool_result."""
    # Walk newest->oldest, keep images on the first _KEEP_LAST_IMAGES user turns
    # that carry an image, strip the rest.
    kept = 0
    for m in reversed(self.messages):
        if m.get("role") != "user" or not isinstance(m.get("content"), list):
            continue
        has_img = any(isinstance(b, dict) and (
            b.get("type") == "image" or
            (b.get("type") == "tool_result" and isinstance(b.get("content"), list)
             and any(isinstance(c, dict) and c.get("type") == "image" for c in b["content"]))
        ) for b in m["content"])
        if not has_img:
            continue
        if kept < _KEEP_LAST_IMAGES:
            kept += 1
            continue
        m["content"] = _strip_one_turn_images(m["content"])   # drop image blocks, keep text
```

Implement `_strip_one_turn_images` by lifting the inner `clean()` from the existing
`_strip_images` (it already handles both top-level images and images nested inside
tool_result content, substituting `{"type":"text","text":"(screenshot)"}`). Do NOT prune
below the pairing — only image blocks are removed, tool_result envelopes stay.

**Why this is cache-safe here:** the cached prefix is `tools → system` (stable); message
content is not cache-marked, so per-turn image pruning does not invalidate anything we are
caching. The doc's "prune in batches, not every turn" caveat applies only when you *cache
message content* — we do not.

### 3. Set computer-use `effort`

Add a model-aware effort helper, guarded so it is inert if the installed `anthropic` SDK
does not accept `output_config`:

```python
def _extra_request_kwargs(self) -> Dict[str, Any]:
    if os.environ.get("JARVIS_CU_EFFORT_DISABLED", "").strip().lower() in {"1", "true", "on"}:
        return {}
    mid = self.model.lower()
    if "opus-4-7" in mid or "opus-4-8" in mid:
        effort = "high"
    elif "sonnet-4-6" in mid or "opus-4-6" in mid:
        effort = "medium"
    else:
        return {}      # haiku / unknown — leave at default
    return {"output_config": {"effort": effort}}
```

**Verify SDK support first** (escape hatch): before wiring this, run
```
cd src/voice-agent && .venv/bin/python -c "import anthropic; print(anthropic.__version__)"
```
and confirm `messages.create` accepts `output_config` (grep the installed SDK, or a live
1-message smoke call with a real `ANTHROPIC_API_KEY`). If it does not, STOP on §3 only —
ship §1 and §2 (which need no new SDK surface) and leave `_extra_request_kwargs` returning
`{}` with a `# TODO: enable when SDK supports output_config.effort` note. Do not fabricate a
parameter the SDK rejects.

---

## Test plan

Extend `tests/test_cu_adapters.py` using its existing mock-client pattern
(`test_anthropic_adapter_parses_tool_use` already builds a fake client capturing
`create(**kwargs)`):

1. **Caching present:** assert the captured `create` kwargs have a `system` that is a list
   whose last block carries `cache_control == {"type": "ephemeral"}`, and the tool carries
   `cache_control`.
2. **Image cap:** seed + drive 5 `add_results` calls each with an `image_b64`; assert that
   after the 5th, at most `_KEEP_LAST_IMAGES` (3) tool_result turns still contain an image
   block, and that **every** tool_result turn still has its text block (pairing intact).
3. **Effort:** for `model="claude-sonnet-4-6"` assert kwargs include
   `output_config={"effort": "medium"}`; for `"claude-opus-4-7"` → `"high"`; for
   `"claude-haiku-4-5"` assert `output_config` is absent.
4. **Effort kill-switch:** with `JARVIS_CU_EFFORT_DISABLED=1`, assert `output_config` absent.

## Done criteria

```
cd src/voice-agent && .venv/bin/python -m pytest tests/test_cu_adapters.py -q
```
All existing + new tests pass. Then the full gate:
```
cd src/voice-agent && .venv/bin/python -m pytest tests/ -q
```
No new failures. If §3 was deferred for SDK reasons, its two tests are marked
`pytest.mark.skip("SDK lacks output_config.effort — see plan 002 §3")` with the reason,
not deleted.

---

## Maintenance note

- If the sidecar ever switches to Anthropic's **native** `computer_20251124` tool (see the
  review's direction finding), the tool-schema `cache_control` moves onto the native tool
  block and the description-token concern disappears (the native schema is built-in).
- `_KEEP_LAST_IMAGES = 3` mirrors Anthropic's "keep the last three screenshots" default.
  Lower it only if you see context-length pressure on very long tasks; raise it only if the
  model starts losing track of earlier screens.
- Re-check `_extra_request_kwargs` model matching whenever a new Claude model id is added to
  `_ALLOWED_MODELS` in `computer_use_service.py` — an unmatched id silently falls back to
  default effort, which is safe but leaves the tuning win on the table.

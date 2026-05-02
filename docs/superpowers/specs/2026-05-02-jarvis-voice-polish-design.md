# JARVIS Voice Polish — Design

**Date:** 2026-05-02
**Status:** approved (auto-mode)
**Scope:** four self-contained edits to the voice agent + browser specialist
**Goal:** eliminate three observed user-facing failures — silent stream hangs, mid-chain bail (`max tool steps reached`), and YouTube/Google search never typing in the search box

## Background

Three independent research agents surveyed how production projects (browser-use, Pipecat, Skyvern, Vapi, Retell, OpenAI Agents SDK) handle the failure modes we've been hitting. Eight concrete fixes surfaced. This design ships the four highest-leverage ones — the "hot path" bundle — and defers the rest until we've measured the impact of these.

### The three observed failures this design targets

1. **Silent stream hang.** Captured live: supervisor handed off to browser specialist, specialist's `on_enter` fired, then nothing for 3+ minutes. `LLM_KWARGS={"timeout": 5.0, "max_retries": 0}` looks like a fix but is connect-only — once one chunk arrives the timer never re-fires. Source: livekit-agents `types.py:75-90` (`APIConnectOptions`).

2. **Mid-chain bail.** Live log: `maximum number of function calls steps reached, generating final response with tool_choice='none'`. Default `max_tool_steps=3`. YouTube search needs 5+. Per LiveKit docs ([livekit/agents](https://docs.livekit.io/agents/build/sessions/)).

3. **Search-box never reached.** When asked "search YouTube for X", the specialist navigates to YouTube but can't find or type into the search input — YouTube's `<input id="search">` lives inside a Web Component shadow DOM that most CSS selectors miss. Production agents (browser-use, Stagehand) sidestep this by *navigating directly to the search results URL*: `youtube.com/results?search_query=X`.

## Scope

**In scope (this design):**
- Unit 1 — LLM stream idle timeout
- Unit 2 — Raise `max_tool_steps`
- Unit 3 — `web_search(engine, query)` direct-URL tool
- Unit 4 — OpenAI handoff prefix in supervisor prompt

**Out of scope (deferred):**
- Per-tool `function_call_timeout` (Pipecat pattern)
- `tool_choice="required"` on supervisor handoff turns
- Skyvern-style validator-after-each-step
- Lower-temperature retry on `tool_use_failed`

We expect the four in-scope fixes to address ~80% of observed failures. If anything still leaks after a day of dogfood, we revisit the deferred items in a separate spec.

## Architecture

The four units are independent and stack on top of the existing voice-agent topology without touching its core flow:

```
LiveKit voice loop
  ↓
[STT — Groq Whisper]
  ↓
Supervisor agent (jarvis_agent.py)         ← Unit 4: handoff prefix added to JARVIS_INSTRUCTIONS
  ├─ LLMStream._run                         ← Unit 1: idle-timeout patch wraps original
  ├─ tool_name_sanitizer                    (existing)
  ├─ dsml_sanitizer                         (existing)
  ├─ pycall_sanitizer                       (existing)
  └─ handoff_text_suppressor                (existing — Unit 4 reduces what reaches it)
  ↓ transfer_to_browser
Browser specialist (specialists/browser.py)
  ├─ AgentSession(max_tool_steps=15)        ← Unit 2
  └─ tools: ext_* (37) + web_search         ← Unit 3 NEW
       ↓
       Bridge → jarvis-screen extension → user's Chrome
  ↓
[TTS — Groq Orpheus / Edge fallback]
```

## Components

### Unit 1 — LLM stream idle timeout

**Module:** `src/voice-agent/llm_idle_timeout.py` (new file).

**Patch target:** `livekit.agents.inference.llm.LLMStream._run`. Same hook the existing sanitizers use; stacks above `tool_name_sanitizer`.

**Behavior:**
1. Wrap `orig_run(self)` in `asyncio.wait_for(orig_run(self), timeout=N)` where `N = JARVIS_LLM_IDLE_TIMEOUT` (env, default 30s).
2. On `asyncio.TimeoutError`, raise `livekit.agents._exceptions.APITimeoutError(retryable=True)` so `FallbackAdapter` flips to the secondary LLM (DeepSeek).
3. On `JARVIS_LLM_IDLE_TIMEOUT=0`, no-op (passthrough). Lets us disable for debug without restart-rebuilding.
4. Idempotent install via `_jarvis_idle_timeout_patched` flag (matches the convention in the other sanitizers).

**Why this isn't a true idle timeout:**
A "no token for 30s" check would require chunk-level instrumentation. A whole-stream timeout is simpler and effective: voice replies finish in 1-3s; tool-call turns finish in 5-15s; anything past 30s is a stalled stream regardless of cause. False positives possible on legitimately slow generations but acceptable trade-off — DeepSeek fallback usually completes faster anyway.

**Install site:** `src/voice-agent/jarvis_agent.py` alongside the other sanitizer installs (lines 102-127 area).

### Unit 2 — Raise `max_tool_steps`

**File edits:**
- `src/voice-agent/jarvis_agent.py` — supervisor's `AgentSession(...)` construction
- `src/voice-agent/specialists/registry.py` — `RegistrySpecialist` doesn't currently set `max_tool_steps`; LiveKit applies the default 3. Pass through `spec.max_tool_steps` (new field, default 15).
- `src/voice-agent/specialists/browser.py`, `desktop.py`, `planner.py`, `browser_v2.py`, `validator.py`, `code_reviewer.py`, `memory_recall.py`, `github.py`, `researcher.py`, `summarize.py`, `weather.py` — only `browser` and `desktop` need `max_tool_steps=15`; the others are one-shot subagents and stay at the LiveKit default.

**Why 15:**
- YouTube search via direct URL = 1 step (post-Unit 3); manual DOM = 5
- Login flow = 6-8 (navigate, observe email, type, observe password, type, click)
- Skyvern's WebVoyager runs use ~30 steps; browser-use defaults to 100
- 15 is a generous buffer for our voice constraints without enabling runaway loops
- Validated by browser-use system prompt: at 75% step consumption, it switches to consolidate-and-bail. With 15 steps, that warning fires at 11.

### Unit 3 — `web_search(engine, query)` tool

**Module:** add to `src/voice-agent/jarvis_browser_ext.py` (extends the existing 37 ext_* tool surface to 38).

**Routing table** (verbatim from browser-use's `search` action):

```python
_SEARCH_URLS = {
    "youtube":    "https://www.youtube.com/results?search_query={q}",
    "google":     "https://www.google.com/search?q={q}",
    "bing":       "https://www.bing.com/search?q={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "amazon":     "https://www.amazon.com/s?k={q}",
    "maps":       "https://www.google.com/maps/search/{q}",
    "wikipedia":  "https://en.wikipedia.org/wiki/Special:Search?search={q}",
}
```

**Behavior:**
- `web_search(engine="youtube", query="cooking videos")` → `urlencode("cooking videos")` → `_post("navigate", url=<built>)` (in current tab) or `_post("new_tab", url=<built>)` (configurable, default same-tab).
- Unknown engine → fall back to Google.
- Unknown engine name returned in summary so LLM can self-correct: `"Searched Google for cooking videos (engine='youtube' was not recognized)"` → never happens for the canonical 7.

**Specialist prompt update** ([specialists/browser.py](src/voice-agent/specialists/browser.py) `BROWSER_INSTRUCTIONS`):

Add a section near the top:

> ═══ SEARCH SHORTCUT ═══
>
> When the user wants to search a known site (YouTube, Google, Amazon, Maps, Wikipedia, Bing, DuckDuckGo), call **`web_search(engine, query)` ONE TIME**. Do NOT navigate then look for a search box — those sites' search inputs live inside shadow DOMs that selectors miss.
>
> Examples:
> - "search YouTube for cooking videos" → `web_search(engine="youtube", query="cooking videos")` → done.
> - "google the weather in Paris" → `web_search(engine="google", query="weather in Paris")` → done.
> - "find an iPhone on Amazon" → `web_search(engine="amazon", query="iPhone")` → done.

### Unit 4 — Handoff prefix in supervisor prompt

**File:** `src/voice-agent/jarvis_agent.py` — prepend to `JARVIS_INSTRUCTIONS` (or to the section that defines transfer tools — placement matters less than presence).

**Text** (verbatim from OpenAI Agents SDK `agents.extensions.handoff_prompt.RECOMMENDED_PROMPT_PREFIX`, [github.com/openai/openai-agents-python](https://github.com/openai/openai-agents-python/blob/main/src/agents/extensions/handoff_prompt.py)):

> "Handoffs are achieved by calling a handoff function, generally named `transfer_to_<agent_name>`. Transfers between agents are handled seamlessly in the background; do not mention or draw attention to these transfers in your conversation with the user."

**Why this works in addition to the existing `handoff_text_suppressor`:**
- Prefix is upstream — reduces the *probability* the LLM emits anticipatory text in the first place. Research found this brings leakage down by 50-80% in production reports.
- `handoff_text_suppressor` is downstream — catches what still slips through.
- Belt and suspenders.

## Data flow

End-to-end trace for "search YouTube for cooking videos" (post-fix):

```
1. STT: "search YouTube for cooking videos"
2. Supervisor LLM (Groq llama-3.3-70b)
   - Sees handoff prefix in system prompt → emits transfer_to_browser ONLY
   - handoff_text_suppressor blanks any stray content
   - Stream wrapped in 30s idle timeout (no risk)
3. Framework switches to browser specialist, voices ack_phrase ("At once, sir.")
4. Browser specialist on_enter
   - max_tool_steps=15 (plenty of headroom)
   - Stream wrapped in 30s idle timeout
5. Specialist LLM emits ONE tool call: web_search(engine="youtube", query="cooking videos")
6. web_search → ext_navigate(url="https://www.youtube.com/results?search_query=cooking%20videos")
7. Bridge → extension → user's Chrome navigates → returns DOM summary
8. Specialist task_done("Searched YouTube for cooking videos, sir.")
9. TTS speaks task_done summary
```

Total: ONE specialist tool step. Previous failure mode: 5 steps, blew through `max_tool_steps=3` limit, never typed in the search box.

## Error handling

| Scenario | Behavior |
|---|---|
| Idle timeout fires (Unit 1) | `APITimeoutError(retryable=True)` → FallbackAdapter switches to DeepSeek → user gets reply ~30-60s after speaking. Logged as `WARNING` for analysis. |
| `max_tool_steps=15` exhausted (Unit 2) | LiveKit's existing behavior: one final LLM call with `tool_choice='none'`, agent narrates partial state. Same as before, just rarer. |
| `web_search` engine unknown (Unit 3) | Falls back to Google with original query; summary mentions the fallback so the LLM can warn the user if needed. |
| Bridge / extension offline (Unit 3) | `web_search` is just a thin wrapper over `ext_navigate`; gets the same `bridge unreachable` error string the LLM already handles for other tools. |
| Supervisor LLM ignores handoff prefix (Unit 4) | `handoff_text_suppressor` still catches; user's experience unchanged from current. |

## Testing

No new pytest suite. Validation via dogfood + log analysis:

1. **Idle timeout:** monkey-patch a sleep into a stream in a one-off Python script; confirm `TimeoutError` after 30s, FallbackAdapter logs "switching to next LLM."
2. **Max tool steps:** ask "search YouTube for cooking videos AND scroll down twice" — chain that needs 4-5 steps. Confirm no `maximum number of function calls steps reached` in logs.
3. **`web_search`:** ask "search YouTube for cooking videos" — confirm exactly one `web_search` tool call in logs, exactly one `[handoff] → browser`, one `ext_navigate`, one `task_done`. New tab opened to YouTube results page.
4. **Handoff prefix:** sample 5-10 supervisor handoffs over a day; count `[confab-detector] dropping assistant turn` events. Expect material drop vs. baseline.

Success criteria: of 10 dogfood requests touching browser, ≥8 complete first attempt with no fake confirmation, no silent hang, no step-budget exhaustion.

## Rollout

All four units are restart-safe and idempotent. Single restart of `jarvis-voice-agent.service` after the edits. No DB migrations, no extension reload, no schema changes.

If anything regresses, `JARVIS_LLM_IDLE_TIMEOUT=0` disables Unit 1; the others are reversible by git revert.

## Open questions

None blocking. Items to revisit after a day of dogfood:
- Is 30s the right idle timeout? May need to tune up if Groq has occasional 25s legitimate generations.
- Does `tool_choice="required"` on supervisor turns need the deferred Option B work, or did the prefix alone solve it?
- Do we need the Skyvern validator step for non-search browser tasks (login, form fill)?

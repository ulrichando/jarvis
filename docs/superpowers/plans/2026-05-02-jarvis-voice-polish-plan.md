# JARVIS Voice Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate three observed user-facing failures in the JARVIS voice agent — silent stream hangs, mid-chain bail (`max tool steps reached`), and YouTube/Google search never typing in the search box — via four small, independent edits.

**Architecture:** Four self-contained units stack on the existing voice-agent: (1) new module patches `LLMStream._run` with `asyncio.wait_for` for idle-timeout fallback; (2) one-line config edit raises `AgentSession.max_tool_steps` from default 3 to 15; (3) one new `@function_tool` `web_search` is added to the browser specialist tool list with a hard-coded URL routing table; (4) the OpenAI Agents SDK handoff-prefix sentence is prepended to the supervisor's system prompt.

**Tech Stack:** Python 3.13, LiveKit Agents 1.5.6, Groq llama-3.3-70b (primary LLM), DeepSeek-V4-flash (fallback), aiohttp, pytest, monkey-patch sanitizer pattern (matches existing `dsml_sanitizer.py`, `pycall_sanitizer.py`, `tool_name_sanitizer.py`, `handoff_text_suppressor.py`).

---

## Pre-flight context for the implementer

You will be working in `/home/ulrich/Documents/Projects/jarvis`. The voice agent lives at `src/voice-agent/`. The agent uses a virtualenv at `src/voice-agent/.venv/`. The voice agent runs as a systemd user service (`jarvis-voice-agent.service`). After every code change, restart it via:

```bash
systemctl --user restart jarvis-voice-agent.service
sleep 4
systemctl --user is-active jarvis-voice-agent.service   # expect: active
```

Logs live at `/tmp/jarvis-voice-agent.log` (plain text, JSONL events interleaved). To filter to message-only lines:

```bash
grep -E '"message"' /tmp/jarvis-voice-agent.log | grep -ivE 'exc_info|Traceback' | tail -50
```

**Existing sanitizer pattern reference** (the model for Unit 1): `src/voice-agent/handoff_text_suppressor.py` and `src/voice-agent/dsml_sanitizer.py`. Each defines a single `install()` function that idempotently patches `livekit.agents.inference.llm.LLMStream._run` (or `_parse_choice`). Install sites are in `jarvis_agent.py` lines 102–135.

**Commit style:** prefix each commit with `voice:` for voice-agent work or `voice-spec:` for plan/doc commits. Match the existing repo log (run `git log --oneline -10` to see).

---

## Task 1 — Unit 1: LLM stream idle timeout

**Files:**
- Create: `src/voice-agent/llm_idle_timeout.py`
- Modify: `src/voice-agent/jarvis_agent.py` (add 2-line install block alongside the other sanitizers)

- [ ] **Step 1: Read the reference sanitizer to absorb the install pattern**

```bash
cat src/voice-agent/handoff_text_suppressor.py
```

Read top to bottom. Note: docstring up top with bug context, `install()` function, `_jarvis_*_patched` flag, monkey-patches `inf_llm.LLMStream._run` (or `_parse_choice`).

- [ ] **Step 2: Create the new module**

Create `src/voice-agent/llm_idle_timeout.py` with this exact content:

```python
"""Wrap each LLM stream in `asyncio.wait_for` so a stalled Groq
connection raises TimeoutError instead of hanging forever.

Live failure 2026-05-02 22:01: supervisor handed off to browser
specialist, specialist's `on_enter` fired, then dead air for 3+
minutes — Groq HTTP stream stalled mid-token. Our `LLM_KWARGS={
"timeout": 5.0, "max_retries": 0}` looks like a fix but is connect-
only (see livekit-agents `types.py` `APIConnectOptions`); once one
chunk arrives, the timer never re-fires.

This module patches `inference.llm.LLMStream._run` to wrap the
original call in `asyncio.wait_for(..., timeout=N)`. On timeout we
raise `APITimeoutError(retryable=True)` so the FallbackAdapter
flips to the secondary LLM (DeepSeek). User gets a (slower) reply
instead of dead silence.

Tunable via `JARVIS_LLM_IDLE_TIMEOUT` (seconds, default 30).
Setting it to 0 disables the wrap (debug only).

Idempotent. Stacks on top of the existing sanitizer patches.
Reference pattern: handoff_text_suppressor.py.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger("jarvis.llm_idle_timeout")


def _timeout_seconds() -> float:
    raw = os.environ.get("JARVIS_LLM_IDLE_TIMEOUT", "30")
    try:
        v = float(raw)
    except ValueError:
        v = 30.0
    return v


def install() -> None:
    """Patch LLMStream._run with an asyncio.wait_for envelope.
    Idempotent."""
    from livekit.agents.inference import llm as inf_llm
    from livekit.agents._exceptions import APITimeoutError

    if getattr(inf_llm.LLMStream, "_jarvis_idle_timeout_patched", False):
        return

    timeout = _timeout_seconds()
    if timeout <= 0:
        logger.warning(
            "JARVIS_LLM_IDLE_TIMEOUT=%s — idle-timeout DISABLED", timeout
        )
        inf_llm.LLMStream._jarvis_idle_timeout_patched = True
        return

    orig_run = inf_llm.LLMStream._run

    async def _patched_run(self) -> None:
        try:
            await asyncio.wait_for(orig_run(self), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "[idle-timeout] LLM stream exceeded %.1fs — raising "
                "APITimeoutError so FallbackAdapter flips to next LLM",
                timeout,
            )
            # retryable=True so FallbackAdapter retries on the next LLM.
            raise APITimeoutError(retryable=True) from None

    inf_llm.LLMStream._run = _patched_run
    inf_llm.LLMStream._jarvis_idle_timeout_patched = True
    logger.info(
        "LLM idle-timeout installed (timeout=%.1fs via JARVIS_LLM_IDLE_TIMEOUT)",
        timeout,
    )
```

- [ ] **Step 3: Confirm syntax + idempotent install**

```bash
src/voice-agent/.venv/bin/python -c "
import sys; sys.path.insert(0, 'src/voice-agent')
import llm_idle_timeout
llm_idle_timeout.install()
llm_idle_timeout.install()  # second call should be a no-op
from livekit.agents.inference import llm as inf_llm
print('patched flag:', getattr(inf_llm.LLMStream, '_jarvis_idle_timeout_patched', False))
"
```

Expected output: `patched flag: True` (with one `LLM idle-timeout installed` log line, not two).

- [ ] **Step 4: Wire install into jarvis_agent.py**

Open `src/voice-agent/jarvis_agent.py` and locate the existing `pycall_sanitizer` install block (around line 122–127). Add the new install RIGHT AFTER `handoff_text_suppressor.install()`:

```python
# Wrap LLM streams in asyncio.wait_for so stalled Groq connections
# raise TimeoutError after JARVIS_LLM_IDLE_TIMEOUT (default 30s)
# instead of hanging forever. Captured live 2026-05-02: specialist
# on_enter fired then 3+ minutes of dead air — connect-only timeout
# couldn't see the stall. Patches LLMStream._run; stacks on top of
# the other sanitizers.
import llm_idle_timeout
llm_idle_timeout.install()
```

Find the right insertion point with `grep -n "handoff_text_suppressor.install" src/voice-agent/jarvis_agent.py`.

- [ ] **Step 5: Verify all patches still stack**

```bash
src/voice-agent/.venv/bin/python -c "
import sys; sys.path.insert(0, 'src/voice-agent')
import deepseek_roundtrip; deepseek_roundtrip.install()
import tool_name_sanitizer; tool_name_sanitizer.install()
import dsml_sanitizer; dsml_sanitizer.install()
import pycall_sanitizer; pycall_sanitizer.install()
import handoff_text_suppressor; handoff_text_suppressor.install()
import llm_idle_timeout; llm_idle_timeout.install()
from livekit.agents.inference import llm as inf_llm
flags = sorted(a for a in dir(inf_llm.LLMStream) if 'jarvis' in a)
print('jarvis-patched flags:')
for f in flags: print(f'  {f} = {getattr(inf_llm.LLMStream, f)}')
"
```

Expected: SIX `_jarvis_*_patched = True` flags including `_jarvis_idle_timeout_patched`.

- [ ] **Step 6: Commit**

```bash
git -C /home/ulrich/Documents/Projects/jarvis add src/voice-agent/llm_idle_timeout.py src/voice-agent/jarvis_agent.py
git -C /home/ulrich/Documents/Projects/jarvis commit -m "voice: llm_idle_timeout — asyncio.wait_for wrap on LLMStream._run, fixes silent Groq stalls (Unit 1 of voice polish)"
```

---

## Task 2 — Unit 2: Raise `max_tool_steps`

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py:4698` — the `AgentSession(...)` call

This is the smallest task. `max_tool_steps` is an `AgentSession` parameter (verified: livekit-agents 1.5.6 `voice/agent_session.py:228`, default 3). The supervisor session is reused for specialists, so this single edit covers all agents.

- [ ] **Step 1: Locate the AgentSession constructor call**

```bash
grep -nE "session = AgentSession\(" src/voice-agent/jarvis_agent.py
```

Expected: a single match around line 4698.

- [ ] **Step 2: Add max_tool_steps=15 to the AgentSession call**

Edit `src/voice-agent/jarvis_agent.py`. Find:

```python
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
```

Change to:

```python
    session = AgentSession(
        # 2026-05-02: raised from livekit's default 3 to 15. Browser
        # specialist chains commonly need 5+ tool calls (navigate,
        # wait_for_load, observe, type, keypress) and 3 was burning
        # the budget on retries — 'maximum number of function calls
        # steps reached' truncated the chain mid-task. 15 leaves
        # headroom for login + form + submit (~8) without enabling
        # runaway loops.
        max_tool_steps=15,
        vad=ctx.proc.userdata["vad"],
```

- [ ] **Step 3: Verify syntax**

```bash
src/voice-agent/.venv/bin/python -c "import ast; ast.parse(open('src/voice-agent/jarvis_agent.py').read()); print('syntax OK')"
```

Expected: `syntax OK`.

- [ ] **Step 4: Commit**

```bash
git -C /home/ulrich/Documents/Projects/jarvis add src/voice-agent/jarvis_agent.py
git -C /home/ulrich/Documents/Projects/jarvis commit -m "voice: AgentSession max_tool_steps 3 -> 15 — fix mid-chain bail on multi-step browser flows (Unit 2 of voice polish)"
```

---

## Task 3 — Unit 3: `web_search(engine, query)` direct-URL tool

**Files:**
- Modify: `src/voice-agent/jarvis_browser_ext.py` (add `web_search` tool + register in `ALL_TOOLS`)
- Modify: `src/voice-agent/specialists/browser.py` (add SEARCH SHORTCUT section to `BROWSER_INSTRUCTIONS`)

- [ ] **Step 1: Find ALL_TOOLS definition**

```bash
grep -n "ALL_TOOLS\s*=\s*" src/voice-agent/jarvis_browser_ext.py
```

Expected: one assignment line near the bottom.

- [ ] **Step 2: Add the web_search tool to jarvis_browser_ext.py**

Add this block to `src/voice-agent/jarvis_browser_ext.py` IMMEDIATELY BEFORE the `ALL_TOOLS = [...]` assignment line:

```python
# ── Search shortcut (2026-05-02) ──────────────────────────────────────
#
# Production agents (browser-use, Stagehand) skip the search-box DOM
# entirely for known sites and navigate to the search-results URL
# directly. This sidesteps shadow-DOM problems (YouTube's
# `<input id="search">` lives in a Web Component) AND collapses the
# 5-step chain (navigate → wait → observe → type → submit) into ONE
# tool call.
#
# URL templates copied verbatim from browser-use's `search` action,
# `browser_use/tools/service.py:442` + the canonical YouTube + Maps
# patterns documented across the web for ~15 years.

import urllib.parse

_SEARCH_URLS = {
    "youtube":    "https://www.youtube.com/results?search_query={q}",
    "google":     "https://www.google.com/search?q={q}",
    "bing":       "https://www.bing.com/search?q={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "amazon":     "https://www.amazon.com/s?k={q}",
    "maps":       "https://www.google.com/maps/search/{q}",
    "wikipedia":  "https://en.wikipedia.org/wiki/Special:Search?search={q}",
}


@function_tool
async def web_search(engine: str, query: str, new_tab: bool = False) -> str:
    """Search a known website by going DIRECTLY to its results URL —
    no DOM clicking, no shadow-DOM searching, no typing required.

    Use this for any "search X for Y" / "find Y on X" voice request
    when X is one of the known engines. Collapses what would be a
    5-step chain (navigate, wait, observe, type, press Enter) into
    ONE tool call.

    Args:
        engine: One of 'youtube', 'google', 'bing', 'duckduckgo',
                'amazon', 'maps', 'wikipedia'. Unknown engines fall
                back to Google.
        query:  The search term, plain text (URL-encoding handled here).
        new_tab: If True, opens results in a brand-new tab. Default
                 False (replaces the current tab) — matches the
                 voice-flow "open YouTube and search" intent.

    Returns:
        One-line confirmation string with engine + query, e.g.
        "Searched YouTube for cooking videos."
    """
    eng = (engine or "").strip().lower()
    template = _SEARCH_URLS.get(eng) or _SEARCH_URLS["google"]
    encoded = urllib.parse.quote(query or "")
    url = template.format(q=encoded)

    if new_tab:
        result = await _post("new_tab", url=url)
    else:
        result = await _post("navigate", url=url)

    if not result.get("ok"):
        return _summarize(result)

    pretty_engine = eng if eng in _SEARCH_URLS else f"Google (engine={eng!r} unknown)"
    return f"Searched {pretty_engine} for {query!r}, sir."
```

Then update the `ALL_TOOLS = [...]` list to include `web_search` — open the file, find the existing list, and add `web_search` to it. The exact location of `ALL_TOOLS` was returned by Step 1 — append the new symbol at the end of the bracket, comma-separated.

- [ ] **Step 3: Verify the new tool registers and is callable**

```bash
src/voice-agent/.venv/bin/python -c "
import sys; sys.path.insert(0, 'src/voice-agent')
import jarvis_browser_ext as m
print('ALL_TOOLS count:', len(m.ALL_TOOLS))
print('web_search present:', any('web_search' in str(t) for t in m.ALL_TOOLS))
print('_SEARCH_URLS keys:', sorted(m._SEARCH_URLS.keys()))
"
```

Expected:
- `ALL_TOOLS count: 38` (was 37, +1 for web_search)
- `web_search present: True`
- All 7 engine keys.

- [ ] **Step 4: Round-trip test the URL builder logic**

```bash
src/voice-agent/.venv/bin/python -c "
import sys, asyncio; sys.path.insert(0, 'src/voice-agent')
import jarvis_browser_ext as m
captured = {}
async def fake_post(action, **args):
    captured['action'] = action
    captured['args'] = args
    return {'ok': True}
m._post = fake_post

def inner(t):
    for a in ('_callable','_orig_func','__wrapped__'):
        v = getattr(t, a, None)
        if v: return v
    return t

# YouTube
r = asyncio.run(inner(m.web_search)(engine='youtube', query='cooking videos'))
print('YouTube:', captured)
assert 'youtube.com/results?search_query=cooking%20videos' in captured['args']['url'], captured

# Unknown engine falls back to Google
r = asyncio.run(inner(m.web_search)(engine='invalid', query='x'))
print('Unknown:', captured)
assert 'google.com/search' in captured['args']['url'], captured

# new_tab=True uses new_tab action
r = asyncio.run(inner(m.web_search)(engine='wikipedia', query='Linux', new_tab=True))
print('Wikipedia new tab:', captured)
assert captured['action'] == 'new_tab', captured
assert 'wikipedia.org' in captured['args']['url'], captured
print('ALL OK')
"
```

Expected: three lines showing correct URL builds + `ALL OK`.

- [ ] **Step 5: Update browser specialist prompt with SEARCH SHORTCUT section**

Open `src/voice-agent/specialists/browser.py`. Find the section header `═══ TYPICAL FLOW ═══` and insert a new section IMMEDIATELY BEFORE it:

```
═══ SEARCH SHORTCUT — USE THIS FIRST FOR ANY SEARCH ═══

When the user wants to search a known site (YouTube, Google, Amazon,
Maps, Wikipedia, Bing, DuckDuckGo), call **`web_search(engine, query)`
ONE TIME**. Do NOT navigate then look for the search box — those
sites' search inputs live inside shadow DOMs that selectors miss.

Examples:
  user: "search YouTube for cooking videos"
  you:  web_search(engine="youtube", query="cooking videos")
  you:  task_done("Searched YouTube for cooking videos, sir.")

  user: "google the weather in Paris"
  you:  web_search(engine="google", query="weather in Paris")
  you:  task_done("Googled weather in Paris, sir.")

  user: "find an iPhone 15 on Amazon"
  you:  web_search(engine="amazon", query="iPhone 15")
  you:  task_done("Searched Amazon for iPhone 15, sir.")

Only use ext_navigate + ext_observe + ext_type + ext_keypress when
(a) the site isn't in the engine list, OR (b) the user wants you to
INTERACT with a specific search result, not just see them.

```

- [ ] **Step 6: Verify browser specialist prompt loads cleanly**

```bash
src/voice-agent/.venv/bin/python -c "
import sys; sys.path.insert(0, 'src/voice-agent')
import specialists.browser as b
assert 'SEARCH SHORTCUT' in b.BROWSER_INSTRUCTIONS, 'SEARCH SHORTCUT section missing'
assert 'web_search(engine=\"youtube\"' in b.BROWSER_INSTRUCTIONS, 'YouTube example missing'
print('browser specialist prompt OK ({} chars)'.format(len(b.BROWSER_INSTRUCTIONS)))
"
```

Expected: `browser specialist prompt OK (NNNN chars)`.

- [ ] **Step 7: Commit**

```bash
git -C /home/ulrich/Documents/Projects/jarvis add src/voice-agent/jarvis_browser_ext.py src/voice-agent/specialists/browser.py
git -C /home/ulrich/Documents/Projects/jarvis commit -m "voice: web_search direct-URL tool — collapses YouTube/Google/Amazon search from 5 steps to 1, sidesteps shadow-DOM (Unit 3 of voice polish)"
```

---

## Task 4 — Unit 4: Handoff prefix in supervisor prompt

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` — prepend the OpenAI Agents SDK handoff prefix to `JARVIS_INSTRUCTIONS`

- [ ] **Step 1: Locate JARVIS_INSTRUCTIONS**

```bash
grep -n "^JARVIS_INSTRUCTIONS\s*=" src/voice-agent/jarvis_agent.py
```

Expected: one match around line 1056.

- [ ] **Step 2: Read the first 5 lines of JARVIS_INSTRUCTIONS**

```bash
src/voice-agent/.venv/bin/python -c "
import sys; sys.path.insert(0, 'src/voice-agent')
# Read raw to avoid importing the whole agent
import re
src = open('src/voice-agent/jarvis_agent.py').read()
m = re.search(r'JARVIS_INSTRUCTIONS\s*=\s*\"\"\"\\\\\n(.{0,500})', src, re.DOTALL)
print(m.group(1) if m else 'not found')
"
```

Expected: the first ~500 chars of the prompt body, beginning right after `\` line continuation.

- [ ] **Step 3: Insert the handoff prefix at the very top of JARVIS_INSTRUCTIONS**

Edit `src/voice-agent/jarvis_agent.py`. The current shape is:

```python
JARVIS_INSTRUCTIONS = """\
<existing first line>
...
"""
```

Change to:

```python
JARVIS_INSTRUCTIONS = """\
HANDOFF DISCIPLINE (read first, applies always):
Handoffs are achieved by calling a transfer function, e.g. `transfer_to_browser`. Transfers between agents are handled seamlessly in the background; do NOT mention or draw attention to these transfers in your conversation with the user. When you call a transfer tool, emit ONLY the tool call — zero free-form text. The framework voices a brief acknowledgment automatically; the specialist voices the actual outcome.

<existing first line>
...
"""
```

The exact text "Handoffs are achieved by calling a transfer function ... do NOT mention or draw attention to these transfers in your conversation with the user." is verbatim from `RECOMMENDED_PROMPT_PREFIX` at https://github.com/openai/openai-agents-python/blob/main/src/agents/extensions/handoff_prompt.py — adopted as-is because it's known to be production-tested.

- [ ] **Step 4: Verify prompt loads + new section is at the top**

```bash
src/voice-agent/.venv/bin/python -c "
import sys; sys.path.insert(0, 'src/voice-agent')
import jarvis_agent as j
prompt = j.JARVIS_INSTRUCTIONS
first_line = prompt.split('\\n')[0]
print('first line:', first_line[:80])
assert 'HANDOFF DISCIPLINE' in prompt[:200], 'handoff prefix not at top'
assert 'do NOT mention or draw attention to these transfers' in prompt, 'verbatim text missing'
print('prompt loads OK ({} chars)'.format(len(prompt)))
"
```

Expected: first line starts with `HANDOFF DISCIPLINE (read first, applies always):` and prompt loads with both checks passing.

- [ ] **Step 5: Commit**

```bash
git -C /home/ulrich/Documents/Projects/jarvis add src/voice-agent/jarvis_agent.py
git -C /home/ulrich/Documents/Projects/jarvis commit -m "voice: prepend OpenAI handoff prefix to supervisor prompt — silent-handoff convention reduces anticipatory text leakage (Unit 4 of voice polish)"
```

---

## Task 5 — Restart and verify

**Files:**
- (none — runtime verification only)

- [ ] **Step 1: Restart the voice agent**

```bash
systemctl --user restart jarvis-voice-agent.service
sleep 4
systemctl --user is-active jarvis-voice-agent.service
```

Expected: `active`.

- [ ] **Step 2: Confirm all six sanitizer flags are set on the running agent**

```bash
src/voice-agent/.venv/bin/python -c "
import sys; sys.path.insert(0, 'src/voice-agent')
import deepseek_roundtrip; deepseek_roundtrip.install()
import tool_name_sanitizer; tool_name_sanitizer.install()
import dsml_sanitizer; dsml_sanitizer.install()
import pycall_sanitizer; pycall_sanitizer.install()
import handoff_text_suppressor; handoff_text_suppressor.install()
import llm_idle_timeout; llm_idle_timeout.install()
from livekit.agents.inference import llm as inf_llm
flags = sorted(a for a in dir(inf_llm.LLMStream) if 'jarvis' in a and 'patched' in a)
assert len(flags) == 6, f'expected 6 patches, got {len(flags)}: {flags}'
for f in flags: assert getattr(inf_llm.LLMStream, f) is True, f
print(f'{len(flags)} sanitizers patched and active')
"
```

Expected: `6 sanitizers patched and active`.

- [ ] **Step 3: Confirm web_search tool is registered**

```bash
src/voice-agent/.venv/bin/python -c "
import sys; sys.path.insert(0, 'src/voice-agent')
import jarvis_browser_ext as m
names = [getattr(t, 'name', None) or getattr(getattr(t, '_func', None), '__name__', '?') for t in m.ALL_TOOLS]
print('total tools:', len(names))
assert 'web_search' in names, f'web_search missing from {names}'
print('web_search registered')
"
```

Expected: `total tools: 38` and `web_search registered`.

- [ ] **Step 4: Confirm /status reports healthy**

```bash
curl -s http://localhost:8767/status
```

Expected JSON has `"connected": true`, `"agent_present": true`, `"silent_mode": false`.

- [ ] **Step 5: Run pytest if any voice-agent tests exist**

```bash
cd src/voice-agent && [ -d tests ] && .venv/bin/pytest tests/ -q 2>&1 | tail -20 || echo "(no tests dir)"
```

Expected: all tests pass (if a `tests/` dir exists). The voice agent has at least `tests/test_confab_detector.py` — that suite should still pass since we did not touch the confab detector.

- [ ] **Step 6: Check the agent's startup log for the new install line**

```bash
grep -E "idle-timeout|max_tool_steps|web_search" /tmp/jarvis-voice-agent.log | tail -5
```

Expected: at least one line `LLM idle-timeout installed (timeout=30.0s via JARVIS_LLM_IDLE_TIMEOUT)`. (The other two units don't log at install time — they're prompt + config edits.)

- [ ] **Step 7: Final commit (if any test fixes were needed)**

If any of Steps 1–6 surfaced an issue and you patched it, stage + commit the fix:

```bash
git -C /home/ulrich/Documents/Projects/jarvis status
# if there are changes:
git -C /home/ulrich/Documents/Projects/jarvis add -A
git -C /home/ulrich/Documents/Projects/jarvis commit -m "voice: fix-up after polish bundle dogfood verification"
```

If everything passed cleanly, no commit needed.

---

## Done definition

After Tasks 1–5 are complete, all of these must be true:

1. `git log --oneline -5` shows four `voice:` commits in order: idle-timeout, max_tool_steps, web_search, handoff-prefix.
2. `systemctl --user is-active jarvis-voice-agent.service` returns `active`.
3. `/status` returns JSON with `agent_present: true`.
4. The Python smoke test in Task 5 Step 2 prints `6 sanitizers patched and active`.
5. `web_search` shows up in `ALL_TOOLS` with `total tools: 38`.
6. The voice-agent startup log contains a `LLM idle-timeout installed` line.

If all six are true, the polish bundle is shipped and the agent is ready for live dogfood.

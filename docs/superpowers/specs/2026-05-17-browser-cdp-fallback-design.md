# Browser subagent: CDP (Playwright) fallback alongside extension

**Status:** spec
**Date:** 2026-05-17
**Author:** Ulrich (designed in conversation with Claude)
**Why now:** the Chrome extension reconnect handshake was just hardened (commit `d32e1806`), but the extension path still has structural fragility a fallback can't fix: cold Chrome takes 8–15 s to boot, the extension SW is killed by Chrome on idle, and the bridge↔extension hop adds two extra failure points the supervisor blames in confusing ways. Industry-standard browser automation (Playwright, OpenAI Operator, Anthropic Computer Use) bypasses extensions entirely and drives Chromium over CDP. Adding that as a fallback gives JARVIS a "always works" path without losing the extension's session-sharing magic.

## Goal

When the Chrome extension isn't connected to the bridge, the browser subagent silently falls back to a CDP-driven Chromium (Playwright). User gets reliable browser automation either way; the extension path stays primary so logged-in sessions still work.

## Architecture

```
                 transfer_to_browser
                          │
                          ▼
              ┌───────────────────────┐
              │  _ensure_chrome_ext   │   pre_transfer hook
              │   _connected (hook)   │   (hardened today)
              └───────────┬───────────┘
                          ▼
            ┌─────────── router ───────────┐
            │  ext_status.connected?       │
            └──┬───────────────────────┬───┘
               │ yes                   │ no (extension dead OR cold-fail)
               ▼                       ▼
        tools/browser_ext.py    tools/browser_cdp.py  ← NEW
        (38 actions, today)     (10 core actions, CDP)
               │                       │
               ▼                       ▼
        bridge → ext WS         Playwright async API
        → user's Chrome         → dedicated Chromium
                                  (~/.jarvis/cdp-profile)
```

**Router lives in** `subagents/browser.py`. At handoff start, it does one `_bridge_ext_connected()` probe; on `True` it returns the extension tool set, on `False` it returns the CDP tool set. The supervisor doesn't need to know which backend is in use — same tool names, same JSON shapes.

## Profile strategy

**Persistent CDP-only profile** at `~/.jarvis/cdp-profile/`. Three options considered:

| Strategy | Pros | Cons | Verdict |
|---|---|---|---|
| Fresh per session | safest; no cookie leak | loses every login | ❌ useless for "check my Gmail" |
| Reuse user's main profile | shared sessions | Chrome locks the profile dir; races user's main Chrome | ❌ hard race |
| **Persistent CDP-only profile** | persists logins across runs, no race | user logs in twice (main Chrome + JARVIS-CDP) | ✅ chosen |

First-time UX: user runs `bin/jarvis-browser-login` once to launch the CDP Chromium in *visible* mode so they can sign into Gmail / X / Claude / etc. Sessions persist.

## Action surface — minimum viable mirror

Mirror only the 10 actions the supervisor actually hits in the 95th-percentile path. Skip the long tail (storage_state, debugger console, pdf export) — those land as v2 once the core proves out.

| Action          | Extension impl              | CDP impl                          |
|-----------------|-----------------------------|-----------------------------------|
| `navigate`      | `chrome.tabs.update`        | `page.goto(url)`                  |
| `click`         | content-script DOM click    | `page.click(selector)`            |
| `type`          | content-script `input` evts | `page.fill(selector, text)`       |
| `key`           | content-script key events   | `page.keyboard.press(key)`        |
| `scroll`        | content-script scrollBy     | `page.mouse.wheel(dx, dy)`        |
| `get_text`      | content-script extract      | `page.text_content(selector)`     |
| `screenshot`    | `captureVisibleTab`         | `page.screenshot()`               |
| `list_tabs`     | `chrome.tabs.query`         | `browser.contexts[0].pages`       |
| `wait_for_load` | webNavigation event         | `page.wait_for_load_state`        |
| `get_url`       | `chrome.tabs.get`           | `page.url`                        |

`observe` (the LLM-friendly element ranker from extension's `_bgObserve`) is the one nontrivial port — mirror the same scoring inside a `page.evaluate(js_string)` payload. Reuse the existing JS verbatim.

## Files to create / modify

| File | Status | Purpose |
|---|---|---|
| `src/voice-agent/tools/browser_cdp.py` | **new** (~400 lines) | Playwright wrapper, the 10 actions as `@function_tool`, lazy singleton lifecycle |
| `src/voice-agent/tools/cdp_chrome.py` | **new** (~150 lines) | Singleton manager: spawn-on-first-use, health check, restart on death, auto-shutdown after 5min idle, atexit cleanup |
| `src/voice-agent/subagents/browser.py` | modify (~30 line addition) | Router: check `ext_status` at handoff, pick `browser_ext.TOOLS` vs `browser_cdp.TOOLS` |
| `src/voice-agent/requirements.txt` | modify | Add `playwright>=1.49.0,<2.0` (pin major) |
| `src/voice-agent/tests/test_browser_cdp.py` | **new** (~250 lines) | Mock Playwright API; test each of 10 actions + lifecycle |
| `src/voice-agent/tests/test_browser_router.py` | **new** (~100 lines) | Verify ext-vs-CDP router picks the right backend on each ext_status state |
| `bin/jarvis-browser-login` | **new** (~30 lines) | One-shot: launch the CDP profile in *visible* mode so user can sign in once |
| `install.sh` | modify (~15 lines) | `pip install playwright` + prompt-gated `playwright install chromium` (~200MB) |
| `CLAUDE.md` | modify (~10 lines) | Document the dual-backend pattern under Voice-agent architecture |
| `src/voice-agent/subagents/HOW_TO_ADD_A_SUBAGENT.md` | modify (~5 lines) | Note that new browser tools should be added to both backends |

## Build sequence

Each step is independently testable. We can stop at any point and the existing extension path is untouched.

1. **Add Playwright dep + install-script gate.** Verify on a clean install dir.
2. **`cdp_chrome.py` singleton.** Spawn, health check, shutdown — tests with mocked Playwright async context manager.
3. **`browser_cdp.py` actions.** Port the 10 actions one at a time, each with a unit test using Playwright's `route` interception against a local `data:` URL.
4. **Router in `browser.py`.** At this point the subagent can use either backend — write the router test BEFORE the action ports if TDD discipline desired.
5. **Live test.** Disable the extension in `chrome://extensions`, say "Jarvis, open YouTube". Verify CDP Chrome launches, navigates, and JARVIS reports success.
6. **`bin/jarvis-browser-login`.** One-shot login flow for first-time setup.
7. **Docs.** Update CLAUDE.md + HOW_TO_ADD_A_SUBAGENT.md.

## Verification plan

- **Unit:** mock `async_playwright()`; assert each action calls the right Playwright method with the right args; assert lifecycle manager spawns once and reuses across calls.
- **Router unit:** mock `_bridge_ext_connected` returning True / False / raises; verify the right `TOOLS` list is returned + a single decision log line fires.
- **Integration (manual):** with extension disabled in `chrome://extensions`, issue 3 representative voice commands (open URL, click a link, type into a search box); verify telemetry + screenshot evidence.
- **Regression:** full voice-agent test suite stays green (currently 1566 tests).

## Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| 200 MB Chromium download on `install.sh` surprises user | high | `install.sh` prompts; skip-flag `JARVIS_SKIP_CDP=1`; document size in the prompt |
| Two Chrome icons in taskbar confuses user | medium | Window title set to "JARVIS Browser" for visible mode; headless by default in production |
| User logged into Gmail in main Chrome, CDP Chrome shows logged-out | by design | `bin/jarvis-browser-login` for first-time setup; document the model in CLAUDE.md |
| Playwright API changes break us | low | pin `playwright>=1.49.0,<2.0` |
| CDP Chrome leaks processes on voice-agent crash | medium | systemd `ExecStopPost=pkill -f "JARVIS Browser"` in unit override |
| Doubles RAM use when both Chromes running | medium | CDP Chrome only spawned on first need (lazy); auto-shutdown after 5 min idle |
| chromium binary doesn't match host Chrome features (DRM, native messaging) | low | irrelevant for v1 — we use it only for clicking/typing/reading; no DRM or extensions |

## Out of scope

- Replacing the extension (it stays primary)
- Bringing back the retired `browser_task` / browser-use library (we're using Playwright directly — leaner)
- New action surface beyond what the extension already exposes (no CDP-superpowers like network interception in v1)
- Cross-profile cookie syncing (user manages the two profiles separately)
- Visual diff regression testing (different problem; later)
- Reusing the CDP backend for the deleted task #42 e2e workflow (separate spec if revived)

## Effort

~1 day of focused work. Sequenced 1→7 with verification at each step so we can stop early if something blocks.

## Open questions for spec review

1. **Headless or visible by default?** Spec says headless in production / visible only for `jarvis-browser-login`. Alternative: visible always so user sees what's happening (matches extension's UX). Trade: visible = transparent, headless = lower RAM + cleaner taskbar. **Recommendation: visible always for parity with the extension; flip via env if user wants headless later.**

2. **What happens if BOTH extension is connected AND user explicitly says "use the side browser"?** No mechanism in v1 — router is single-decision. Could add a `force_cdp=true` arg to `transfer_to_browser`. **Recommendation: defer; the router is already complex enough.**

3. **Where does the JS for `observe` live?** Two options: (a) copy-paste the JS string into `browser_cdp.py`, (b) extract to a shared `src/voice-agent/tools/_browser_observe.js` file both backends read. **Recommendation: (b) — DRY across backends, easier to update in one place.**

4. **What happens to the CDP profile on `install.sh` re-run?** Spec doesn't say. **Recommendation: don't touch `~/.jarvis/cdp-profile/` on reinstall — it's user data.**

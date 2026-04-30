# JARVIS Extension Browser Control v3 — Manus-style architecture

**Date:** 2026-04-30
**Status:** Approved
**Scope:** `src/extensions/jarvis-screen/`, `src/cli/src/bridge/server.ts`, `src/voice-agent/jarvis_specialist_agents.py`, `src/voice-agent/jarvis_agent.py`

## Problem

JARVIS's voice agent has chronically failed to reliably drive browser automation across ~10 fixes (sanitizers, prompt rewrites, schema fixes, retry tuning, multi-agent handoff, GROUP-catalog removal, supervisor tool stripping). Two fundamental causes:

1. **Free Groq LLMs (`llama-3.3-70b-versatile`, `gpt-oss-120b`, `qwen3-32b`) are chat-trained, not agent-trained.** They malform structured tool calls, jam tool name + JSON args into one string, narrate success without firing tools, and lose discipline as the prompt grows. No prompt engineering closes the gap to Claude — the gap is in the model's training.

2. **The `browser_task` tool (browser-use library) requires the LLM to drive a long agentic loop** — for each step, screenshot → vision LLM → click coordinates → screenshot → next decision. Each step is 5–30 s and burns tokens. The LLM's tool-call discipline degrades over the long loop, and the user gets either no result or hallucinated success ("I've opened Gmail and read your unread emails") with zero side effects.

The reference architecture for solving this on a tight budget is **Manus's "Browser Operator" pattern**: a Chrome extension running inside the user's real browser, accepting deterministic high-level commands from a small planner LLM. The LLM plans (one tool call per step); the extension executes (no LLM round-trip per DOM op).

JARVIS already has a Manifest V3 Chrome extension (`jarvis-screen` v2.0) that's currently read-only (DOM extraction). It has `<all_urls>` host permissions, `scripting`, `activeTab`, and a service-worker bridge to the JARVIS backend. **All the infrastructure is in place; we just need to add an action surface.**

## Solution

Upgrade `jarvis-screen` v2.0 → v3.0 with a 25-command DevTools-like action surface. Add a `BrowserSpecialistAgent` that owns the step-by-step LLM loop. Add a bridge route that brokers commands between voice agent and extension over the bridge's existing WebSocket. Voice agent emits ONE structured command per LLM call; the extension handles the DOM mechanics deterministically.

### Architecture

```
                    USER VOICE  ("log into my Gmail")
                                ▼
              ┌────────────────────────────────────────────┐
              │  JarvisAgent (supervisor — read-only tools) │
              │  has only transfer_to_desktop +             │
              │  transfer_to_browser                        │
              └──────────────────┬─────────────────────────┘
                                 │ transfer_to_browser(intent)
                                 ▼
   ┌────────────────────────────────────────────────────────────────┐
   │  BrowserSpecialistAgent (NEW)                                   │
   │  step-by-step loop: ONE command per LLM call                    │
   │  has 25 ext_* tools + task_done()                               │
   └────────────────────────┬───────────────────────────────────────┘
                            │ HTTP POST /api/ext_browse
                            ▼
   ┌──────────────────────────────────────────────────────────┐
   │  jarvis-bridge (Bun.serve :8765)                          │
   │  + /api/ext_browse (NEW)        — POST commands          │
   │  + /ws (UPGRADED to bidirectional)                       │
   │  + /api/ext_status (NEW)        — connectivity check     │
   │  + extension command queue with cmd_id correlation       │
   └────────────────────┬─────────────────────────────────────┘
                        │ WebSocket (extension-initiated, kept alive)
                        ▼
   ┌──────────────────────────────────────────────────────────┐
   │  jarvis-screen v3.0 Chrome extension (UPGRADED)           │
   │  background.js: WS keep-alive + command dispatcher        │
   │  content.js: 25 action handlers + page_state response     │
   │  side_panel: "browser actions" status panel               │
   └──────────────────────────────────────────────────────────┘
```

### Components

| Component | Path | New / Modified | Responsibility | Lines (est) |
|---|---|---|---|---|
| Extension manifest | `src/extensions/jarvis-screen/manifest.json` | Modified | Bump v2.0→v3.0; add `webNavigation` permission for navigate-and-wait reliability | +5 |
| Extension service worker | `src/extensions/jarvis-screen/background.js` | Modified | Maintain persistent WS to bridge; receive command, dispatch to content.js, return page_state. Keep-alive ping every 25 s to prevent MV3 service-worker shutdown | +200 |
| Extension content script | `src/extensions/jarvis-screen/content.js` | Modified | 25 DOM action handlers; build `page_state` response (URL, title, page_text 2KB cap, dom_summary headings, actionable_elements top-30) | +400 |
| Extension side panel | `src/extensions/jarvis-screen/side_panel.{html,js}` | Modified | Show connection state + recent action log + manual override (kill switch + "pause") | +100 |
| Bridge route | `src/cli/src/bridge/server.ts` | Modified | New `/api/ext_browse` POST route; new `/api/ext_status` GET; promote `/ws` to bidirectional with cmd_id correlation queue | +150 |
| Browser specialist agent | `src/voice-agent/jarvis_specialist_agents.py` | Modified | Add `BrowserSpecialistAgent(Agent)` with 25 `@function_tool` ext_* methods + `task_done`. Loop iteration cap = 20. on_enter voices "Looking, sir." | +600 |
| Supervisor handoff | `src/voice-agent/jarvis_agent.py` | Modified | Add `transfer_to_browser` `@function_tool` on `JarvisAgent`; remove `browser_task` from supervisor's tool list (it's still importable for legacy fallback path) | +30 |
| Legacy fallback | `src/voice-agent/jarvis_browser.py` | Deprecated | Old browser-use lib path retained as fallback — gated on `JARVIS_USE_LEGACY_BROWSER=1`. Default path is the extension. | 0 (env-gated) |
| E2E test | `src/voice-agent/tests/test_extension_browser.py` | New | Boots bridge, launches Chrome with extension, runs fixed command sequence, asserts result | +120 |
| Content-script unit tests | `src/extensions/jarvis-screen/tests/content.test.js` | New | Jest + jsdom; one test per command | +250 |

### Data flow — one task end-to-end

User says: *"open Gmail and tell me how many unread"*

1. **JarvisAgent** routes the request via `transfer_to_browser(intent="open Gmail and report unread count")`. Hands off to BrowserSpecialistAgent.
2. **BrowserSpecialistAgent.on_enter** voices "Looking, sir." (one short cue, no narration of the plan).
3. LLM sees the intent and emits `ext_navigate(url="https://mail.google.com")`.
4. The tool method posts `{cmd_id, action:"navigate", args:{url:"..."}}` to `bridge:8765/api/ext_browse`.
5. Bridge looks up the active extension's WebSocket (registered when extension loaded), sends the command. Bridge holds the open POST request, waiting for the WS response.
6. **Extension `background.js`** receives the WS message, calls `chrome.tabs.update` (or creates a new tab if no Gmail tab exists), waits for `webNavigation.onCompleted`, then sends `{action:"navigate", url, ...}` to `content.js` via `chrome.tabs.sendMessage`.
7. **Extension `content.js`** waits for `document.readyState === "complete"`, builds and returns `page_state = {ok:true, url, title, page_text:<truncated 2KB>, dom_summary:[{level,text}], actionable_elements:[{selector,role,label,text}, ...up to 30]}`.
8. background.js wraps the result with the original `cmd_id` and pushes back over WS.
9. Bridge resolves the awaiting POST, returns the JSON to the BrowserSpecialistAgent's tool method.
10. LLM receives the `page_state`, sees Gmail is loaded, emits next: `ext_find_by_text(text="Inbox")`.
11. Loop repeats. After ~3-5 steps the LLM has enough info to call `task_done(summary="12 unread, sir.")`.
12. Specialist hands control back to JarvisAgent which voices the summary.

**Per-step latency budget:** ~50ms WS round-trip + content.js DOM op (5–500ms) + LLM next-step decision (200–500ms with `llama-3.3-70b-versatile`). Target: <1s per step. Compared to browser-use's 5–30s per step.

### Command surface (25 actions + task_done handoff)

#### Navigation (5)

| Command | Args | Returns |
|---|---|---|
| `ext_navigate(url)` | `url` | `page_state` |
| `ext_back()` | — | `page_state` |
| `ext_forward()` | — | `page_state` |
| `ext_get_url()` | — | `{url, title}` |
| `ext_close_tab()` | — | `{ok:true}` |

#### Reading the page (4)

| Command | Args | Returns |
|---|---|---|
| `ext_extract_text(selector="body")` | selector | `{text}` (truncated 4KB) |
| `ext_screenshot()` | — | `{image_b64}` (≤512KB PNG) |
| `ext_find_by_text(text)` | exact or substring | `{matches:[selector,...]}` |
| `ext_dom_summary()` | — | `{headings, actionable_elements:[{selector,role,label,text}, …]}` |

#### Mouse (5)

| Command | Args | Returns |
|---|---|---|
| `ext_click(selector)` | selector | `page_state` or error |
| `ext_right_click(selector)` | selector | `page_state` |
| `ext_hover(selector)` | selector | `{ok:true}` |
| `ext_drag(from_selector, to_selector)` | both | `page_state` |
| `ext_select(selector, value)` | `<select>` selector + option value or label | `page_state` |

#### Keyboard / input (4)

| Command | Args | Returns |
|---|---|---|
| `ext_type(selector, text)` | selector + text | `{ok:true}` |
| `ext_fill_form({field: value})` | dict mapping name/aria-label/placeholder → value | `{filled_count, missing:[...]}` |
| `ext_keypress(key)` | e.g. `"Enter"`, `"Escape"`, `"Ctrl+C"` | `{ok:true}` |
| `ext_submit(form_selector)` | form selector | `page_state` — **gated** |

#### Scroll / wait / dialog (4)

| Command | Args | Returns |
|---|---|---|
| `ext_scroll(direction, amount)` | `"up"/"down"/"left"/"right"` + px count or `"page"` | `{ok:true}` |
| `ext_wait_for(selector, timeout=10)` | selector + seconds | `{found:bool, page_state}` |
| `ext_accept_dialog(accept=true)` | bool | `{ok:true}` |
| `ext_switch_iframe(selector_or_index)` | iframe pointer | `{ok:true}` |

#### Power tools (3) — all gated

| Command | Args | Returns |
|---|---|---|
| `ext_exec_js(code)` | JS string | `{result_or_error}` — **always confirm** |
| `ext_get_cookies(domain)` | domain | `{cookies:[...]}` — **gated** |
| `ext_set_cookies(domain, cookies[])` | domain + cookies | `{ok:true}` — **always confirm** |

#### Handoff (1)

| Command | Args | Returns |
|---|---|---|
| `task_done(summary)` | one-line summary string | `(supervisor_agent, summary)` — handoff |

### Safety gates (deterministic, in extension — not LLM-decided)

| Gate | What triggers | Effect |
|---|---|---|
| **Destructive-verb regex** in tool args | `delete`, `purchase`, `buy`, `transfer`, `send`, `submit_payment`, `cancel_subscription` in selector text or page URL | Extension responds `{ok:false, needs_confirmation:true, preview:<exact action>}`. Specialist must voice the preview, wait for explicit user "yes/confirm", then re-call with `confirmed:true` flag |
| **Always-confirm commands** | `ext_exec_js`, `ext_set_cookies`, `ext_submit` on payment-domain pages (allowlist: `stripe.com`, `paypal.com`, Amazon checkout, common bank domains) | Same gate, no override |
| **Domain allowlist (off by default)** | If `~/.jarvis/browser-allowlist.txt` exists, extension refuses domains not on it | Hard block; voice agent gets clear "domain X not on allowlist" message |
| **No-credentials rule** | Tool args containing `password`, `OTP`, `2fa`, `MFA`, `cvv`, `card_number`, `ssn` | Refused at the extension boundary; voice agent told to ask user to enter manually |

### Configuration

All env-overridable; defaults match the shipped behaviour.

| env var | default | purpose |
|---|---|---|
| `JARVIS_USE_LEGACY_BROWSER` | `0` | If `1`, supervisor uses old `browser_task` (browser-use lib) instead of `transfer_to_browser` |
| `JARVIS_EXT_TIMEOUT_MS` | `10000` | Per-command timeout for the bridge ↔ extension round trip |
| `JARVIS_EXT_LOOP_MAX_STEPS` | `20` | Max iterations of the BrowserSpecialistAgent loop before forced handoff back |
| `JARVIS_EXT_BROWSER_ALLOWLIST` | unset | Path to allowlist file; if unset, all domains allowed |
| `JARVIS_EXT_REQUIRE_CONFIRM` | `1` | Master switch for safety gates; `0` disables all confirmation |

### Error handling

| Failure | Where caught | What user hears |
|---|---|---|
| Extension not connected | Bridge `/api/ext_browse` returns 503 | "Browser extension isn't connected, sir. Check Chrome." |
| WS disconnects mid-task | Bridge holds queue, retries 1× then fails | "Lost the browser connection, sir. Try again?" |
| Selector not found | content.js returns `{ok:false, error:"selector not found"}` | LLM's next step adapts (try different selector or `find_by_text`) |
| Page navigation timeout (>10s) | content.js returns timeout | LLM retries with longer `wait_for(timeout=20)` or asks user |
| Destructive action without confirm | Gate fires before action | Voice agent voices preview, waits, re-calls with `confirmed=True` |
| Loop runaway (>20 steps) | BrowserSpecialistAgent caps iterations | "I've taken 20 steps without finishing — switching back, sir." |
| Tab closed by user mid-task | `chrome.tabs.sendMessage` rejects | content.js returns `{ok:false, error:"tab closed"}` → loop ends gracefully |
| Service worker killed mid-task | Keep-alive misses → bridge sees WS close | Bridge reports "Browser extension dropped, sir." |

### Testing

Following the project's existing pytest conventions (`src/voice-agent/tests/`) plus a Jest suite for the extension.

#### Unit (content.js handlers — Jest + jsdom)

`src/extensions/jarvis-screen/tests/content.test.js`:
- One test per command (25 tests). Mock the DOM with jsdom; inject simple HTML; verify each handler returns the correct `page_state` envelope and mutates the DOM correctly.
- Edge cases: invalid selector → graceful error; missing element → `{ok:false, error:...}`.

#### Bridge route tests

`src/cli/src/bridge/tests/ext_browse.test.ts`:
- POST a command, assert it's queued with cmd_id.
- Mock-WS-respond with matching cmd_id, assert POST resolves with the response.
- Timeout case: WS doesn't respond, assert 504 after `JARVIS_EXT_TIMEOUT_MS`.
- Multiple concurrent commands, assert correlation works.
- ~10 tests.

#### BrowserSpecialistAgent unit tests

`src/voice-agent/tests/test_browser_specialist.py`:
- Loop terminates cleanly on `task_done`.
- Loop respects iteration cap.
- Tool method posts to bridge with correct shape.
- Confirmation gate triggers on destructive verbs.
- ~6 tests.

#### Integration smoke (E2E)

`src/voice-agent/tests/test_extension_browser.py`:
- Boots the bridge.
- Launches Chrome with the unpacked extension via `subprocess.Popen([CHROME, "--load-extension=...", "--user-data-dir=/tmp/jarvis-test-profile"])`.
- Waits for extension WS to connect to bridge.
- Sends a fixed sequence: `ext_navigate("https://example.com")` → `ext_find_by_text("More information")` → `ext_extract_text("body")`.
- Asserts the extracted text contains expected markers.
- ~120 lines, single Python file.

#### Manual dogfood checklist (in the spec, not automated)

8 voice-driven flows the user actually cares about. Each is a "did it work end-to-end?" yes/no:

1. *"Open Gmail"* — Gmail loads in active tab, signed-in profile.
2. *"Search YouTube for lofi hip hop"* — search executes, results visible.
3. *"What's the unread count in my Gmail?"* — extract count from inbox.
4. *"Open my Amazon order history"* — navigate to orders page.
5. *"Post 'hello world' on Twitter"* — composes, asks for confirmation, posts after "yes".
6. *"Scroll to the bottom of the LinkedIn feed"* — scrolls smoothly.
7. *"Find the cheapest flight from SFO to NYC next Friday"* — multi-step search + extract.
8. *"Click the orange 'subscribe' button on this page"* — finds by colour-or-text, clicks.

### Configuration changes outside code

The user must reload the extension once after the v3.0 ship:
1. Open `chrome://extensions`
2. Toggle Developer mode on
3. Find the JARVIS extension, click "Reload"
4. Verify status pill in side panel shows "Connected to JARVIS bridge"

### Verification

- `pytest src/voice-agent/tests/test_browser_specialist.py` → all green
- `pytest src/voice-agent/tests/test_extension_browser.py` → E2E smoke green
- `cd src/extensions/jarvis-screen && npx jest` → 25/25 unit tests green
- `cd src/cli && bun test src/bridge/tests/ext_browse.test.ts` → 10/10 green
- Manual dogfood: 6/8 of the 8 flows above work first-try without manual intervention
- Latency: median per-step (navigate excluded) ≤ 1s, p95 ≤ 2s

### Rollback

`JARVIS_USE_LEGACY_BROWSER=1` reverts to the old `browser_task` (browser-use library) path. The supervisor's `transfer_to_browser` tool detects the env var and routes to `browser_task` instead of the specialist. No code changes needed to revert.

To fully remove v3 mid-session without env-flag access:
1. Remove the `transfer_to_browser` `@function_tool` method from `JarvisAgent`
2. Restart `jarvis-voice-agent`

The extension itself can stay loaded — without the agent calling `/api/ext_browse`, it just sits idle.

## Out of scope

- **Cross-tab orchestration** ("on tab A copy this, on tab B paste") — possible with current architecture, parked for v2
- **Headless mode / second Chrome instance** — extension runs in user's live Chrome only
- **OAuth / 2FA automation** — explicitly refused at extension boundary; user enters credentials manually
- **Browser-use library** — kept as `JARVIS_USE_LEGACY_BROWSER=1` env fallback only; not the default path
- **Mobile browsers / Firefox** — Firefox extension exists (`jarvis-screen-firefox`) but manifest differs; ship Firefox in v2
- **Screenshot-based vision actions** — extension ops are DOM-based; for "click the orange button at coordinates 400,200" the user falls back to `transfer_to_desktop` + `computer_use`
- **Multi-step forms across pages with state** (e.g. multi-page checkouts) — handled by the LLM loop step-by-step, but no special "wizard" support
- **Recording macros** ("remember how I did this and replay") — defer to v2
- **Anthropic Computer Use API integration** — out of scope (paid)
- **Manus Cloud Browser** — out of scope (paid SaaS)

## Success criteria

1. The 8 manual dogfood flows above achieve ≥6/8 first-try success after the v3 ship
2. Median per-step latency (excluding navigation, which depends on the site) ≤ 1s
3. Zero "I've done X" hallucinations on action requests — every claim corresponds to a fired `ext_*` tool call in the agent log
4. `tail -f /tmp/jarvis-voice-agent.log | grep ext_` shows real tool firings during browser tasks (no narration trap)
5. `JARVIS_USE_LEGACY_BROWSER=1` cleanly reverts to the v2 `browser_task` behaviour
6. All four test suites green: content.js Jest, bridge ts test, BrowserSpecialistAgent pytest, E2E smoke
7. Extension reload doesn't break the live Chrome session — user keeps cookies/tabs/login state

## Why this works when prompt engineering didn't

| Approach | What the LLM has to do reliably | Free Groq models can do this? |
|---|---|---|
| `browser_task` via browser-use lib | Multi-step screenshot → coordinates → click loop, tens of LLM round-trips | No — chronic narration trap, malformed tool calls |
| Claude computer-use (paid) | Same loop pattern | Yes — Anthropic specifically trained Claude for this |
| **Extension v3 (this spec)** | **One structured tool call per step**. The action layer is deterministic JS. | **Yes** — emitting one valid `tool_use` block is well within free Groq capability |

The architecture moves the unreliable thing (multi-step computer-use loops) out of the LLM and into deterministic JavaScript. The LLM only needs to be reliable at **one tool call**, which it is.

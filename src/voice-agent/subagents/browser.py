"""Browser subagent — drives a real Chrome via the jarvis-screen
extension. Replaces the legacy `browser_task` (browser-use library)
path: instead of one all-in-one black box, the LLM emits one DOM-
level command per turn and steps the task forward (Manus pattern).

The 38 ext_* @function_tools live in `tools.browser_ext.py`; this
file just wraps them in a HandoffSubagent so the LLM reaches them
through the same registry handoff as desktop.

A `pre_transfer` hook (`_ensure_chrome_extension_connected`) guards
the handoff: if the bridge reports the extension as disconnected,
the hook launches `google-chrome --profile-directory=Default` and
waits up to ~8s for the extension to register. Without this, "open
YouTube" on a cold-Chrome system handed off to the subagent only
for it to bail with "extension not connected" — JARVIS told the
user the system was broken when in fact Chrome just wasn't open.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Optional

from .registry import HandoffSubagent, register
from ._ack_phrases import ACK_BROWSER


logger = logging.getLogger("jarvis.subagents.browser")


BROWSER_INSTRUCTIONS = """\
You are JARVIS's browser subagent. The supervisor handed control to
you because the user wants something done in a real Chrome browser:
log in to a site, post a tweet, check Gmail, scroll a feed, fill a
form, navigate a multi-step UI.

YOUR ONE JOB: drive the browser one DOM action at a time, voice a
one-sentence summary when done, hand back to the supervisor via
task_done().

═══ HARD RULE (read this before anything else) ═══

Your FIRST action on every turn MUST be a tool call. Not text. Not
"Done, sir." Not "A new tab is open." A TOOL CALL — ext_new_tab,
ext_click, ext_navigate, ext_screenshot, ext_type, web_search, etc.

**EVERY turn MUST end with `task_done(summary)`.** No exceptions.
Even if you give up. Even if you can't figure out what to do. The
framework needs the task_done signal to hand back to the supervisor.
A turn that ends with text-only and no task_done is a BUG — your
text gets TTS-spoken (potentially as a hallucinated success claim)
and the supervisor never gets a clean handback.

═══ CRITICAL: NEVER SAY "TAB IS OPEN" UNLESS YOU JUST OPENED ONE ═══

Past failure 2026-05-06 turn 1104 (this is the canonical past
failure — read it carefully):

  user: "Sounds like you weren't able to perform it."
  you (browser subagent) replied: "A new tab is open in your
       browser, sir."

You said this WITHOUT calling ext_new_tab in this handoff. You
made it up. Zero ext_* tool calls fired all session. The user
heard the lie via TTS. They challenged you in the next turn and
you confessed: "The first time, I had not yet executed the
action — I only outlined what I intended to do."

THAT WAS A LIE PASSED OFF AS REPLY TEXT. It's the worst failure
mode you can have — voicing fake reality.

**The rule, exact:** before you say ANY of these phrases as
reply text, your IMMEDIATELY-PRIOR message in this turn MUST
contain a SUCCESSFUL `ext_new_tab` / `ext_navigate` /
`ext_click` / etc. tool result:

  ❌ "A new tab is open" / "Tab opened" / "Done, sir."
  ❌ "I've opened" / "I navigated" / "I clicked"
  ❌ "It's open" / "It's done" / "Successfully opened"
  ❌ Any past-tense action verb claiming completion

If no tool fired this turn, the only valid reply text is:
  ✅ "I haven't actually done that yet — let me try."
  ✅ (then call the right ext_* tool)
  ✅ task_done with the actual outcome (success OR failure)

**Test before speaking:** scan your prior messages in this turn.
Is there a `<tool_result>` from ext_new_tab / ext_navigate /
ext_click? If NO → you have not done the thing. Do not say you
have.

═══ NEVER WRITE PROTOCOL SHAPES AS REPLY TEXT ═══

Tool calls go in the structured `tool_calls` field, NEVER in your
reply text. The voice TTS reads reply text LITERALLY — protocol
syntax becomes audible garbage. **Banned forms** (any of these as
reply text is a bug — re-emit as a real tool call):

  ❌ `task_done("...")` — Python form. task_done is a TOOL, not
     reply text. Never type those characters.
  ❌ `<function>ext_list_tabs</function>` — XML bare-tag form
     (live-captured 2026-05-06 turn 1093: user said "open a new
     tab on my current browser", you emitted that string as text
     instead of calling ext_list_tabs as a tool. TTS read the
     angle brackets aloud and no tab opened. The user heard
     gibberish and saw nothing happen).
  ❌ `<function=ext_navigate>{"url":"..."}</function>` — XML
     attribute form.
  ❌ `[{"name":"ext_click","parameters":{"selector":"..."}}]` —
     JSON-array form.
  ❌ `<tool_call>...</tool_call>` — generic tool-call wrapper.
  ❌ Anything starting with a tool name followed by `(` or `<`.

If you find your draft starting with `<` or `task_done(` or `[{"`,
STOP. Re-emit the turn as a real structured tool_call. The
framework's tool dispatch is the path; the reply-text channel is
for SUMMARIES (after a tool result lands), nothing else.

═══ NO BAILOUT-FIRST RULE (READ TWICE) ═══

Your FIRST tool call MUST do real work — `web_search`, `ext_navigate`,
`ext_new_tab`, `ext_screenshot`, `ext_observe`, `ext_dom_summary`, or
any other ext_* action that interacts with Chrome. Your first tool
call MUST NOT be `task_done`.

`task_done` is the EXIT, not the entrance. You may only call it AFTER
you have already executed at least one ext_* / web_search tool AND
seen its result in this turn's tool history.

If you don't know what to do given the user's request, your safe
fallback is `ext_screenshot()` (to see what's on screen) or
`web_search(engine="google", query=<the request verbatim>)` — NEVER
task_done with a guessed summary.

Past failure 2026-05-02 23:35: subagent activated for "search YouTube
for cooking videos", emitted task_done("Intruder incidents reported,
reviewing security protocols") as its FIRST and ONLY tool call. No
search ran. The summary was hallucinated from background TV dialogue
in chat_ctx. Three follow-ups produced three more bogus task_done
summaries in the same pattern. This rule exists to break that pattern.

═══ NEVER-NARRATE RULE ═══

If you find yourself about to emit text content as the first thing,
STOP. Re-emit the turn as a tool_call. The chat history may show
prior turns where some other agent said "A new tab is open" without
calling a tool — those are CONFABULATIONS, not examples to follow.

═══ "OPEN X" REQUESTS — INSTANT NAVIGATE ═══

When the supervisor's `request` (or the user's request via
chat_ctx) is "Open X" / "Open the X website" / "Go to X" / "Take me
to X" where X is a recognizable site, your FIRST tool call MUST be
the navigation. Don't think, don't plan, just navigate. Then
verify with the tool result, then `task_done`.

Cheat sheet (lifted from `web_search`'s known engines for parity):

  YouTube      → web_search(engine="youtube", query="")
                 (an empty query lands on youtube.com homepage)
              OR ext_navigate(url="https://www.youtube.com")
  Gmail / mail → ext_navigate(url="https://mail.google.com")
  Twitter / X  → ext_navigate(url="https://twitter.com")
  Amazon       → ext_navigate(url="https://www.amazon.com")
  Google       → ext_navigate(url="https://www.google.com")
  GitHub       → ext_navigate(url="https://github.com")
  Reddit       → ext_navigate(url="https://www.reddit.com")
  Wikipedia    → ext_navigate(url="https://en.wikipedia.org")
  Maps         → web_search(engine="maps", query="<their query>")
                 OR ext_navigate(url="https://www.google.com/maps")
  any other recognizable site → ext_navigate(url="https://<site>.com")

After the navigate result lands, call `task_done(summary)` with a
result-based summary citing the tool result:

  ✅ "YouTube is loaded — homepage shows the user's subscriptions."
  ✅ "Gmail's open at the inbox."
  ❌ "YouTube's open now" (with no prior navigate tool_result)
  ❌ "Navigated to YouTube" (without naming what the page actually
     showed in the tool result)

**Do NOT** ext_screenshot first. Do NOT ext_list_tabs first. Do NOT
ext_observe first. The user's intent is unambiguous — JUST navigate.

Past failure 2026-05-13 01:03 UTC: handoff received with request
"Open YouTube (youtube.com)". Subagent activated. Subagent's LLM
never called ext_navigate; supervisor (or subagent confab) voiced
"YouTube's open now"; user pushed back "YouTube is not open"; loop
repeated. Chrome remained on chrome://new-tab-page/ the entire
time. Don't be that subagent.

═══ ABSOLUTE RULES ═══

1. **ONE COMMAND PER TURN.** Pick the next single action, fire its
   tool, look at the result, then decide the next action. Don't try
   to plan five steps ahead.

2. **THE TOOL IS HOW YOU ACT.** Never narrate "I'll click the login
   button" without firing ext_click. Tool result = ground truth.

2a. **NO ANTICIPATORY TEXT.** When you decide to call a tool, emit
   the tool_call ONLY — zero text content in the same turn. Don't
   say "Done, sir." or "Opening a tab now." BEFORE the tool returns.
   The framework streams text as the LLM produces it; if you say
   "Done" then the tool call fails (schema rejection, network error,
   bridge timeout), the user already heard "Done" but nothing
   happened. Voice the outcome AFTER you see the tool result, not
   before.

2b. **NEVER claim success without a tool result proving it.** Before
   any past-tense completion ("opened, sir" / "tab is open" /
   "posted" / "Done"), your IMMEDIATELY-PRIOR message MUST contain a
   successful tool result for the action you're claiming. If the
   LLM call failed mid-stream, no tool ran — call task_done with the
   error reason ("ext_keypress failed: <reason>") instead of
   confabulating success.

   **Past failure 2026-05-01**: user said "Open a new tab on the
   browser." Browser subagent replied "Done, sir." with NO tool
   call (Groq rejected its function-call attempt mid-stream). The
   user noticed immediately because the screen showed no new tab.
   This is the worst failure mode — voicing a fake reality. Always
   verify with the tool result; never speak success speculatively.

3. **CONFIRM DESTRUCTIVE ACTIONS.** Anything that posts content,
   sends a message, places an order, deletes data, OR runs raw JS:
   first call returns a confirmation prompt; voice it; only re-call
   with `confirmed=True` after explicit user OK ("yes", "do it",
   "confirm"). Background voices DO NOT count as confirmation.

4. **NEVER engage in conversation.** If the user changes topic mid-
   flight or this isn't a browser request, call task_done IMMEDIATELY
   with one of these EXACT bailout phrases (the framework only honors
   no-tool-fired exits when the summary contains one):

     - "user changed topic to <X>"
     - "not a browser task — handing back to supervisor"
     - "wrong subagent — needs the desktop subagent"
     - "wrong subagent — needs the supervisor"
     - "cannot accomplish with browser tools — handing back to supervisor"

   DO NOT freelance phrasing — the framework will refuse it and
   you'll be stuck in a loop. Use one of the exact phrases above.

5. **TAB SAFETY.** Don't open dozens of tabs. Reuse the active tab
   via ext_navigate. Close orphan tabs with ext_close_tab when done.

═══ SEARCH SHORTCUT — USE THIS FIRST FOR ANY SEARCH ═══

When the user wants to search a known site (YouTube, Google, Amazon,
Maps, Wikipedia, Bing, DuckDuckGo), call **`web_search(engine, query)`
ONE TIME**. Do NOT navigate then look for the search box — those
sites' search inputs live inside shadow DOMs that selectors miss.
This collapses 5 tool calls (navigate, wait, observe, type, Enter)
into 1.

Examples:
  user: "search YouTube for cooking videos"
  you:  web_search(engine="youtube", query="cooking videos")
  you:  task_done("Searched YouTube for cooking videos.")

  user: "google the weather in Paris"
  you:  web_search(engine="google", query="weather in Paris")
  you:  task_done("Googled weather in Paris.")

  user: "find an iPhone 15 on Amazon"
  you:  web_search(engine="amazon", query="iPhone 15")
  you:  task_done("Searched Amazon for iPhone 15.")

Only fall back to ext_navigate + ext_observe + ext_type + ext_keypress
when (a) the site isn't in the engine list, OR (b) the user wants you
to INTERACT with a specific search result, not just see them.

═══ TYPICAL FLOW ═══

```
user: "post 'gm' on twitter"
you:  ext_get_url()                              → "https://twitter.com/home"
you:  ext_find_by_text("What's happening?")      → "<textarea#tweet-box>"
you:  ext_type("textarea#tweet-box", "gm")       → "ok"
you:  ext_find_by_text("Post")                   → "<button.tweet-submit>"
[destructive — confirm first]
you:  task_done("Type-ready: 'gm' in Twitter compose. Confirm post?")
[supervisor voices the confirm; user says "yes"; supervisor calls
 transfer_to_browser again with confirmed=True via confirmed param]
```

═══ TOOLS YOU HAVE (38) ═══

**Navigation (7):** ext_navigate, ext_new_tab, ext_back, ext_forward, ext_get_url, ext_close_tab, ext_list_tabs

  - **"open a new tab"** / "open a tab" / "new tab" → `ext_new_tab()`.
    Optionally pass a URL to load there; default lands on Chrome's
    new-tab page. Use this — NOT ext_navigate, which replaces the
    current tab's content.
  - **"go to X.com"** in the existing tab → `ext_navigate(url)`.
  - **"what tabs are open"** / "list my tabs" → `ext_list_tabs()`.

**File I/O (2):**
  - **"save this page as PDF"** / "download this page" → `ext_save_pdf()`.
    Lands in the Downloads folder.
  - **"upload my CV"** / "attach this file" → `ext_upload_file(selector, file_path)`.
    Requires the file_path to be absolute and exist on Chrome's machine.

**Debugging (1):**
  - **"any errors in the console"** / "console says what" → `ext_get_console()`.
    Captures only logs AFTER first attach; reload the page if you
    need startup-time logs.

**Storage (5):**
  - **Cookies**: `ext_get_cookies(domain?)`, `ext_set_cookies(cookies, confirmed)`.
  - **`ext_local_storage(action, key?, value?, scope?)`** — modern web
    auth tokens live in localStorage, not cookies. action='list'/'get'/
    'set'/'delete'/'clear'. scope='local'|'session'.
  - **`ext_storage_state_get()` + `ext_storage_state_set(state)`** —
    full snapshot/restore of cookies + localStorage + sessionStorage
    as one JSON. Use for "save my login state" / "restore session."

**Forms helpers:**
  - **`ext_get_dropdown_options(selector)`** — call BEFORE ext_select
    when you're not sure of the option values. Returns array of
    {value, text, selected, disabled}.

**Observation + waiting (Phase C):**
  - **`ext_observe(query?, limit?)`** — find actionable elements by
    natural-language query. Returns ranked array of {selector, tag,
    role, text, suggested_method, score}. Use FIRST when you don't
    know the right CSS selector for what the user wants.
  - **`ext_wait_for_load(state?, timeout_ms?)`** — wait for the page
    to reach 'load' (default), 'domcontentloaded', or 'networkidle'.
    Use after navigation when the page is JS-heavy.
  - **`ext_download_file(url, filename?)`** — download a direct URL
    to the Downloads folder. For "click this button which triggers
    a download" just use ext_click — Chrome auto-saves.

**Reading:** ext_extract_text (page or selector), ext_find_by_text
(locate by visible text → returns selector hint), ext_dom_summary
(forms/buttons/headings overview), ext_screenshot

**Mouse:** ext_click, ext_right_click, ext_hover, ext_drag, ext_select

**Keyboard / forms:** ext_type (replaces input value), ext_fill_form
(multi-field — pass `fields_json` as a JSON OBJECT STRING, e.g.
`'{"#email":"a@b.com","#pw":"…"}'`), ext_keypress (Enter/Tab/Escape/
ArrowDown), ext_submit

**Scroll / waiting:** ext_scroll (up/down/top/bottom), ext_wait_for
(wait for selector), ext_accept_dialog (handle confirm/prompt),
ext_switch_iframe (work inside iframes)

**Power tools (gated):** ext_exec_js (raw JS), ext_get_cookies,
ext_set_cookies — all require confirmed=True for destructive intent.

**task_done(summary)** — REQUIRED when the user's request is
complete. One-line summary of what landed.

═══ COMMON PATTERNS ═══

- **Login flow:** ext_navigate → ext_wait_for("input[name=email]")
  → ext_type → ext_type → ext_click(submit). Verify with ext_get_url.

- **"What's on this page?":** ext_dom_summary first. If the user
  wants article text, ext_extract_text(selector="article").

- **Heavy JS / SPA:** ext_screenshot to see what's actually rendered;
  DOM-only inspection can lie.

- **Destructive verbs (post, send, buy, delete):** task_done with the
  proposed action and "Confirm?" — supervisor handles the voice
  confirmation, then re-handsoff with confirmed=True.

═══ EXAMPLES ═══

User: "open gmail"
You: ext_navigate("https://mail.google.com")
You: task_done("Gmail open.")

User: "what's on my screen right now"
You: ext_dom_summary()
You: task_done("<one-line summary of the DOM>")

User: "scroll down"
You: ext_scroll(direction="down", amount=600)
You: task_done("Scrolled down.")

User: "post 'hello world' on twitter"
You: ext_navigate("https://twitter.com/home")
You: ext_wait_for("textarea[data-testid=tweetTextarea_0]")
You: ext_type("textarea[data-testid=tweetTextarea_0]", "hello world")
You: task_done("'hello world' typed in Twitter compose. Confirm post?")
[wait for user 'yes', supervisor re-routes with confirmed]
"""


def _browser_tools() -> list:
    """Pick the tool surface based on extension connectivity.

    Router (added 2026-05-17 per docs/superpowers/specs/2026-05-17-
    browser-cdp-fallback-design.md):
      - Extension connected to bridge → return ALL_TOOLS from
        tools.browser_ext (38 actions on the user's real Chrome).
      - Extension NOT connected → return CDP_TOOLS from
        tools.browser_cdp (10 actions on a bundled Chromium driven
        via Playwright — gives JARVIS an "always works" browser even
        if the extension is broken / crashed / not loaded).

    The probe is a sync HTTP call to /api/ext_status with a 1s timeout.
    Bridge is local (127.0.0.1:8765), so the probe budget is tens of
    milliseconds in practice. If the bridge itself is unreachable we
    treat that as "extension not connected" and fall back to CDP.

    Tools surface is identical-named in both modules — the supervisor
    sees `ext_navigate / ext_click / ...` either way, so a single
    prompt covers both backends.

    Opt-out: JARVIS_BROWSER_DISABLE_CDP_FALLBACK=1 forces the extension
    surface even when not connected (subagent will then bail with the
    extension's existing "not connected" error path).
    """
    import os
    if _is_extension_connected_sync() or \
       os.environ.get("JARVIS_BROWSER_DISABLE_CDP_FALLBACK") == "1":
        from tools.browser_ext import ALL_TOOLS
        logger.info("[browser] router → extension backend (ALL_TOOLS)")
        return list(ALL_TOOLS)
    from tools.browser_cdp import CDP_TOOLS
    logger.info("[browser] router → CDP backend (CDP_TOOLS)")
    return list(CDP_TOOLS)


def _is_extension_connected_sync() -> bool:
    """Synchronous probe of /api/ext_status. Returns False on any
    error (timeout, bridge down, bad JSON). Used by `_browser_tools()`
    which is called sync from the registry construction path.

    Mirrors the async `_bridge_ext_connected()` defined later in this
    file — kept separate so we don't drag aiohttp into the sync path.
    """
    import json
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(
            f"{_BRIDGE_URL}/api/ext_status", timeout=1.0
        ) as resp:
            if resp.status != 200:
                return False
            data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("connected"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return False


_BROWSER_WHEN = (
    "Use whenever the user wants something done IN a web browser tab: "
    "log in to a site, navigate to a URL and read what's there, fill "
    "a form, click through a multi-step UI, post / tweet / send / buy "
    "(after confirmation), scroll a feed, check Gmail / Twitter / "
    "Amazon. Drives a real Chrome via the jarvis-screen extension. "
    "NOT for opening Chrome itself (that's transfer_to_desktop) "
    "or for multi-file project work (that's transfer_to_planner)."
)


# Pre-transfer hook config — overridable via env for tests / opt-out.
# `JARVIS_BROWSER_PRELAUNCH_DISABLE=1` keeps the supervisor's behavior
# from before this hook landed (transfer fires whatever the bridge
# state, subagent bails if extension not connected).
_BRIDGE_URL = os.environ.get("JARVIS_BRIDGE_URL", "http://127.0.0.1:8765")
_CHROME_LAUNCH_CMD = ["setsid", "-f", "google-chrome", "--profile-directory=Default"]
# Bumped 8 → 15 s on 2026-05-17. Cold Chrome on this box reliably
# takes 8-10 s from `setsid` to first WS-open (profile load + 20+
# extensions hydrating + service worker boot), then the extension
# reconnect ladder needs another 0.5-1 s for hello+ack. 8 s was hit
# repeatedly in logs ("extension never connected within 8.0s").
_PRELAUNCH_WAIT_S = float(os.environ.get("JARVIS_BROWSER_PRELAUNCH_WAIT_S", "15.0"))
# Two-phase polling: fast early to catch warm Chrome (most cases),
# slower late so we don't slam the bridge during the slow tail.
_PRELAUNCH_POLL_FAST_S = float(os.environ.get("JARVIS_BROWSER_PRELAUNCH_POLL_FAST_S", "0.15"))
_PRELAUNCH_POLL_SLOW_S = float(os.environ.get("JARVIS_BROWSER_PRELAUNCH_POLL_SLOW_S", "0.40"))
_PRELAUNCH_POLL_FAST_FOR_S = float(os.environ.get("JARVIS_BROWSER_PRELAUNCH_POLL_FAST_FOR_S", "3.0"))

# Pre-navigation table for "Open X" requests. When the supervisor's
# `request` matches one of these tokens (or contains an explicit URL),
# the pre_transfer hook navigates DIRECTLY via the bridge BEFORE the
# subagent activates — so the subagent's LLM doesn't have a chance to
# confabulate task_done as its first action while the user hears 20s
# of silence. Live failure 2026-05-13 01:11-01:12 UTC: "open Twitter"
# took two attempts because the first attempt's subagent LLM emitted
# task_done("Navigated to Twitter") with no real navigate, gate
# REFUSED, LLM retried (eventually firing ext_navigate), but the user
# had already given up and asked again.
#
# Site → URL mapping (lower-case, matched as a substring of request).
_SITE_URLS: dict[str, str] = {
    "youtube":   "https://www.youtube.com/",
    "gmail":     "https://mail.google.com/",
    "mail":      "https://mail.google.com/",
    "twitter":   "https://twitter.com/",
    "x.com":     "https://x.com/",
    "amazon":    "https://www.amazon.com/",
    "google":    "https://www.google.com/",
    "github":    "https://github.com/",
    "reddit":    "https://www.reddit.com/",
    "wikipedia": "https://en.wikipedia.org/",
    "maps":      "https://www.google.com/maps",
    "facebook":  "https://www.facebook.com/",
    "instagram": "https://www.instagram.com/",
    "linkedin":  "https://www.linkedin.com/",
    "netflix":   "https://www.netflix.com/",
    "spotify":   "https://open.spotify.com/",
    "claude":    "https://claude.ai/",
    "chatgpt":   "https://chatgpt.com/",
}

_URL_RE = re.compile(r"https?://\S+")


def _resolve_open_intent(request: str) -> Optional[str]:
    """Extract a target URL from a transfer_to_browser request like
    'Open YouTube' / 'Go to gmail.com' / 'Navigate to https://x.com'.

    Returns the canonical URL or None. None means 'request isn't an
    Open-X — let the subagent figure it out the slow way.'

    Resolution order:
      1. Explicit URL in the request → use it verbatim.
      2. Known-site keyword (youtube / gmail / etc.) → table lookup.
      3. `.com` / `.org` / `.net` heuristic (bare domain words like
         'example.com') → prepend https://.
    """
    if not request:
        return None
    req = request.strip()
    req_low = req.lower()

    # 1. Explicit URL anywhere in the request.
    m = _URL_RE.search(req)
    if m:
        url = m.group(0).rstrip(").,;:!?\"'")
        return url

    # 2. Known-site keyword.
    for keyword, url in _SITE_URLS.items():
        if keyword in req_low:
            return url

    # 3. Bare-domain heuristic (e.g. `open example.com`).
    bare = re.search(r"\b([a-z0-9][a-z0-9-]{1,62}\.(?:com|org|net|io|dev|app|co|ai|gg|me|tv))\b", req_low)
    if bare:
        return f"https://{bare.group(1)}/"

    return None


async def _bridge_ext_connected() -> bool:
    """GET /api/ext_status → {"connected": bool}.

    Returns False on any error (bridge down, network glitch, bad
    JSON). The hook treats False the same way regardless of root
    cause — try to launch Chrome and see if that fixes it. Keeps
    the hook simple at the cost of one redundant launch when the
    bridge itself is unreachable.
    """
    try:
        # Lazy aiohttp import — kept out of registry-load path.
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=2.0)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(f"{_BRIDGE_URL}/api/ext_status") as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                return bool(data.get("connected"))
    except Exception as e:
        logger.debug(f"[browser.pre_transfer] ext_status probe failed: {e}")
        return False


async def _bridge_navigate(url: str) -> bool:
    """POST `/api/ext_browse` with `action=navigate` and the given
    URL. Returns True on success. Logs on failure but never raises.
    """
    try:
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=10.0)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(
                f"{_BRIDGE_URL}/api/ext_browse",
                json={"action": "navigate", "args": {"url": url}},
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(
                        f"[browser.pre_transfer] navigate {url} → "
                        f"HTTP {resp.status}: {body[:200]}"
                    )
                    return False
                data = await resp.json()
                if not data.get("ok"):
                    logger.warning(
                        f"[browser.pre_transfer] navigate {url} → "
                        f"bridge error: {data.get('error', 'unknown')}"
                    )
                    return False
                return True
    except Exception as e:
        logger.warning(f"[browser.pre_transfer] navigate {url} raised: {e}")
        return False


async def _bridge_focus_chrome() -> None:
    """Bring Chrome to front via wmctrl. Best-effort, silent on failure.
    Mirror of `tools.browser_ext_nav._focus_chrome_window` but as a
    direct subprocess so the pre_transfer hook doesn't import the
    @function_tool module."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "wmctrl", "-a", "Google Chrome",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except Exception:
        pass


async def _launch_chrome() -> bool:
    """Fire `setsid -f google-chrome --profile-directory=Default`.

    Detached via setsid+`-f` so the chrome process group survives
    voice-agent restarts; the launch itself returns immediately.
    Returns False if `setsid` can't even spawn (PATH issue).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *_CHROME_LAUNCH_CMD,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        # setsid -f exits ~immediately after forking Chrome.
        await asyncio.wait_for(proc.wait(), timeout=2.0)
        return proc.returncode == 0
    except Exception as e:
        logger.warning(f"[browser.pre_transfer] Chrome launch failed: {e}")
        return False


async def _ensure_chrome_extension_connected(context, request, supervisor):
    """Pre-transfer hook: idempotently ensure Chrome is running AND
    the jarvis-screen extension is connected to the bridge BEFORE
    the browser subagent's `on_enter` fires.

    Live failure 2026-05-13 00:28 UTC: user said "open YouTube" with
    Chrome not running. Supervisor fired transfer_to_browser, browser
    subagent's ext_navigate hit the bridge, bridge returned 503
    "extension not connected", subagent bailed, user heard "the
    browser extension isn't connected" with no automatic recovery.

    The fix lifts the launch-Chrome-first responsibility from the
    soft prompt rules into a code-level invariant — same pattern
    `_ensure_screen_share_active` uses for the screen_share subagent.

    Steps:
      1. GET /api/ext_status. If connected → return None (proceed).
      2. Not connected → `setsid -f google-chrome --profile-directory
         =Default`. The flag set is what supervisor.md:2316 specifies.
      3. Poll ext_status every PRELAUNCH_POLL_S until connected or
         PRELAUNCH_WAIT_S elapsed.
      4. Connected → return None. Timed out → return abort string
         the supervisor voices ("Chrome is starting, give it a sec").

    Idempotent: a re-fire on an already-connected extension just
    runs step 1 and returns. Safe to call on every transfer.

    Opt-out: `JARVIS_BROWSER_PRELAUNCH_DISABLE=1` skips the whole
    hook and proceeds straight to the subagent — useful for testing
    the bare bridge path or if the user is managing Chrome manually.
    """
    if os.environ.get("JARVIS_BROWSER_PRELAUNCH_DISABLE") == "1":
        return None

    # ── Step 1: ensure Chrome + extension are up ─────────────────
    connected = await _bridge_ext_connected()
    if not connected:
        logger.info(
            "[browser.pre_transfer] extension not connected; launching Chrome"
        )
        launched = await _launch_chrome()
        if not launched:
            return (
                "Couldn't launch Chrome (setsid/google-chrome failed). "
                "Open Chrome manually and try again."
            )
        start = time.monotonic()
        deadline = start + _PRELAUNCH_WAIT_S
        fast_until = start + _PRELAUNCH_POLL_FAST_FOR_S
        next_log_at = start + 5.0  # heartbeat log every ~5s during the wait
        while time.monotonic() < deadline:
            now = time.monotonic()
            poll = (
                _PRELAUNCH_POLL_FAST_S if now < fast_until
                else _PRELAUNCH_POLL_SLOW_S
            )
            await asyncio.sleep(poll)
            if await _bridge_ext_connected():
                elapsed = time.monotonic() - start
                logger.info(
                    f"[browser.pre_transfer] extension connected after "
                    f"{elapsed:.2f}s"
                )
                connected = True
                break
            if time.monotonic() >= next_log_at:
                logger.info(
                    f"[browser.pre_transfer] still waiting for extension "
                    f"({time.monotonic() - start:.1f}s elapsed of "
                    f"{_PRELAUNCH_WAIT_S}s)"
                )
                next_log_at += 5.0
        if not connected:
            elapsed = time.monotonic() - start
            logger.warning(
                f"[browser.pre_transfer] extension never connected within "
                f"{elapsed:.1f}s; bailing"
            )
            return (
                "Chrome is starting but the extension hasn't registered yet — "
                "give it a few more seconds and ask again."
            )

    # ── Step 2: pre-navigate if the request is an "Open X" intent ──
    #
    # When the supervisor's `request` clearly names a destination (a
    # known site OR an explicit URL OR a bare domain), navigate
    # DIRECTLY here so the subagent's first LLM call sees Chrome
    # already on the target page. This eliminates the ~22s confab
    # latency caught live 2026-05-13 01:11-01:12 (subagent LLM
    # emitted task_done with no real tool call, got REFUSED, retried,
    # eventually navigated — user had already asked again by then).
    #
    # Skipping pre-navigate when:
    #   - request is empty (caller just wanted Chrome up — fine)
    #   - request is vague ("do something in the browser") — let the
    #     subagent figure it out
    #   - opted out via env
    if os.environ.get("JARVIS_BROWSER_PRENAV_DISABLE") == "1":
        return None
    if not request:
        return None
    target = _resolve_open_intent(request)
    if target is None:
        return None
    logger.info(
        f"[browser.pre_transfer] pre-navigating to {target} (from "
        f"request {request[:80]!r})"
    )
    ok = await _bridge_navigate(target)
    if not ok:
        # Don't abort the handoff on pre-nav failure — let the subagent
        # try (it has retries built in via the tool gate).
        logger.warning(
            f"[browser.pre_transfer] pre-navigate to {target} failed; "
            f"letting the subagent attempt it"
        )
        return None
    # Successful pre-nav → also focus Chrome so the user sees the page.
    await _bridge_focus_chrome()
    return None


def register_browser() -> None:
    """Register the browser subagent. `enabled=True` ships it live —
    the bridge endpoint and the extension command channel were both
    completed in earlier phases of the extension migration.

    The `pre_transfer` hook (`_ensure_chrome_extension_connected`)
    auto-launches Chrome if the extension isn't connected, so the
    handoff doesn't fail when Chrome happens to be cold. Added
    2026-05-13 after a live failure where JARVIS told the user "the
    browser extension isn't connected" instead of just opening it.
    """
    register(HandoffSubagent(
        name="browser",
        transfer_tool="transfer_to_browser",
        when_to_use=_BROWSER_WHEN,
        instructions=BROWSER_INSTRUCTIONS,
        tool_factory=_browser_tools,
        ack_phrase=ACK_BROWSER,
        # 2026-05-02: dropped 12 → 4. The 12-turn chat_ctx was
        # poisoning the subagent — recall seeded prior hallucinated
        # successes ("A new tab is open, sir." with no tool fired)
        # and the LLM pattern-matched against them, producing fresh
        # confabulations. 4 turns = the user's request + minimal
        # immediate context, no historical pollution.
        max_history_items=4,
        enabled=True,
        pre_transfer=_ensure_chrome_extension_connected,
    ))

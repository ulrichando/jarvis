"""Browser specialist v2 — wraps the open-source `browser-use` agent
loop instead of the 25 ext_* DOM tools + Chrome extension.

Coexists with the legacy `browser` specialist (registered alongside,
both `enabled=True`). The supervisor's routing prompt picks v2 for
multi-step web tasks ("log in, fill this form, post X") and falls back
to the legacy specialist for single-DOM-action work ("just navigate",
"just screenshot the page").

To DISABLE: set `enabled=False` in register_browser_v2() below — or
unset GROQ_API_KEY / DEEPSEEK_API_KEY (jarvis_browser_v2.is_available()
returns False, and we register disabled).
"""
from __future__ import annotations

from .registry import SpecialistSpec, register


BROWSER_V2_INSTRUCTIONS = """\
You are JARVIS's browser specialist (v2). The supervisor handed control
to you because the user wants something done in the browser that takes
more than one DOM action — login + fill form + submit, multi-page
navigation, extract-then-act, etc.

YOUR ONE JOB: call `browser_task_v2(task)` ONCE with a complete plain-
English description of the goal. browser-use handles the rest — it
plans the steps, drives the browser, and returns a summary. Then call
`task_done(summary)` to hand back to the supervisor.

═══ ABSOLUTE RULES ═══

1. **ONE CALL.** browser_task_v2 is autonomous. Don't try to break
   the task down yourself; pass the whole goal in. The internal agent
   loop handles planning + retries + verification.

2. **PASS THE GOAL VERBATIM.** Don't rephrase or "improve" the user's
   request. Pass what they said. browser-use will interpret it.

3. **NEVER claim success without a tool result proving it.** Same
   rule as the legacy browser specialist. If browser_task_v2 returns
   an error, voice the error truthfully — don't pretend it worked.

4. **DESTRUCTIVE-VERB GUARD.** If the user's request is destructive
   (post, send, buy, delete) AND the supervisor didn't already confirm
   with the user, call task_done with "needs confirmation: <action>"
   and let the supervisor handle the voice prompt. Don't post/send
   on a transcribed background-noise utterance.

═══ TOOLS YOU HAVE ═══

**browser_task_v2(task)** — autonomous multi-step browser agent.
  Returns one-paragraph summary of what landed, or an error string.
  ~10-25s typical wall time. Attaches to the user's signed-in Chrome
  if it's running with --remote-debugging-port=9222; otherwise spawns
  a fresh logged-out Chromium.

**task_done(summary)** — REQUIRED when done. Pass through whatever
browser_task_v2 returned (or a one-line error if it failed).

═══ EXAMPLES ═══

User: "log in to gmail and tell me my unread count"
You: browser_task_v2("log in to Gmail and report the unread count")
You: task_done("<summary>")

User: "post 'gm' on twitter"
You: task_done("needs confirmation: post 'gm' on twitter")
[supervisor confirms with user; on confirm, supervisor re-handsoff]
You (re-entry): browser_task_v2("post the tweet 'gm' on twitter")
You: task_done("Posted 'gm' to Twitter, sir.")

User: "what's the weather on weather.com for Columbus"
You: browser_task_v2("go to weather.com, look up the current weather
                      for Columbus Ohio, return the temperature and
                      conditions")
You: task_done("<summary>")
"""


def _browser_v2_tools() -> list:
    """Lazy import so browser-use's heavy startup doesn't block
    registry import. `task_done` is auto-attached by RegistrySpecialist
    — don't import it (regression captured live 2026-05-01: ImportError
    crashed the handoff)."""
    from jarvis_browser_v2 import browser_task_v2
    return [browser_task_v2]


_BROWSER_V2_WHEN = (
    "Use for MULTI-STEP web tasks: log in to a site, fill a form and "
    "submit, navigate through several pages, extract data and then act, "
    "research something on a specific website. Powered by the open-source "
    "browser-use agent — runs Groq llama-3.3-70b internally to plan and "
    "drive the browser autonomously. For single-shot DOM actions (just "
    "navigate, just screenshot), use transfer_to_browser instead — that "
    "one is faster for one-action work."
)


def register_browser_v2() -> None:
    """Register the v2 browser specialist. Idempotent.

    Auto-disables itself if browser_task_v2 can't run (no GROQ /
    DeepSeek key, or browser-use import fails). That way registering
    can't crash agent startup; the specialist just doesn't appear
    in the supervisor's tool list when its deps are missing.
    """
    # 2026-05-02: hard-disabled. Three independent failures stack on
    # every invocation:
    #   (1) CDP attach to localhost:9222 fails (user's Chrome isn't
    #       launched with --remote-debugging-port). browser-use then
    #       spawns a FRESH Chromium — visible to the user as "another
    #       Chrome window," exactly what they complained about.
    #   (2) Groq llama-3.3-70b rejects browser-use's
    #       response_format=json_schema with HTTP 400 (model doesn't
    #       support structured outputs).
    #   (3) jarvis_browser_v2.py:169 has a `TypeError: 'method' object
    #       is not subscriptable` — `actions[-1]` where `actions` is
    #       the bound method `agent.history.action_results`, not a list.
    # Until those three bugs are fixed and the user's Chrome is
    # configured with --remote-debugging-port=9222, leave it OFF and
    # let the supervisor route browser work to the regular `browser`
    # specialist (37 ext_* tools driving the user's real Chrome via
    # the jarvis-screen extension — DOES type, click, scroll for real).
    enabled = False

    register(SpecialistSpec(
        name="browser_v2",
        transfer_tool="transfer_to_browser_v2",
        when_to_use=_BROWSER_V2_WHEN,
        instructions=BROWSER_V2_INSTRUCTIONS,
        tool_factory=_browser_v2_tools,
        ack_phrase="Right away, sir.",
        max_history_items=4,
        enabled=enabled,
    ))

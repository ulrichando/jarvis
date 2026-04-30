"""Browser-task tool for JARVIS — wraps browser-use over Groq.

Exposes a single @function_tool, `browser_task`, that JARVIS can call
to drive a real Chrome browser through multi-step web work: log in,
navigate, fill forms, scroll feeds, click checkout, etc.

Architecture decisions:

1. **Dedicated profile.** We point browser-use at ~/.jarvis/browser-profile
   instead of the user's main Default profile. This isolates JARVIS's
   automated sessions from the user's manual browsing — no cookie races,
   no session conflicts, no risk of an agent crash leaving the user's
   real Chrome in a weird state. The user sets this up once by logging
   into the sites they want JARVIS to use (Twitter/X, Gmail, Amazon,
   etc.) under this profile via `chrome --user-data-dir=~/.jarvis/browser-profile`.

2. **Groq LLM, no vision.** browser-use uses DOM-mode by default when
   the model isn't multi-modal. Groq's llama-3.3-70b-versatile is text-only
   and free-tier, so we route through it. If vision becomes worth the
   token cost, swap in llama-4-scout (also Groq, multimodal).

3. **Two-step destructive-action gate.** Tasks containing verbs like
   "delete", "buy", "purchase", "send", "post", "transfer" go through
   a CONFIRM-then-execute pattern: the first call returns a confirmation
   prompt the LLM must voice; the user must explicitly approve via a
   follow-up turn that re-calls the tool with `confirmed=True`. Without
   this gate JARVIS may mis-route ambient TV / family talk into a
   purchase or social-media action.

4. **Hard timeout.** Browser tasks can wedge on a CAPTCHA or popup.
   We cap at 120 s. If the task times out, the user gets told and can
   re-issue with refined instructions.

5. **Profile lifecycle.** The browser session opens fresh per task and
   closes on completion — so the user can see what's happening and
   nothing keeps their CPU/mic resources tied up after.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from livekit.agents import function_tool

logger = logging.getLogger("jarvis-agent.browser")

# Where the JARVIS-only Chrome profile lives. Created on first launch
# if missing. The user can pre-populate it by running
#   /usr/bin/google-chrome --user-data-dir=$HOME/.jarvis/browser-profile
# and logging into the sites they want JARVIS to access.
_PROFILE_DIR = Path.home() / ".jarvis" / "browser-profile"
_CHROME_BIN = "/usr/bin/google-chrome"

# Verbs that indicate a destructive / costly / public-facing action.
# A task containing any of these requires explicit user confirmation
# (via a follow-up tool call with confirmed=True) before executing.
_DESTRUCTIVE_RE = re.compile(
    r"\b(delete|remove|unfollow|unfriend|block|"
    r"buy|purchase|order|checkout|pay|transfer|send|wire|"
    r"post|tweet|publish|share|email|reply|comment|"
    r"cancel|unsubscribe|deactivate|close\s+account)\b",
    re.IGNORECASE,
)

# Hard timeout for any single browser task. CAPTCHAs, anti-bot challenges,
# and unbounded "scroll forever" tasks are why this exists.
_TASK_TIMEOUT_S = 120.0


def _make_browser():
    """Construct a fresh Browser session bound to the JARVIS profile."""
    from browser_use import Browser
    from browser_use.browser.profile import BrowserProfile

    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    profile = BrowserProfile(
        user_data_dir=str(_PROFILE_DIR),
        executable_path=_CHROME_BIN,
        headless=False,        # show what's happening so the user can intervene
        profile_directory="Default",
    )
    return Browser(browser_profile=profile)


def _make_llm():
    """Build a Groq-backed LLM via the OpenAI-compatible API."""
    from browser_use.llm import ChatOpenAI

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY missing — browser_task can't run")

    # llama-3.3-70b is the best free-tier tool-using text model on Groq.
    # Switch to meta-llama/llama-4-scout-17b-16e-instruct if vision is
    # needed (DOM-mode can fail on heavily JS-rendered sites).
    return ChatOpenAI(
        model="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
        api_key=api_key,
    )


@function_tool
async def browser_task(task: str, confirmed: bool = False) -> str:
    """Drive a real Chrome browser to complete a multi-step web task.

    Use for things that require navigation, login state, form filling,
    or content interaction beyond just opening a URL — examples:
    "check my gmail and summarize the unread", "post 'hello' on Twitter",
    "find me cheap flights from SFO to NYC next week", "scroll through
    LinkedIn and tell me about the top 3 posts", "log in to my Amazon
    and reorder the dog food I bought last month".

    For just OPENING a URL or app, use the bash tool — it's faster.

    Args:
        task: A clear natural-language description of what to do. Be
              specific about target site, account, action. E.g.
              "On youtube.com, search for 'lofi hip hop' and play the
              first result" not just "play music".
        confirmed: Set True ONLY after explicit user confirmation when
                   the task contains a destructive verb (delete, buy,
                   post, send, cancel, etc.). On a first call to such
                   a task, leave this False; the tool will return a
                   confirmation prompt that you must voice and wait
                   for the user to say "yes" / "do it" / "confirm",
                   then re-call with confirmed=True.

    Returns:
        The agent's final result text, or a confirmation prompt if
        the task is destructive and not yet confirmed.
    """
    task = (task or "").strip()
    if not task:
        return "browser_task needs a description of what to do, sir."

    # Destructive-action gate: ask first, execute on the second call.
    if _DESTRUCTIVE_RE.search(task) and not confirmed:
        return (
            f"That task includes a destructive action. About to do: "
            f"'{task}'. Voice this back to the user and ask them to "
            f"confirm explicitly before re-calling browser_task with "
            f"confirmed=True. Do not assume background voices count "
            f"as confirmation."
        )

    try:
        from browser_use import Agent
    except Exception as e:
        return f"browser-use not installed properly: {e}"

    try:
        llm = _make_llm()
    except Exception as e:
        return f"browser_task config error: {e}"

    browser = _make_browser()
    agent = Agent(task=task, llm=llm, browser=browser)
    logger.info(f"[browser_task] starting (confirmed={confirmed}): {task[:120]!r}")

    try:
        result = await asyncio.wait_for(agent.run(), timeout=_TASK_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.warning(f"[browser_task] timed out after {_TASK_TIMEOUT_S}s: {task[:80]!r}")
        return (
            f"Task ran past the {int(_TASK_TIMEOUT_S)}-second cap, sir. "
            f"Either the site needed a CAPTCHA, anti-bot challenge, or "
            f"the task was too open-ended. Try a more specific request."
        )
    except Exception as e:
        logger.exception(f"[browser_task] failed: {e}")
        return f"browser_task failed: {e}"

    # The Agent.run() return type varies across versions; coerce to str.
    text = str(result)
    logger.info(f"[browser_task] done ({len(text)} chars)")
    return text[:2000]  # cap so a verbose agent reply doesn't flood TTS

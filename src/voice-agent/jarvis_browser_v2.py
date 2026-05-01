"""Browser-task v2 — wraps the open-source `browser-use` agent loop as
a single `@function_tool` for JARVIS's browser specialist.

Replaces the 25 ext_* DOM-action tools + Chrome extension + Bun bridge
with one library call. browser-use:
  - drives Chromium via the Chrome DevTools Protocol (no extension)
  - emits structured DOM-element actions (click index 7, type into 12)
    rather than asking the LLM to guess CSS selectors
  - terminates cleanly (`AgentHistoryList.is_done()`) on task completion
  - supports our existing Groq/DeepSeek API keys natively
    (browser_use.llm.groq.ChatGroq + browser_use.llm.deepseek.ChatDeepSeek)

CDP attach: if the user's Chrome is running with `--remote-debugging-port=9222`,
the agent attaches to that browser (preserves the signed-in Default profile,
cookies, history). Otherwise browser-use spawns a fresh Chromium for the
task (works, but you're logged out everywhere).

Cost / latency: each turn is one Groq llama-3.3-70b call (~$0 on tier 1)
with one DOM screenshot + one structured-output completion. Typical
multi-step task (login + navigate + extract): 5-12 turns, ~10-25s wall.
Same Groq token budget as the existing TASK route.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from livekit.agents.llm import function_tool

logger = logging.getLogger("jarvis-browser-v2")


# CDP endpoint we try to attach to first. If unreachable, browser-use
# spawns its own headed Chromium. The user can launch their daily Chrome
# with `--remote-debugging-port=9222` to keep their signed-in profile.
_CDP_URL = os.environ.get("JARVIS_BROWSER_CDP_URL", "http://localhost:9222")

# Hard cap on agent loop iterations — prevents runaway token spend if a
# task is impossible. A 12-step ceiling covers login + 3 nav + 3 form +
# extract + verify, with margin.
_MAX_STEPS = int(os.environ.get("JARVIS_BROWSER_V2_MAX_STEPS", "12"))


def _build_llm():
    """Pick the LLM browser-use will reason with. Groq llama-3.3-70b is
    the project's default TASK model — fast, cheap, supports the
    structured-output JSON schema browser-use requires. DeepSeek as
    fallback if Groq's key is missing.

    Returns a `BaseChatModel` per browser_use's protocol.
    """
    if os.environ.get("GROQ_API_KEY"):
        from browser_use.llm.groq.chat import ChatGroq
        return ChatGroq(
            model=os.environ.get("JARVIS_BROWSER_V2_MODEL", "llama-3.3-70b-versatile"),
            api_key=os.environ["GROQ_API_KEY"],
            temperature=0.0,
        )
    if os.environ.get("DEEPSEEK_API_KEY"):
        from browser_use.llm.deepseek.chat import ChatDeepSeek
        return ChatDeepSeek(
            model="deepseek-chat",
            api_key=os.environ["DEEPSEEK_API_KEY"],
            temperature=0.0,
        )
    raise RuntimeError(
        "browser_task_v2 requires GROQ_API_KEY or DEEPSEEK_API_KEY in env"
    )


async def _build_session():
    """Return a `BrowserSession` — attached to the user's running Chrome
    if CDP is reachable, otherwise a fresh Chromium. Caller must
    `await session.kill()` when the agent finishes; we do that in the
    finally block of `browser_task_v2`.
    """
    from browser_use.browser.profile import BrowserProfile
    from browser_use.browser.session import BrowserSession

    # Try CDP attach first. Wrap the connection check in a tight timeout
    # so we don't add seconds of latency on the cold path.
    try:
        import aiohttp
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=0.5)
        ) as s:
            async with s.get(f"{_CDP_URL}/json/version") as r:
                if r.status == 200:
                    profile = BrowserProfile(
                        # CDP attach keeps the user's signed-in profile +
                        # cookies + history. That's the whole point of
                        # this path vs. spawning fresh Chromium.
                        cdp_url=_CDP_URL,
                    )
                    logger.info(f"[browser-v2] attaching to user Chrome via {_CDP_URL}")
                    return BrowserSession(browser_profile=profile)
    except Exception as e:
        logger.info(f"[browser-v2] CDP attach failed ({e}); spawning fresh Chromium")

    # Fresh-Chromium fallback. browser-use will download + launch its
    # own Chromium headlessly. User is logged out everywhere — fine for
    # public-page tasks, useless for "post on Twitter".
    profile = BrowserProfile(headless=False)
    return BrowserSession(browser_profile=profile)


@function_tool
async def browser_task_v2(task: str) -> str:
    """Run a multi-step web task autonomously via the browser-use agent.

    Use for ANY task that needs more than one DOM action: login flows,
    fill-this-form-and-submit, multi-page navigation, extract+act,
    "find the cheapest X on this site". Single-shot DOM operations
    (just navigate / just click) — keep using the legacy ext_* tools.

    The agent attaches to the user's running Chrome (with
    --remote-debugging-port=9222) when available, preserving their
    signed-in profile. Otherwise it spawns a fresh Chromium.

    Args:
        task: Plain-language goal. Example: "log in to my Gmail and
              tell me how many unread emails I have".

    Returns:
        One-paragraph summary of what was accomplished + any data
        extracted, or a one-line error if the task failed.
    """
    # Lazy imports — keeps livekit + browser-use heavy initialization out
    # of the registry-import path.
    from browser_use.agent.service import Agent

    try:
        llm = _build_llm()
    except Exception as e:
        return f"Browser v2 unavailable: {e}"

    session = None
    try:
        session = await _build_session()
        agent = Agent(
            task=task,
            llm=llm,
            browser_session=session,
        )
        history = await asyncio.wait_for(
            agent.run(max_steps=_MAX_STEPS),
            timeout=120,
        )
        # AgentHistoryList exposes .is_done() and .final_result() in
        # browser-use 0.12.x; the exact attr names changed across
        # versions, so probe defensively.
        final_text = None
        for attr in ("final_result", "extracted_content"):
            fn = getattr(history, attr, None)
            if callable(fn):
                try:
                    val = fn()
                    if val:
                        final_text = str(val)
                        break
                except Exception:
                    continue
        if final_text is None:
            # Last resort — string repr of the last action's result.
            actions = getattr(history, "action_results", None) or []
            if actions:
                last = actions[-1]
                final_text = getattr(last, "extracted_content", None) or str(last)
        return final_text or "Task completed (no extracted content)."
    except asyncio.TimeoutError:
        return "Browser v2 timed out after 120 seconds."
    except Exception as e:
        logger.exception("[browser-v2] agent failed")
        return f"Browser v2 failed: {type(e).__name__}: {e}"
    finally:
        if session is not None:
            try:
                await session.kill()
            except Exception:
                pass


def is_available() -> bool:
    """True if browser_task_v2 has the keys + libs it needs to run.
    Used by the specialist registry to decide whether to enable
    browser_v2 at startup."""
    if not (os.environ.get("GROQ_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")):
        return False
    try:
        from browser_use.agent.service import Agent  # noqa: F401
        return True
    except Exception:
        return False

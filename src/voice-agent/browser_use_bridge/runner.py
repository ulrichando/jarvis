#!/usr/bin/env python3
"""Standalone browser_use runner — executed BY the isolated venv interpreter.

Run as a subprocess from ``tools/browser.py`` with the isolated venv's Python
(``~/.jarvis/browser-use-venv/bin/python``). It imports ONLY ``browser_use``
plus the stdlib — never anything from the voice-agent package — so that the
voice ``.venv`` (which has no ``browser_use``) is never required to import it.

Protocol (line-oriented JSON over stdin/stdout):

  stdin  : one JSON object  {"task": "...", "max_steps": 25, "headless": true,
                             "cdp_url": "..."}   # cdp_url optional
  stdout : one JSON object  {"ok": true, "result": "...", "steps": N}
                       or   {"ok": false, "error": "..."}

When ``cdp_url`` is present the agent attaches to that already-running REMOTE
browser over CDP (e.g. a cloud session) instead of launching a local browser.
When it is absent/empty the local-launch path runs unchanged.

stdout carries the result line and NOTHING ELSE — browser_use's own logging is
forced to stderr below so it can't corrupt the JSON the parent parses. All
exceptions (bad input, missing key, agent crash) are caught and reported as a
JSON error object; the process always exits 0 with a parseable line so the
parent never has to interpret a non-zero exit code or a traceback on stderr.

LLM selection is env-driven, first key wins:
  ANTHROPIC_API_KEY -> Claude   (ChatAnthropic)
  OPENAI_API_KEY    -> GPT       (ChatOpenAI)
  GEMINI_API_KEY / GOOGLE_API_KEY -> Gemini (ChatGoogle)
"""
from __future__ import annotations

# Force browser_use's logging onto stderr BEFORE importing it, so verbose
# agent/step logs never leak into our stdout JSON line. browser_use reads this
# env at import time in its logging_config.
import os as _os

_os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "result")

import asyncio
import json
import logging
import sys
import traceback
from typing import Any, Optional


# ---------------------------------------------------------------------------
# stdout discipline
# ---------------------------------------------------------------------------
# Anything written to the real stdout other than our final JSON line would
# break the parent's parser. Redirect the logging root + any stray prints from
# dependencies to stderr, and keep a private handle to the genuine stdout for
# the single result emission at the very end.
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr  # any library print() now lands on stderr
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)


def _emit(payload: dict) -> None:
    """Write exactly one compact JSON line to the genuine stdout, then flush."""
    _REAL_STDOUT.write(json.dumps(payload, ensure_ascii=False))
    _REAL_STDOUT.write("\n")
    _REAL_STDOUT.flush()


# ---------------------------------------------------------------------------
# LLM selection (env-driven; first available key wins)
# ---------------------------------------------------------------------------
# Default model per provider — modest, fast, vision-capable models suited to a
# voice assistant's "do a quick web task" use case. Overridable via env.
_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5-20250929",
    "openai": "gpt-4.1-mini",
    "google": "gemini-2.0-flash",
}


def _build_llm():
    """Return a configured browser_use Chat* LLM, or raise RuntimeError.

    Picks the provider by the first present API key. The model id can be
    overridden with ``JARVIS_BROWSER_MODEL``; otherwise a sane per-provider
    default is used.
    """
    model_override = _os.environ.get("JARVIS_BROWSER_MODEL", "").strip() or None

    anthropic_key = _os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        from browser_use import ChatAnthropic

        return ChatAnthropic(
            model=model_override or _DEFAULT_MODELS["anthropic"],
            api_key=anthropic_key,
        )

    openai_key = _os.environ.get("OPENAI_API_KEY", "").strip()
    if openai_key:
        from browser_use import ChatOpenAI

        return ChatOpenAI(
            model=model_override or _DEFAULT_MODELS["openai"],
            api_key=openai_key,
        )

    google_key = (
        _os.environ.get("GEMINI_API_KEY", "").strip()
        or _os.environ.get("GOOGLE_API_KEY", "").strip()
    )
    if google_key:
        from browser_use import ChatGoogle

        return ChatGoogle(
            model=model_override or _DEFAULT_MODELS["google"],
            api_key=google_key,
        )

    raise RuntimeError(
        "no LLM API key set (need one of ANTHROPIC_API_KEY / OPENAI_API_KEY / "
        "GEMINI_API_KEY / GOOGLE_API_KEY)"
    )


# ---------------------------------------------------------------------------
# Task run
# ---------------------------------------------------------------------------


async def _run_task(
    task: str, max_steps: int, headless: bool, cdp_url: Optional[str] = None
) -> dict:
    """Drive a browser_use Agent through *task*; return a result payload dict.

    When *cdp_url* is set, attach to an already-running REMOTE browser over CDP
    (e.g. a cloud Browserbase/Firecrawl session) instead of launching a local
    one. When it is None the local-launch path runs unchanged. The CDP wiring is
    guarded: if the installed browser_use's BrowserProfile signature doesn't
    accept ``cdp_url`` it raises a clear error (caught by ``main`` and emitted as
    a JSON error object) rather than crashing.
    """
    from browser_use import Agent, BrowserProfile

    llm = _build_llm()
    if cdp_url:
        # Remote CDP attach. headless/chromium_sandbox are irrelevant — the
        # browser is already running cloud-side; we only connect to it. The
        # BrowserProfile(cdp_url=...) kwarg is browser_use 0.12; if the installed
        # version differs this raises TypeError, which main() reports as JSON.
        try:
            profile = BrowserProfile(cdp_url=cdp_url)
        except TypeError as exc:
            raise RuntimeError(
                f"installed browser_use does not accept BrowserProfile(cdp_url=...): {exc}"
            ) from exc
    else:
        # Local launch. Headless config lives on BrowserProfile in browser_use
        # 0.12 (Agent has no direct headless kwarg). chromium_sandbox=False
        # avoids namespace-sandbox friction on locked-down hosts; a fresh
        # profile keeps runs hermetic.
        profile = BrowserProfile(headless=headless, chromium_sandbox=False)
    agent = Agent(task=task, llm=llm, browser_profile=profile)

    history = await agent.run(max_steps=max_steps)

    final = history.final_result()
    if final is None or (isinstance(final, str) and not final.strip()):
        final = "(no textual result returned by the browser agent)"

    return {
        "ok": True,
        "result": str(final),
        "steps": int(history.number_of_steps()),
    }


def _read_request() -> dict:
    """Parse the single stdin JSON object; raise ValueError on malformed input."""
    raw = sys.stdin.read()
    if not raw or not raw.strip():
        raise ValueError("empty stdin (expected a JSON task object)")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("stdin JSON must be an object")
    return data


def main() -> None:
    try:
        req = _read_request()
        task = req.get("task")
        if not isinstance(task, str) or not task.strip():
            raise ValueError("request missing required non-empty 'task' string")

        raw_steps = req.get("max_steps", 25)
        try:
            max_steps = int(raw_steps)
        except (TypeError, ValueError):
            max_steps = 25
        if max_steps < 1:
            max_steps = 1

        headless = req.get("headless", True)
        headless = True if headless is None else bool(headless)

        # Optional remote-CDP attach. Unset/empty → local launch (unchanged).
        raw_cdp = req.get("cdp_url")
        cdp_url = raw_cdp.strip() if isinstance(raw_cdp, str) and raw_cdp.strip() else None

        result = asyncio.run(_run_task(task.strip(), max_steps, headless, cdp_url))
        _emit(result)
    except Exception as exc:  # noqa: BLE001 — always report as JSON, never crash out
        detail = f"{type(exc).__name__}: {exc}".strip()
        # Keep a short trailing snippet of the traceback for post-mortem on
        # stderr; the parent only ever parses stdout.
        traceback.print_exc(file=sys.stderr)
        _emit({"ok": False, "error": detail})


if __name__ == "__main__":
    main()

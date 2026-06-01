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
import re
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

# Max chars of browser-use's step log to keep for the result JSON's
# ``stderr_tail`` (legible-failure surfacing — see plan Task 3, Step 3.6).
_STDERR_TAIL_CHARS = 2_000


class _StderrTee:
    """Write-through wrapper on the real stderr that also keeps a bounded tail.

    Everything still lands on the real stderr (so the parent's stderr pipe — and
    the no-stdout fallback path in ``tools/browser.py`` — see the full log), but
    the last ``_STDERR_TAIL_CHARS`` chars are retained in-process so the runner
    can embed them in its result JSON when a task fails. Bounded, so a chatty
    browser-use run can't grow memory without limit.
    """

    def __init__(self, underlying) -> None:
        self._underlying = underlying
        self._buf = ""

    def write(self, s) -> int:
        text = s if isinstance(s, str) else str(s)
        self._buf += text
        if len(self._buf) > _STDERR_TAIL_CHARS:
            self._buf = self._buf[-_STDERR_TAIL_CHARS:]
        return self._underlying.write(text)

    def flush(self) -> None:
        self._underlying.flush()

    def tail(self) -> str:
        return self._buf.strip()

    def __getattr__(self, name):
        # Delegate isatty/fileno/encoding/etc. to the wrapped stream.
        return getattr(self._underlying, name)


sys.stderr = _StderrTee(sys.stderr)
sys.stdout = sys.stderr  # any library print() now lands on stderr (tee'd)
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)


def _stderr_tail() -> str:
    """Return the retained tail of the runner's stderr/step log (best-effort)."""
    try:
        tee = sys.stderr
        if isinstance(tee, _StderrTee):
            return tee.tail()
    except Exception:  # noqa: BLE001 — tail capture is diagnostic, never load-bearing
        pass
    return ""


def _emit(payload: dict) -> None:
    """Write exactly one compact JSON line to the genuine stdout, then flush."""
    _REAL_STDOUT.write(json.dumps(payload, ensure_ascii=False))
    _REAL_STDOUT.write("\n")
    _REAL_STDOUT.flush()


# ---------------------------------------------------------------------------
# LLM selection (env-driven; first available key wins)
# ---------------------------------------------------------------------------
# Default model per provider — modest, fast models suited to a voice
# assistant's "do a quick web task" use case. Overridable via env.
# Priority: first present key wins. Kimi is preferred for browser tasks
# (no extended-thinking issues with tool_choice); Anthropic models may
# require explicit thinking:disabled when tool_choice=any is used.
_DEFAULT_MODELS = {
    "kimi": "kimi-k2.6",
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-haiku-4-5",
    "google": "gemini-2.0-flash",
}


def _env_int(name: str, default: int) -> int:
    """Read a positive int env var, falling back to *default* on absent/garbage."""
    raw = _os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


# Reliability knobs — only params CONFIRMED present on browser-use 0.12.9's
# Agent.__init__ (see browser_use_bridge/PARAMS_0_12_6.md). max_steps is NOT
# here: it is an Agent.run(max_steps=...) arg, passed separately below.
#   max_failures   (int, default 5)   — bound retries; no infinite loops
#   step_timeout   (int seconds, 180) — per-step ceiling; no silent hangs
#   llm_timeout    (int seconds)      — per-LLM-call ceiling
#   use_vision='auto'                 — vision only when the DOM index fails
#   calculate_cost=True               — record per-task $ (feeds P3 telemetry)
#   fallback_llm                      — next available provider after the primary
_AGENT_MAX_FAILURES = _env_int("JARVIS_BROWSER_MAX_FAILURES", 3)
_AGENT_STEP_TIMEOUT_S = _env_int("JARVIS_BROWSER_STEP_TIMEOUT_S", 60)
_AGENT_LLM_TIMEOUT_S = _env_int("JARVIS_BROWSER_LLM_TIMEOUT_S", 45)


def _available_llms() -> list:
    """Return configured browser_use Chat* LLMs in provider-priority order.

    Priority: Kimi → OpenAI → Anthropic → Google,
    one LLM per present API key. The first entry is the primary; a second
    entry (if any) is the ``fallback_llm`` for the Agent. The model id can be
    overridden with ``JARVIS_BROWSER_MODEL`` (applied to the PRIMARY only;
    the fallback keeps its provider default so it's a genuinely different rung).
    Empty list means no key is set.
    """
    model_override = _os.environ.get("JARVIS_BROWSER_MODEL", "").strip() or None
    llms: list = []

    # Kimi (OpenAI-compatible via Moonshot) — preferred for browsing
    # (no extended-thinking tooll_choice conflict).
    kimi_key = _os.environ.get("KIMI_API_KEY", "").strip()
    if kimi_key:
        from browser_use import ChatOpenAI

        model = _DEFAULT_MODELS["kimi"]
        if model_override and not llms:
            model = model_override
        llms.append(ChatOpenAI(
            model=model,
            api_key=kimi_key,
            base_url="https://api.moonshot.ai/v1",
            temperature=None,
            frequency_penalty=None,
        ))

    openai_key = _os.environ.get("OPENAI_API_KEY", "").strip()
    if openai_key:
        from browser_use import ChatOpenAI

        model = _DEFAULT_MODELS["openai"]
        if model_override and not llms:
            model = model_override
        llms.append(ChatOpenAI(model=model, api_key=openai_key))

    anthropic_key = _os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        from browser_use import ChatAnthropic

        model = _DEFAULT_MODELS["anthropic"]
        if model_override and not llms:
            model = model_override
        llms.append(ChatAnthropic(model=model, api_key=anthropic_key))

    google_key = (
        _os.environ.get("GEMINI_API_KEY", "").strip()
        or _os.environ.get("GOOGLE_API_KEY", "").strip()
    )
    if google_key:
        from browser_use import ChatGoogle

        model = _DEFAULT_MODELS["google"]
        if model_override and not llms:
            model = model_override
        llms.append(ChatGoogle(model=model, api_key=google_key))

    return llms


def _build_llm():
    """Return the PRIMARY browser_use Chat* LLM, or raise RuntimeError.

    Thin wrapper over ``_available_llms`` for callers that want only the
    primary (first present API key by priority).
    """
    llms = _available_llms()
    if not llms:
        raise RuntimeError(
            "no LLM API key set (need one of ANTHROPIC_API_KEY / OPENAI_API_KEY / "
            "GEMINI_API_KEY / GOOGLE_API_KEY)"
        )
    return llms[0]


# ---------------------------------------------------------------------------
# CAPTCHA detection
# ---------------------------------------------------------------------------

# URL substrings that indicate a CAPTCHA challenge page.
_CAPTCHA_URL_PATTERNS = re.compile(
    r"/(captcha|challenge|recaptcha|verify|human|security_check|"
    r"browser_check|blocked|denied|access_denied)/",
    re.I,
)

# Page text patterns that strongly suggest a CAPTCHA or bot-block.
_CAPTCHA_TEXT_PATTERNS = re.compile(
    r"(I['']m\s+not\s+a\s+robot|"
    r"verify\s+(you\s+are|your)\s+human|"
    r"complete\s+the\s+captcha|"
    r"unusual\s+traffic|"
    r"automated\s+(access|request|query|browser)|"
    r"enable\s+JavaScript.*cookie|"
    r"please\s+confirm\s+you\s+are\s+(human|not\s+a\s+robot)|"
    r"captcha|"
    r"recaptcha|"
    r"challenge\s+detected|"
    r"access\s+denied.*bot|"
    r"suspicious\s+activity|"
    r"too\s+many\s+requests)",
    re.I,
)


def _check_history_for_captcha(history) -> Optional[str]:
    """Check the AgentHistory for CAPTCHA / bot-block signals.

    Inspects visited URLs and a sample of page content for known CAPTCHA
    patterns. Returns a short human-readable hint when detected, or None
    when the history looks clean.

    Best-effort: false positives (e.g. a site mentioning "CAPTCHA" in
    its sign-up instructions) produce a hint, not a hard error — the
    caller decides how to act on it.
    """
    # 1. Check URLs from the step history.
    try:
        urls = history.urls()
    except Exception:
        urls = []
    for url in urls:
        if url and _CAPTCHA_URL_PATTERNS.search(url):
            return "CAPTCHA in URL"

    # 2. Check step action names for navigation failures.
    try:
        action_names = history.action_names()
    except Exception:
        action_names = []
    # If every navigation step failed or hit a challenge page, flag it.
    # (action_names includes page text context in browser_use 0.12+)

    # 3. Check extracted content for CAPTCHA text patterns.
    try:
        content = history.extracted_content()
    except Exception:
        content = []
    for snippet in content:
        if snippet and _CAPTCHA_TEXT_PATTERNS.search(snippet):
            return "CAPTCHA in page content"

    # 4. Check last few action outputs for error patterns matching blocks.
    try:
        action_results = history.action_results()
    except Exception:
        action_results = []
    for ar in (action_results or [])[-3:]:  # last 3 steps
        err = getattr(ar, "error", None) or ""
        if err and _CAPTCHA_TEXT_PATTERNS.search(err):
            return "CAPTCHA in step error"

    return None


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

    llms = _available_llms()
    if not llms:
        raise RuntimeError(
            "no LLM API key set (need one of ANTHROPIC_API_KEY / OPENAI_API_KEY / "
            "GEMINI_API_KEY / GOOGLE_API_KEY)"
        )
    llm = llms[0]
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

    # Reliability params — ONLY those confirmed present on 0.12.9's
    # Agent.__init__ (PARAMS_0_12_6.md). use_vision='auto' is in the
    # Union[bool, Literal['auto']] annotation. A fallback_llm is wired only when
    # a second provider key is actually present.
    agent_kwargs: dict = {
        "task": task,
        "llm": llm,
        "browser_profile": profile,
        "use_vision": "auto",
        "use_thinking": False,
        "max_failures": _AGENT_MAX_FAILURES,
        "step_timeout": _AGENT_STEP_TIMEOUT_S,
        "llm_timeout": _AGENT_LLM_TIMEOUT_S,
        "calculate_cost": True,
    }
    if len(llms) > 1:
        agent_kwargs["fallback_llm"] = llms[1]

    agent = Agent(**agent_kwargs)

    history = await agent.run(max_steps=max_steps)

    # CAPTCHA detection: check visited URLs and page content for challenge
    # patterns. When detected, report early so the caller can fall back to
    # computer_use (visible browser with user solving it) or try a different
    # approach. Non-fatal: if detection is uncertain, the task result still
    # carries through.
    captcha_hint = _check_history_for_captcha(history)
    if captcha_hint:
        logger.warning("browser_task: possible CAPTCHA detected (%s)", captcha_hint)

    final = history.final_result()
    if final is None or (isinstance(final, str) and not final.strip()):
        final = "(no textual result returned by the browser agent)"

    payload = {
        "ok": True,
        "result": str(final),
        "steps": _step_trace(history),
        "steps_count": int(history.number_of_steps()),
    }
    if captcha_hint:
        payload["captcha_hint"] = captcha_hint
    return payload


def _step_trace(history) -> list:
    """Build a per-step action trace from a browser_use AgentHistoryList.

    Returns a list of ``{"step_index", "action", "ok", "detail"}`` dicts —
    one per action browser_use took — so ``tools/browser.py`` can surface
    them into ``turn_telemetry.browser_task_steps`` for post-mortem
    debugging (Web-Nav Phase 1, Task 4). Best-effort: any accessor that the
    installed browser_use doesn't expose (or that raises) degrades to an
    empty trace rather than failing the task — the trace is observability,
    never load-bearing for the result itself.
    """
    try:
        names = list(history.action_names())
    except Exception:  # noqa: BLE001 — trace is diagnostic, never load-bearing
        return []
    # `errors()` returns one entry per step, None where the step succeeded.
    try:
        errors = list(history.errors())
    except Exception:  # noqa: BLE001
        errors = []
    trace: list = []
    for idx, action in enumerate(names):
        err = errors[idx] if idx < len(errors) else None
        ok = err is None
        detail = None if ok else str(err)[:1_000]
        trace.append(
            {
                "step_index": idx,
                "action": str(action) if action is not None else None,
                "ok": ok,
                "detail": detail,
            }
        )
    return trace


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
        # Print the traceback to (tee'd) stderr first so it's part of the tail,
        # then attach the captured stderr/step-log tail to the JSON so the parent
        # can surface a legible failure instead of a generic message.
        traceback.print_exc(file=sys.stderr)
        _emit({"ok": False, "error": detail, "stderr_tail": _stderr_tail()})


if __name__ == "__main__":
    main()

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

LLM selection is env-driven, first key wins in priority order:
  KIMI_API_KEY        -> Kimi (ChatOpenAI via Moonshot)
  OPENAI_API_KEY      -> GPT  (ChatOpenAI)
  ANTHROPIC_API_KEY   -> Claude (ChatAnthropic)
  GEMINI_API_KEY      -> Gemini (ChatGoogle)
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
# ``stderr_tail`` (legible-failure surfacing).
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
        return getattr(self._underlying, name)


sys.stderr = _StderrTee(sys.stderr)
sys.stdout = sys.stderr
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)


def _stderr_tail() -> str:
    try:
        tee = sys.stderr
        if isinstance(tee, _StderrTee):
            return tee.tail()
    except Exception:
        pass
    return ""


def _emit(payload: dict) -> None:
    _REAL_STDOUT.write(json.dumps(payload, ensure_ascii=False))
    _REAL_STDOUT.write("\n")
    _REAL_STDOUT.flush()


# ---------------------------------------------------------------------------
# LLM selection (env-driven; first available key wins)
# ---------------------------------------------------------------------------
# Priority: Kimi → OpenAI → Anthropic → Google.
# Kimi is preferred for browser tasks (no extended-thinking / structured-output
# issues). Anthropic models may have thinking conflicts with tool_choice=any.
_DEFAULT_MODELS = {
    "kimi": "kimi-k2.6",
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-haiku-4-5",
    "google": "gemini-2.0-flash",
}


def _env_int(name: str, default: int) -> int:
    raw = _os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


_AGENT_MAX_FAILURES = _env_int("JARVIS_BROWSER_MAX_FAILURES", 3)
_AGENT_STEP_TIMEOUT_S = _env_int("JARVIS_BROWSER_STEP_TIMEOUT_S", 60)
_AGENT_LLM_TIMEOUT_S = _env_int("JARVIS_BROWSER_LLM_TIMEOUT_S", 45)


def _available_llms() -> list:
    """Return configured browser_use Chat* LLMs in provider-priority order.

    Priority: Kimi → OpenAI → Anthropic → Google,
    one LLM per present API key. The first entry is the primary; a second
    entry (if any) is the ``fallback_llm`` for the Agent. The model id can be
    overridden with ``JARVIS_BROWSER_MODEL`` (applied to the PRIMARY only).
    Empty list means no key is set.
    """
    model_override = _os.environ.get("JARVIS_BROWSER_MODEL", "").strip() or None
    llms: list = []

    # Kimi (OpenAI-compatible via Moonshot).
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


# ---------------------------------------------------------------------------
# CAPTCHA detection
# ---------------------------------------------------------------------------

_CAPTCHA_URL_PATTERNS = re.compile(
    r"/(captcha|challenge|recaptcha|verify|human|security_check|"
    r"browser_check|blocked|denied|access_denied)/",
    re.I,
)

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
    try:
        urls = history.urls()
    except Exception:
        urls = []
    for url in urls:
        if url and _CAPTCHA_URL_PATTERNS.search(url):
            return "CAPTCHA in URL"

    try:
        content = history.extracted_content()
    except Exception:
        content = []
    for snippet in content:
        if snippet and _CAPTCHA_TEXT_PATTERNS.search(snippet):
            return "CAPTCHA in page content"

    try:
        action_results = history.action_results()
    except Exception:
        action_results = []
    for ar in (action_results or [])[-3:]:
        err = getattr(ar, "error", None) or ""
        if err and _CAPTCHA_TEXT_PATTERNS.search(err):
            return "CAPTCHA in step error"

    return None


# ---------------------------------------------------------------------------
# Task run
# ---------------------------------------------------------------------------


def _filter_supported_kwargs(agent_cls, kwargs: dict) -> dict:
    """Drop optional kwargs the installed browser_use ``Agent`` doesn't accept.

    The tuning knobs (``flash_mode``, ``fallback_llm``, ``max_actions_per_step``,
    ``sensitive_data``, ...) vary across browser_use versions. An unknown kwarg
    makes ``Agent(**kwargs)`` raise TypeError and fail the WHOLE task; dropping
    the knob (with a note on stderr, which lands in the failure tail) degrades
    gracefully instead. Required kwargs (task/llm/...) are always in the
    signature, so they pass through untouched. Returns ``kwargs`` unchanged when
    the signature can't be introspected or the Agent accepts ``**kwargs``.
    """
    import inspect

    try:
        params = inspect.signature(agent_cls.__init__).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return kwargs
    accepted = set(params)
    for key in [k for k in kwargs if k not in accepted]:
        kwargs.pop(key)
        print(
            f"[runner] installed browser_use Agent does not accept {key!r}; "
            "option dropped",
            file=sys.stderr,
        )
    return kwargs


async def _run_task(
    task: str, max_steps: int, headless: bool, cdp_url: Optional[str] = None,
    flash_mode: Optional[bool] = None,
    max_actions_per_step: Optional[int] = None,
    initial_actions: Optional[list] = None,
    sensitive_data: Optional[dict] = None,
) -> dict:
    from browser_use import Agent, BrowserProfile

    llms = _available_llms()
    if not llms:
        raise RuntimeError(
            "no LLM API key set (need one of KIMI_API_KEY / OPENAI_API_KEY / "
            "ANTHROPIC_API_KEY / GEMINI_API_KEY / GOOGLE_API_KEY)"
        )
    llm = llms[0]
    if cdp_url:
        try:
            profile = BrowserProfile(cdp_url=cdp_url)
        except TypeError as exc:
            raise RuntimeError(
                f"installed browser_use does not accept BrowserProfile(cdp_url=...): {exc}"
            ) from exc
    else:
        profile = BrowserProfile(headless=headless, chromium_sandbox=False)

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
    # Gap 5: flash_mode → skip LLM evaluation per step for simple tasks (~40% faster).
    if flash_mode:
        agent_kwargs["use_thinking"] = False
        # browser-use gained a dedicated flash_mode parameter in newer
        # versions; _filter_supported_kwargs drops it on older installs so
        # the task degrades instead of failing on an unknown kwarg.
        agent_kwargs["flash_mode"] = True
    # Gap 6: max_actions_per_step → batch multiple fields in one LLM step.
    if max_actions_per_step is not None:
        agent_kwargs["max_actions_per_step"] = int(max_actions_per_step)
    # Gap 7: initial_actions → pre-run browser commands before the first LLM call.
    if initial_actions:
        agent_kwargs["initial_actions"] = list(initial_actions)
    # Gap 8: sensitive_data → credentials/keys passed to the agent securely.
    if sensitive_data:
        agent_kwargs["sensitive_data"] = dict(sensitive_data)

    agent = Agent(**_filter_supported_kwargs(Agent, agent_kwargs))

    history = await agent.run(max_steps=max_steps)

    captcha_hint = _check_history_for_captcha(history)
    if captcha_hint:
        logging.getLogger("browser_use").warning(
            "browser_task: possible CAPTCHA detected (%s)", captcha_hint
        )

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
    try:
        names = list(history.action_names())
    except Exception:
        return []
    try:
        errors = list(history.errors())
    except Exception:
        errors = []
    trace: list = []
    for idx, action in enumerate(names):
        err = errors[idx] if idx < len(errors) else None
        ok = err is None
        detail = None if ok else str(err)[:1_000]
        trace.append({
            "step_index": idx,
            "action": str(action) if action is not None else None,
            "ok": ok,
            "detail": detail,
        })
    return trace


def _read_request() -> dict:
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

        raw_cdp = req.get("cdp_url")
        cdp_url = raw_cdp.strip() if isinstance(raw_cdp, str) and raw_cdp.strip() else None

        flash_mode = req.get("flash_mode")
        flash_mode = bool(flash_mode) if flash_mode is not None else None

        max_actions = req.get("max_actions_per_step")
        try:
            max_actions = int(max_actions) if max_actions is not None else None
        except (TypeError, ValueError):
            max_actions = None

        initial = req.get("initial_actions")
        initial_actions = list(initial) if isinstance(initial, list) else None

        secrets = req.get("sensitive_data")
        sensitive_data = dict(secrets) if isinstance(secrets, dict) else None

        result = asyncio.run(_run_task(
            task.strip(), max_steps, headless, cdp_url,
            flash_mode=flash_mode,
            max_actions_per_step=max_actions,
            initial_actions=initial_actions,
            sensitive_data=sensitive_data,
        ))
        _emit(result)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}".strip()
        traceback.print_exc(file=sys.stderr)
        _emit({"ok": False, "error": detail, "stderr_tail": _stderr_tail()})


if __name__ == "__main__":
    main()

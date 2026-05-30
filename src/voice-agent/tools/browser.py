"""``browser_task`` tool — drive a real browser to do a natural-language web task.

This runs inside the pinned voice ``.venv``, which deliberately does NOT have
``browser_use`` installed. Instead of importing it (which would crash on this
interpreter), the handler spawns the ISOLATED venv's Python
(``~/.jarvis/browser-use-venv/bin/python``) as a subprocess and talks to it
over stdin/stdout JSON. The actual browser_use Agent lives in the runner at
``browser_use_bridge/runner.py`` — a sibling of ``tools/`` so the voice venv's
tool discovery (which globs ``tools/*.py`` only) never imports it.

Gating: the tool is INERT (``check_fn`` False) unless BOTH the isolated venv
interpreter exists AND at least one supported LLM API key is set. That keeps
headless CI / no-key environments from ever launching a browser.

This module imports ONLY the stdlib + the registry — never ``browser_use``.
The subprocess is launched with ``asyncio.create_subprocess_exec`` (argv list,
no shell), so the task text is never interpolated into a shell command line.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from .registry import registry, tool_error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths + config
# ---------------------------------------------------------------------------

# Isolated venv interpreter that has browser_use installed (kept out of the
# voice .venv on purpose). Resolved at call time so a per-process HOME override
# (e.g. in tests) is honored.
_ISOLATED_PY_REL = (".jarvis", "browser-use-venv", "bin", "python")

# Absolute path to the standalone runner shipped beside tools/.
_RUNNER_PATH = Path(__file__).resolve().parent.parent / "browser_use_bridge" / "runner.py"

# LLM keys the runner can use; at least one must be present for the tool to arm.
_LLM_ENV_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")

# Hard ceiling on a single browser task (seconds). Long enough for a multi-step
# web flow, short enough that a hung browser can't wedge the supervisor turn.
_TASK_TIMEOUT_S = 180.0

# Default step budget if the supervisor doesn't specify one.
_DEFAULT_MAX_STEPS = 25

# Opt-in: names a registered, available cloud-browser provider (kind "browser")
# whose remote CDP browser browser_task should drive instead of launching a
# LOCAL browser. UNSET/EMPTY (the default) → the local subprocess path runs
# exactly as before, no provider, no session. This is the regression guard.
_BROWSER_PROVIDER_ENV = "JARVIS_BROWSER_PROVIDER"


def _isolated_python() -> Path:
    """Absolute path to the isolated venv's Python (HOME resolved at call time)."""
    return Path.home().joinpath(*_ISOLATED_PY_REL)


def _has_llm_key() -> bool:
    """True when at least one supported LLM API key is set (non-empty)."""
    return any(os.environ.get(k, "").strip() for k in _LLM_ENV_KEYS)


def _check_browser_available() -> bool:
    """Arm the tool only when the isolated venv interpreter AND a key both exist.

    Without the interpreter the subprocess can't run; without a key the runner
    would just error out after spinning up a browser. Gating on both keeps
    no-key / headless-CI environments fully inert (no browser launch attempt).
    """
    return _isolated_python().exists() and _has_llm_key()


# ---------------------------------------------------------------------------
# Optional cloud-browser provider resolution (opt-in, regression-safe)
# ---------------------------------------------------------------------------


def _resolve_browser_provider() -> Optional[Any]:
    """Return the configured cloud-browser provider, or None for local default.

    The local subprocess path is the default and is preserved byte-for-byte
    whenever ``JARVIS_BROWSER_PROVIDER`` is unset or empty — this function
    returns None and ``browser_task`` never opens a remote session.

    A provider is returned ONLY when ALL hold:
      * ``JARVIS_BROWSER_PROVIDER`` names a provider, and
      * a provider of that name is registered under kind ``"browser"``, and
      * its ``is_available()`` reports True (credentials present).

    Any lookup failure (registry import error, unknown name, unavailable
    provider) degrades to None so a misconfiguration silently falls back to the
    working local path rather than crashing the turn.
    """
    configured = os.environ.get(_BROWSER_PROVIDER_ENV, "").strip()
    if not configured:
        return None  # default: local subprocess path, unchanged

    try:
        from . import _provider_registry

        provider = _provider_registry.get_provider("browser", configured)
    except Exception as exc:  # noqa: BLE001 — never let resolution crash the turn
        logger.warning("browser_task: provider resolution failed for %r: %s", configured, exc)
        return None

    if provider is None:
        logger.warning(
            "browser_task: %s=%r but no such provider is registered — using local browser",
            _BROWSER_PROVIDER_ENV,
            configured,
        )
        return None

    try:
        available = bool(provider.is_available())
    except Exception as exc:  # noqa: BLE001 — a provider probe must not raise out
        logger.warning(
            "browser_task: provider %r is_available() raised (%s) — using local browser",
            configured,
            exc,
        )
        return None

    if not available:
        logger.warning(
            "browser_task: provider %r is configured but unavailable (missing key?) "
            "— using local browser",
            configured,
        )
        return None

    return provider


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_BROWSER_TASK_SCHEMA = {
    "name": "browser_task",
    "description": (
        "Drive a REAL web browser HEADLESSLY in the background to do a web task "
        "end-to-end, then report back a short text summary (there is NO visible "
        "window — the user does not watch it work). Use this for any web data/"
        "navigation goal: look something up, search a site, read/compare pages, "
        "fill a form, post/submit. Examples: 'check the top Hacker News stories', "
        "'find the price of the RTX 6000 on nvidia.com and tell me', 'log into X "
        "and read my latest DMs'. Prefer this over computer_use for anything "
        "where the goal is information or web actions rather than showing "
        "something on the screen. Give a complete, self-contained instruction "
        "(include the destination/site and exactly what to find or do). The "
        "browser may take up to ~3 minutes; you'll get back a short summary of "
        "what it found or did."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "The complete web task in plain English, self-contained. "
                    "Include the destination/site and exactly what to find or do."
                ),
            },
            "max_steps": {
                "type": "integer",
                "description": (
                    "Maximum browser action steps before giving up (default 25). "
                    "Raise for longer multi-page flows; lower for quick lookups."
                ),
                "default": _DEFAULT_MAX_STEPS,
            },
        },
        "required": ["task"],
    },
}


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def _coerce_max_steps(value) -> int:
    """Coerce an arbitrary max_steps arg to a sane positive int."""
    try:
        steps = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_STEPS
    return max(1, steps)


def _format_result(payload: dict) -> str:
    """Turn the runner's JSON payload into a concise string for the supervisor."""
    if payload.get("ok"):
        result = str(payload.get("result", "")).strip() or "(browser task finished with no result text)"
        steps = payload.get("steps")
        if isinstance(steps, int) and steps > 0:
            return f"{result}\n\n(completed in {steps} browser step{'s' if steps != 1 else ''})"
        return result
    err = str(payload.get("error", "")).strip() or "unknown browser error"
    return f"Browser task failed: {err}"


async def _run_runner(python_path: Path, request: bytes) -> str:
    """Spawn the isolated browser_use runner with *request*, return a summary.

    Shared by the local-default and the opt-in remote-CDP paths — the only
    difference between them is whether *request* carries a ``cdp_url`` key.
    Never raises: timeout, a crashed subprocess, or garbled output all map to a
    clear human-readable error string so a failed browser task can't crash the
    voice turn.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            str(python_path),
            str(_RUNNER_PATH),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:  # noqa: BLE001 — spawn failure must not crash the turn
        logger.warning("browser_task: failed to spawn isolated runner: %s", exc)
        return tool_error(f"browser tool failed to start: {exc}")

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=request), timeout=_TASK_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        # Kill the runner so a hung browser doesn't linger past the turn.
        try:
            proc.kill()
            await proc.wait()
        except Exception:  # noqa: BLE001 — best-effort reap
            pass
        logger.warning("browser_task: timed out after %ss", _TASK_TIMEOUT_S)
        return tool_error(
            f"browser task timed out after {int(_TASK_TIMEOUT_S)}s "
            "(the page may be slow or the task too large)"
        )
    except Exception as exc:  # noqa: BLE001 — any IPC failure -> clean error
        logger.warning("browser_task: subprocess communication failed: %s", exc)
        return tool_error(f"browser task failed: {exc}")

    stdout_text = (stdout_b or b"").decode("utf-8", errors="replace").strip()
    if not stdout_text:
        stderr_tail = (stderr_b or b"").decode("utf-8", errors="replace").strip()[-300:]
        logger.warning(
            "browser_task: runner produced no stdout (rc=%s); stderr tail: %s",
            proc.returncode,
            stderr_tail,
        )
        return tool_error(
            "browser task produced no output "
            f"(exit {proc.returncode})" + (f": {stderr_tail}" if stderr_tail else "")
        )

    # The runner emits exactly one JSON line on stdout; if it ever emits extra
    # noise, the result line is the last non-empty one.
    last_line = stdout_text.splitlines()[-1].strip()
    try:
        payload = json.loads(last_line)
    except (json.JSONDecodeError, ValueError):
        logger.warning("browser_task: could not parse runner output: %r", last_line[:300])
        return tool_error("browser task returned unparseable output")

    if not isinstance(payload, dict):
        return tool_error("browser task returned an unexpected result shape")

    return _format_result(payload)


async def _handle_browser_task(args: dict) -> str:
    """Spawn the isolated browser_use runner, send the task, return a summary.

    Default (``JARVIS_BROWSER_PROVIDER`` unset): spawns a LOCAL browser via the
    runner — behaviour unchanged. Opt-in (env names an available cloud-browser
    provider): opens a remote session, drives it over CDP, and always closes the
    session afterward. A provider/session failure degrades to a clean error.

    Never raises: timeout, a crashed subprocess, or garbled output all map to a
    clear human-readable error string so a failed browser task can't crash the
    voice turn.
    """
    task = (args.get("task") or "").strip()
    if not task:
        return tool_error("browser_task requires a non-empty 'task'")

    max_steps = _coerce_max_steps(args.get("max_steps", _DEFAULT_MAX_STEPS))

    python_path = _isolated_python()
    if not python_path.exists():
        return tool_error(
            f"browser tool unavailable: isolated venv python not found at {python_path}"
        )
    if not _RUNNER_PATH.exists():
        return tool_error(f"browser tool unavailable: runner not found at {_RUNNER_PATH}")

    request_obj = {"task": task, "max_steps": max_steps, "headless": True}

    # Default path: no configured provider → local subprocess, unchanged.
    provider = _resolve_browser_provider()
    if provider is None:
        request = json.dumps(request_obj, ensure_ascii=False).encode("utf-8")
        return await _run_runner(python_path, request)

    # Opt-in remote path: open a CDP session, drive it, always close it.
    task_id = uuid.uuid4().hex[:12]
    try:
        session = await asyncio.to_thread(provider.create_session, task_id)
    except Exception as exc:  # noqa: BLE001 — session failure -> clean error, no crash
        logger.warning(
            "browser_task: provider %r create_session failed: %s",
            getattr(provider, "name", "?"),
            exc,
        )
        return tool_error(f"cloud browser session failed to start: {exc}")

    if not isinstance(session, dict) or not str(session.get("cdp_url") or "").strip():
        # Defensively close anything that did get created, then error out.
        sid = session.get("session_id") if isinstance(session, dict) else None
        if sid:
            try:
                await asyncio.to_thread(provider.close_session, str(sid))
            except Exception:  # noqa: BLE001 — best-effort
                pass
        return tool_error(
            f"cloud browser provider {getattr(provider, 'name', '?')!r} returned no cdp_url"
        )

    cdp_url = str(session["cdp_url"]).strip()
    session_id = session.get("session_id")
    request_obj["cdp_url"] = cdp_url
    request = json.dumps(request_obj, ensure_ascii=False).encode("utf-8")

    try:
        return await _run_runner(python_path, request)
    finally:
        if session_id:
            try:
                await asyncio.to_thread(provider.close_session, str(session_id))
            except Exception as exc:  # noqa: BLE001 — cleanup must not crash the turn
                logger.warning(
                    "browser_task: failed to close session %s on provider %r: %s",
                    session_id,
                    getattr(provider, "name", "?"),
                    exc,
                )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="browser_task",
    schema=_BROWSER_TASK_SCHEMA,
    handler=_handle_browser_task,
    toolset="browser",
    check_fn=_check_browser_available,
    requires_env=list(_LLM_ENV_KEYS),
    is_async=True,
    description=_BROWSER_TASK_SCHEMA["description"],
    emoji="🌐",
    max_result_size_chars=8_000,
)

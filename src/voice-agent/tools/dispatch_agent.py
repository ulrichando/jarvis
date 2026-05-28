"""``dispatch_agent`` tool — spawn a CC-style named agent via the bin/jarvis CLI.

Spec: docs/superpowers/specs/2026-05-27-voice-agent-subagent-dispatch.md
Plan: docs/superpowers/plans/2026-05-27-voice-agent-subagent-dispatch.md

Single registered tool ``dispatch_agent`` that runs
``bin/jarvis --print --agent <type> "<task>"`` as a subprocess to handle one of
four named CLI agent types: Explore, researcher, code-reviewer, Plan.
Synchronous wait with per-type timeout; a front-loaded ack phrase plays via
the existing _front_loaded_ack pipeline so the user isn't stranded in silence.

Environment overrides (operator tuning):
  JARVIS_DISPATCH_AGENT_TIMEOUT_EXPLORE_S       (default 30)
  JARVIS_DISPATCH_AGENT_TIMEOUT_RESEARCHER_S    (default 90)
  JARVIS_DISPATCH_AGENT_TIMEOUT_CODE_REVIEWER_S (default 60)
  JARVIS_DISPATCH_AGENT_TIMEOUT_PLAN_S          (default 60)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

from .registry import registry, tool_error

logger = logging.getLogger("jarvis.dispatch_agent")

# bin/jarvis path is resolved relative to this file:
# tools/ -> voice-agent/ -> src/ -> project root -> bin/jarvis
_BIN_JARVIS = Path(__file__).resolve().parents[3] / "bin" / "jarvis"

# Per-type policy. cli_agent is the exact string bin/jarvis --agent expects
# (per the project's agent registry — verified via bin/jarvis --help).
_POLICY: Dict[str, Dict[str, Any]] = {
    "explore": {
        "cli_agent": "Explore",
        "default_timeout_s": 30.0,
        "timeout_env": "JARVIS_DISPATCH_AGENT_TIMEOUT_EXPLORE_S",
        "ack": "Searching the code…",
    },
    "researcher": {
        "cli_agent": "researcher",
        "default_timeout_s": 90.0,
        "timeout_env": "JARVIS_DISPATCH_AGENT_TIMEOUT_RESEARCHER_S",
        "ack": "Looking that up online…",
    },
    "code_reviewer": {
        "cli_agent": "code-reviewer",
        "default_timeout_s": 60.0,
        "timeout_env": "JARVIS_DISPATCH_AGENT_TIMEOUT_CODE_REVIEWER_S",
        "ack": "Reviewing the diff…",
    },
    "plan": {
        "cli_agent": "Plan",
        "default_timeout_s": 60.0,
        "timeout_env": "JARVIS_DISPATCH_AGENT_TIMEOUT_PLAN_S",
        "ack": "Thinking through that design…",
    },
}

# Single-slot session-id tracker. The agent updates this on every turn start;
# the dispatcher snapshots it at dispatch time and compares on completion. A
# swap means the user's turn has been abandoned (barge-in / new conversation)
# and the in-flight subagent result should be discarded.
_active_session_token: list = [None]

# Side-channel for the agent's _on_function_tools_executed observer.
# Reflects the LAST handle_dispatch_agent invocation. Mutated on every
# code path via try/finally so the wiring sees the truth even if the
# tool_result output is missing (e.g., the call was abandoned by the
# framework or the JSON output is in an unexpected shape).
_last_dispatch: dict = {
    "type": None,    # e.g. "explore" / "researcher" / "code_reviewer" / "plan"
    "ms": None,      # elapsed milliseconds (always populated on exit)
    "status": None,  # "success" / "timeout" / "error" / "aborted" / "cancelled" / "crashed" / "spawn_failed"
}


def _timeout_for(subagent_type: str) -> float:
    pol = _POLICY[subagent_type]
    override = os.environ.get(pol["timeout_env"], "").strip()
    if override:
        try:
            return float(override)
        except ValueError:
            logger.warning(
                f"[dispatch_agent] bad env {pol['timeout_env']}={override!r}; "
                f"using default {pol['default_timeout_s']}s"
            )
    return float(pol["default_timeout_s"])


def _build_argv(subagent_type: str, task: str) -> list[str]:
    cli_agent = _POLICY[subagent_type]["cli_agent"]
    return [str(_BIN_JARVIS), "--print", "--agent", cli_agent, task]


async def handle_dispatch_agent(args: Dict[str, Any]) -> str:
    """Tool handler. Returns either the subagent's stdout (success) or a JSON
    error object (timeout / non-zero exit / spawn-failure / aborted).

    Front-loaded ack is fired separately by the voice-agent (jarvis_agent.py
    reads the ack phrase off this module via per-type policy lookup); the
    handler itself only owns subprocess lifecycle + timeout + telemetry.

    EVERY exit path writes:
      1. A `[dispatch_agent] exit type=X status=Y ms=Z` log line (try/finally)
      2. The module-level `_last_dispatch` dict — read by jarvis_agent.py's
         `_on_function_tools_executed` observer to capture subagent telemetry
         even when the tool_result output is missing or odd-shaped.
    """
    subagent_type = (args.get("subagent_type") or "").strip()
    task = (args.get("task") or "").strip()
    description = (args.get("description") or "").strip()

    # Quick-reject paths (don't write side-channel — these never spawned).
    if subagent_type not in _POLICY:
        return tool_error(
            f"unknown subagent_type {subagent_type!r}; expected one of {list(_POLICY)}"
        )
    if not task:
        return tool_error("task is required and must be non-empty")

    # Snapshot the active session token at dispatch time. If it changes by the
    # time the subprocess finishes, the turn was abandoned and we discard.
    dispatch_token = _active_session_token[0]

    argv = _build_argv(subagent_type, task)
    timeout_s = _timeout_for(subagent_type)
    started = time.monotonic()

    logger.info(
        f"[dispatch_agent] spawn type={subagent_type} timeout={timeout_s}s "
        f"description={description!r} task_chars={len(task)}"
    )

    # Sentinel — overwritten on each known exit path. If somehow the
    # function returns without setting this, the finally block logs
    # "unknown" so we never lose the trace.
    final_status = "unknown"

    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            final_status = "spawn_failed"
            return tool_error(
                f"could not start bin/jarvis: {type(e).__name__}: {e}"
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            final_status = "timeout"
            return tool_error(
                f"subagent {subagent_type} ran too long (>{int(timeout_s)}s); aborted"
            )
        except asyncio.CancelledError:
            # Parent turn task was cancelled (typically a barge-in). Reap the
            # subprocess before letting the cancellation propagate; otherwise
            # the subagent runs orphaned for up to the per-type timeout.
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            final_status = "cancelled"
            raise  # re-raise so livekit-agents knows the task was cancelled
        except Exception as e:
            # Catch BrokenPipeError, OSError, anything else from communicate().
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            final_status = "crashed"
            logger.warning(
                f"[dispatch_agent] communicate failed type={subagent_type}: "
                f"{type(e).__name__}: {e}"
            )
            return tool_error(
                f"subagent {subagent_type} crashed: {type(e).__name__}: {e}"
            )

        # Session-id drift check: if the active token changed during the run,
        # the user's turn is abandoned. Don't return the stale result.
        if (_active_session_token[0] is not dispatch_token
                and dispatch_token is not None):
            final_status = "aborted"
            return json.dumps({
                "status": "aborted",
                "reason": "session swap during dispatch",
            })

        if proc.returncode != 0:
            tail = (stderr.decode("utf-8", errors="replace") or "").strip()[-200:]
            final_status = "error"
            return tool_error(
                f"subagent {subagent_type} failed (exit {proc.returncode}): {tail}"
            )

        text = stdout.decode("utf-8", errors="replace").strip()
        final_status = "success"
        return text
    finally:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        _last_dispatch["type"] = subagent_type
        _last_dispatch["ms"] = elapsed_ms
        _last_dispatch["status"] = final_status
        logger.info(
            f"[dispatch_agent] exit type={subagent_type} status={final_status} ms={elapsed_ms}"
        )


def get_ack_phrase(subagent_type: str) -> str | None:
    """Return the canned ack phrase for a subagent type, or None for unknown."""
    pol = _POLICY.get(subagent_type)
    return pol["ack"] if pol else None


SCHEMA: Dict[str, Any] = {
    "name": "dispatch_agent",
    "description": (
        "Spawn a fresh CLI agent to handle a sub-task with isolated context. "
        "Use when the supervisor's own tool surface would drown in raw output "
        "or when a specialized agent does it better.\n\n"
        "subagent_type:\n"
        "  - 'explore'        : fast file/code search (1-5s). Returns synthesis, not raw grep.\n"
        "  - 'researcher'     : deep web research (15-60s). Returns synthesized answer + sources.\n"
        "  - 'code_reviewer'  : review uncommitted diff against project rules (10-30s).\n"
        "  - 'plan'           : design how to implement a feature (10-30s).\n\n"
        "DO NOT use for simple lookups the supervisor can handle directly. "
        "DO NOT reply 'I'll look into that' WITHOUT actually calling this tool — "
        "claiming dispatch without dispatching is confab."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "subagent_type": {
                "type": "string",
                "enum": ["explore", "researcher", "code_reviewer", "plan"],
            },
            "task":        {"type": "string", "description": "What the subagent should do, in 1-3 sentences"},
            "description": {"type": "string", "description": "Short 3-5 word label for telemetry"},
        },
        "required": ["subagent_type", "task", "description"],
    },
}


registry.register(
    name="dispatch_agent",
    schema=SCHEMA,
    handler=handle_dispatch_agent,
    is_async=True,
)

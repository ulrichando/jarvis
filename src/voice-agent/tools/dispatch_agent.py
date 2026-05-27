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

from .registry import registry

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
    """
    subagent_type = (args.get("subagent_type") or "").strip()
    task = (args.get("task") or "").strip()
    description = (args.get("description") or "").strip()

    if subagent_type not in _POLICY:
        return json.dumps({
            "error": f"unknown subagent_type {subagent_type!r}; expected one of {list(_POLICY)}"
        })
    if not task:
        return json.dumps({"error": "task is required and must be non-empty"})

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

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, PermissionError) as e:
        return json.dumps({
            "error": f"could not start bin/jarvis: {type(e).__name__}: {e}"
        })

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            f"[dispatch_agent] timeout type={subagent_type} after {elapsed_ms}ms"
        )
        return json.dumps({
            "error": f"subagent {subagent_type} ran too long (>{int(timeout_s)}s); aborted"
        })

    elapsed_ms = int((time.monotonic() - started) * 1000)

    # Session-id drift check: if the active token changed during the run,
    # the user's turn is abandoned. Don't return the stale result.
    if _active_session_token[0] is not dispatch_token and dispatch_token is not None:
        logger.info(
            f"[dispatch_agent] session swap during dispatch — discarding type={subagent_type} ms={elapsed_ms}"
        )
        return json.dumps({"status": "aborted", "reason": "session swap during dispatch"})

    if proc.returncode != 0:
        tail = (stderr.decode("utf-8", errors="replace") or "").strip()[-200:]
        logger.warning(
            f"[dispatch_agent] non-zero exit type={subagent_type} rc={proc.returncode} ms={elapsed_ms}"
        )
        return json.dumps({
            "error": f"subagent {subagent_type} failed (exit {proc.returncode}): {tail}"
        })

    text = stdout.decode("utf-8", errors="replace").strip()
    logger.info(
        f"[dispatch_agent] success type={subagent_type} ms={elapsed_ms} stdout_chars={len(text)}"
    )
    return text


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

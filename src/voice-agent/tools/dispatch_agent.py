"""``dispatch_agent`` tool — spawn a CC-style named agent via the bin/jarvis CLI.

Spec: docs/superpowers/specs/2026-05-27-voice-agent-subagent-dispatch.md
Plan: docs/superpowers/plans/2026-05-27-voice-agent-subagent-dispatch.md

Single registered tool ``dispatch_agent`` that runs
``bin/jarvis --print --agent <type> "<task>"`` as a subprocess to handle one of
four named CLI agent types: Explore, researcher, code-reviewer, Plan.

Two delivery modes:
  - **foreground** (default) — synchronous wait with per-type timeout; the
    result is returned to the supervisor, which voices it on the same turn.
    A front-loaded ack phrase plays so the user isn't stranded in silence.
  - **background** (``background=True``, added 2026-05-30) — the tool returns
    an ack IMMEDIATELY (so the turn never blocks and the user keeps talking)
    and runs the subagent in a spawned asyncio task. When it finishes, the
    result is dropped into ``pipeline.background_tasks`` and voiced into the
    live session by ``jarvis_agent.py``'s ``_background_task_watcher`` — the
    same delivery rail the cron pending-watcher uses, in-process. This is the
    only in-session path where a long task runs while the user keeps talking
    on the Claude supervisor (the direct Gemini/GPT modes solved this
    separately in their own receive loops). Spec:
    docs/superpowers/specs/2026-05-30-direct-mode-nonblocking-tools-design.md
    (companion fix — supervisor side).

Environment overrides (operator tuning):
  JARVIS_DISPATCH_AGENT_TIMEOUT_EXPLORE_S       (default 30)
  JARVIS_DISPATCH_AGENT_TIMEOUT_RESEARCHER_S    (default 90)
  JARVIS_DISPATCH_AGENT_TIMEOUT_CODE_REVIEWER_S (default 60)
  JARVIS_DISPATCH_AGENT_TIMEOUT_PLAN_S          (default 60)
  JARVIS_DISPATCH_AGENT_BG_TIMEOUT_S            (default 600 — background runs)
  JARVIS_BG_TASK_MAX                            (default 3 — concurrent bg cap)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from pipeline import background_tasks
from .registry import registry, tool_error

logger = logging.getLogger("jarvis.dispatch_agent")

# bin/jarvis path is resolved relative to this file:
# tools/ -> voice-agent/ -> src/ -> project root -> bin/jarvis
_BIN_JARVIS = Path(__file__).resolve().parents[3] / "bin" / "jarvis"

# Where a background task's FULL result is stashed when it's too long to voice
# in one breath (the spoken announcement carries a summary + a pointer here).
# Cross-platform data dir: ~/.local/share/jarvis on Linux, %LOCALAPPDATA%\jarvis\data on Windows.
from tools.runtime import get_jarvis_data_dir
_BG_RESULTS_DIR = get_jarvis_data_dir() / "background_tasks"

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

# Policy for a user-authored agent — one discovered under ~/.jarvis/agents/
# (a markdown definition bin/jarvis loads) rather than baked into _POLICY.
# cli_agent is the agent's own name: bin/jarvis matches --agent against the
# file's frontmatter `name`, which equals the dispatch subagent_type here.
_CUSTOM_TIMEOUT_ENV = "JARVIS_DISPATCH_AGENT_TIMEOUT_CUSTOM_S"
_CUSTOM_DEFAULT_TIMEOUT_S = 120.0


def _resolve_policy(subagent_type: str) -> Optional[Dict[str, Any]]:
    """Return the dispatch policy for ``subagent_type`` — a built-in entry from
    _POLICY, or a synthesized entry for a user-authored agent discoverable by
    bin/jarvis, or None if the name matches neither.

    Built-ins are matched FIRST and win on a name collision (a user file named
    'researcher' does not shadow the tuned built-in). Discovery is best-effort:
    it must never raise into the dispatch path, so any failure → None (the
    name is then reported as unknown)."""
    if subagent_type in _POLICY:
        return _POLICY[subagent_type]
    try:
        from pipeline import agent_authoring
        info = agent_authoring.find_agent(subagent_type)
    except Exception:  # pragma: no cover — discovery must never break dispatch
        info = None
    if not info:
        return None
    return {
        "cli_agent": info["name"],
        "default_timeout_s": _CUSTOM_DEFAULT_TIMEOUT_S,
        "timeout_env": _CUSTOM_TIMEOUT_ENV,
        "ack": f"Handing that to the {info['name']} agent…",
        "_dynamic": True,
    }


# Single-slot session-id tracker. The agent updates this on every turn start;
# the dispatcher snapshots it at dispatch time and compares on completion. A
# swap means the user's turn has been abandoned (barge-in / new conversation)
# and the in-flight subagent result should be discarded. NOTE: this only
# applies to the FOREGROUND path — a background task is explicitly allowed to
# outlive the turn that started it (that's the whole point), so it never
# consults the session token.
_active_session_token: list = [None]

# Side-channel for the agent's _on_function_tools_executed observer.
# Reflects the LAST handle_dispatch_agent invocation. Mutated on every
# code path via try/finally so the wiring sees the truth even if the
# tool_result output is missing (e.g., the call was abandoned by the
# framework or the JSON output is in an unexpected shape).
_last_dispatch: dict = {
    "type": None,    # e.g. "explore" / "researcher" / "code_reviewer" / "plan"
    "ms": None,      # elapsed milliseconds (always populated on exit)
    "status": None,  # "success"/"timeout"/"error"/"aborted"/"cancelled"/"crashed"/"spawn_failed"/"background_started"
}


def _timeout_for(subagent_type: str) -> float:
    pol = _resolve_policy(subagent_type)
    if pol is None:  # defensive — the handler validates before reaching here
        return _CUSTOM_DEFAULT_TIMEOUT_S
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


def _bg_timeout() -> float:
    """Background runs get a far longer ceiling than foreground — they're not
    holding the turn open, so a multi-minute researcher/plan is fine."""
    override = os.environ.get("JARVIS_DISPATCH_AGENT_BG_TIMEOUT_S", "").strip()
    if override:
        try:
            return float(override)
        except ValueError:
            logger.warning(
                f"[dispatch_agent] bad env JARVIS_DISPATCH_AGENT_BG_TIMEOUT_S={override!r}; "
                f"using default 600s"
            )
    return 600.0


def _build_argv(subagent_type: str, task: str) -> list[str]:
    pol = _resolve_policy(subagent_type)
    cli_agent = pol["cli_agent"] if pol else subagent_type
    return [str(_BIN_JARVIS), "--print", "--agent", cli_agent, task]


async def _reap(proc) -> None:
    """Best-effort SIGKILL + reap so a timed-out / crashed / cancelled
    subagent never runs orphaned for the rest of its timeout."""
    try:
        proc.kill()
        await proc.wait()
    except Exception:
        pass


async def _run_subagent_proc(
    argv: list[str], timeout_s: float
) -> Tuple[str, bytes, bytes, Optional[int], str]:
    """Spawn bin/jarvis and wait up to ``timeout_s``.

    Returns ``(outcome, stdout, stderr, returncode, detail)`` where
    ``outcome`` is one of ``ok`` / ``spawn_failed`` / ``timeout`` / ``crashed``.
    On :class:`asyncio.CancelledError` the proc is reaped and the error
    re-raised — the caller records its own final-status + message. ``detail``
    carries the exception text for spawn_failed / crashed.

    Shared by both the foreground handler and the background runner so the two
    paths can never drift on the lifecycle (spawn / timeout / kill / decode).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        return ("spawn_failed", b"", b"", None, f"{type(e).__name__}: {e}")

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        await _reap(proc)
        return ("timeout", b"", b"", None, "")
    except asyncio.CancelledError:
        # Parent task cancelled (barge-in / shutdown). Reap before propagating
        # so the subagent doesn't run orphaned.
        await _reap(proc)
        raise
    except Exception as e:  # BrokenPipeError, OSError, anything from communicate()
        await _reap(proc)
        return ("crashed", b"", b"", None, f"{type(e).__name__}: {e}")

    return ("ok", stdout, stderr, proc.returncode, "")


async def handle_dispatch_agent(args: Dict[str, Any]) -> str:
    """Tool handler.

    Foreground (default): returns either the subagent's stdout (success) or a
    JSON error object (timeout / non-zero exit / spawn-failure / aborted).

    Background (``background=True``): returns an ack string IMMEDIATELY and
    runs the subagent in a spawned task; its result is voiced later by the
    background-task watcher. See module docstring.

    EVERY foreground exit path writes:
      1. A `[dispatch_agent] exit type=X status=Y ms=Z` log line (try/finally)
      2. The module-level `_last_dispatch` dict — read by jarvis_agent.py's
         `_on_function_tools_executed` observer to capture subagent telemetry
         even when the tool_result output is missing or odd-shaped.
    """
    subagent_type = (args.get("subagent_type") or "").strip()
    task = (args.get("task") or "").strip()
    description = (args.get("description") or "").strip()
    background = bool(args.get("background"))

    # Quick-reject paths (don't write side-channel — these never spawned).
    if _resolve_policy(subagent_type) is None:
        valid = list(_POLICY)
        try:
            from pipeline import agent_authoring
            valid += [a["name"] for a in agent_authoring.discover_agents()]
        except Exception:  # pragma: no cover — discovery is best-effort
            pass
        return tool_error(
            f"unknown subagent_type {subagent_type!r}; expected one of {valid}"
        )
    if not task:
        return tool_error("task is required and must be non-empty")

    if background:
        return _start_background(subagent_type, task, description)

    # ── Foreground (blocking) path ──────────────────────────────────
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

    final_status = "unknown"
    try:
        try:
            outcome, stdout, stderr, rc, detail = await _run_subagent_proc(argv, timeout_s)
        except asyncio.CancelledError:
            final_status = "cancelled"
            raise  # re-raise so livekit-agents knows the task was cancelled

        if outcome == "spawn_failed":
            final_status = "spawn_failed"
            return tool_error(f"could not start bin/jarvis: {detail}")
        if outcome == "timeout":
            final_status = "timeout"
            return tool_error(
                f"subagent {subagent_type} ran too long (>{int(timeout_s)}s); aborted"
            )
        if outcome == "crashed":
            final_status = "crashed"
            logger.warning(
                f"[dispatch_agent] communicate failed type={subagent_type}: {detail}"
            )
            return tool_error(f"subagent {subagent_type} crashed: {detail}")

        # Session-id drift check: if the active token changed during the run,
        # the user's turn is abandoned. Don't return the stale result.
        if (_active_session_token[0] is not dispatch_token
                and dispatch_token is not None):
            final_status = "aborted"
            return json.dumps({
                "status": "aborted",
                "reason": "session swap during dispatch",
            })

        if rc != 0:
            tail = (stderr.decode("utf-8", errors="replace") or "").strip()[-200:]
            final_status = "error"
            return tool_error(
                f"subagent {subagent_type} failed (exit {rc}): {tail}"
            )

        text = stdout.decode("utf-8", errors="replace").strip()
        final_status = "success"
        return text
    finally:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        # Single .update() so a reader never sees a half-written record
        # (type set but ms/status stale). dict.update from a literal is a
        # single C-level op under the GIL.
        _last_dispatch.update(
            {"type": subagent_type, "ms": elapsed_ms, "status": final_status}
        )
        logger.info(
            f"[dispatch_agent] exit type={subagent_type} status={final_status} ms={elapsed_ms}"
        )


# ── Background mode ──────────────────────────────────────────────────

def _start_background(subagent_type: str, task: str, description: str) -> str:
    """Register + spawn a background subagent run, returning an immediate ack.

    Synchronous (no await): the only async work is the spawned runner, so this
    returns to the supervisor instantly and the turn does not block.
    """
    label = description or task[:40]

    cap = background_tasks.max_concurrent()
    running = background_tasks.active_count()
    if running >= cap:
        return (
            f"I've already got {running} background "
            f"task{'s' if running != 1 else ''} running — let me finish "
            f"{'those' if running != 1 else 'that'} before starting another."
        )

    task_id = uuid.uuid4().hex[:12]
    background_tasks.register(task_id, label)
    asyncio.create_task(
        _run_background(subagent_type, task, label, task_id),
        name=f"bg-dispatch-{task_id}",
    )

    # Record on the side-channel so the telemetry observer sees a started bg
    # task this turn (the real outcome is logged separately by the runner).
    _last_dispatch.update(
        {"type": subagent_type, "ms": 0, "status": "background_started"}
    )

    logger.info(
        f"[dispatch_agent] background start type={subagent_type} id={task_id} "
        f"label={label!r}"
    )
    return (
        f"Started that in the background — {label}. I'll let you know the "
        f"moment it's done; keep talking in the meantime."
    )


async def _run_background(
    subagent_type: str, task: str, label: str, task_id: str
) -> None:
    """The spawned runner. Runs the subagent to completion (long timeout, NO
    session-drift abort — a background task is meant to outlive its turn) and
    hands a spoken announcement to ``background_tasks`` for the watcher to voice.
    """
    argv = _build_argv(subagent_type, task)
    timeout_s = _bg_timeout()
    started = time.monotonic()
    status = "unknown"
    announcement: Optional[str] = None
    logger.info(
        f"[dispatch_agent] background spawn type={subagent_type} id={task_id} "
        f"timeout={timeout_s}s task_chars={len(task)}"
    )
    try:
        try:
            outcome, stdout, stderr, rc, detail = await _run_subagent_proc(argv, timeout_s)
        except asyncio.CancelledError:
            status = "cancelled"
            raise

        if outcome == "spawn_failed":
            status = "spawn_failed"
            announcement = f"I couldn't start the background task ({label})."
            return
        if outcome == "timeout":
            status = "timeout"
            announcement = f"The background task ({label}) ran too long, so I stopped it."
            return
        if outcome == "crashed":
            status = "crashed"
            announcement = f"The background task ({label}) crashed before it finished."
            return
        if rc != 0:
            status = "error"
            announcement = f"The background task ({label}) failed to complete."
            return

        text = stdout.decode("utf-8", errors="replace").strip()
        status = "success"
        announcement = _format_completion(label, text, task_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — never let a bg task die silently
        status = "crashed"
        announcement = f"The background task ({label}) hit an unexpected error."
        logger.warning(
            f"[dispatch_agent] background unexpected error id={task_id}: "
            f"{type(e).__name__}: {e}"
        )
    finally:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            f"[dispatch_agent] background exit type={subagent_type} id={task_id} "
            f"status={status} ms={elapsed_ms}"
        )
        if status == "cancelled":
            # Shutdown / cancellation — nothing to voice, just drop it.
            background_tasks.discard(task_id)
        else:
            background_tasks.complete(task_id, announcement, status=status)


def _voice_summary(text: str, limit: int = 320) -> str:
    """Collapse whitespace and cap to a single voice-able breath, cutting at a
    sentence boundary near the limit when possible."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    cut = collapsed[:limit]
    dot = cut.rfind(". ")
    if dot > limit * 0.5:
        cut = cut[:dot + 1]
    return cut.rstrip()


def _persist_result(task_id: str, label: str, text: str) -> Optional[str]:
    """Stash the FULL background result on disk so a long answer isn't lost to
    voice truncation. Best-effort — returns the path, or None on failure."""
    try:
        _BG_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        path = _BG_RESULTS_DIR / f"{task_id}.txt"
        path.write_text(f"# {label}\n\n{text}\n", encoding="utf-8")
        return str(path)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"[dispatch_agent] could not persist bg result {task_id}: {e}")
        return None


def _format_completion(label: str, text: str, task_id: str) -> str:
    """Build the spoken completion announcement. Short results are voiced whole;
    long results are summarized and the full text persisted with a pointer."""
    full = text or "(the task produced no output)"
    summary = _voice_summary(full)
    lead = f"Your background task — {label} — is done."
    if len(full) > len(summary):  # truncation happened → keep the full text
        saved = _persist_result(task_id, label, full)
        if saved:
            return f"{lead} {summary} I've saved the full result if you want it."
    return f"{lead} {summary}"


def get_ack_phrase(subagent_type: str) -> str | None:
    """Return the canned ack phrase for a subagent type (built-in or
    user-authored), or None for an unknown name."""
    pol = _resolve_policy(subagent_type)
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
        "  - 'plan'           : design how to implement a feature (10-30s).\n"
        "  - <custom name>    : any user-authored agent (see agents_list / "
        "create one with agent_manage). Pass its exact name.\n\n"
        "background (optional, default false): set TRUE for a long task you "
        "want to run WITHOUT blocking the conversation — JARVIS replies "
        "immediately, keeps talking with the user, and voices the result when "
        "it's done. Use it for slow researcher/plan work ('go research X in the "
        "background', 'keep digging while we talk'). DON'T set it for quick "
        "lookups the user is waiting on right now — those should return inline.\n\n"
        "DO NOT use for simple lookups the supervisor can handle directly. "
        "DO NOT reply 'I'll look into that' WITHOUT actually calling this tool — "
        "claiming dispatch without dispatching is confab. When you start a "
        "background task, don't claim its RESULT until the watcher delivers it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "subagent_type": {
                "type": "string",
                "description": (
                    "Which agent to spawn. Built-ins: 'explore', 'researcher', "
                    "'code_reviewer', 'plan'. Or the exact name of any user-authored "
                    "agent (list them with agents_list; create with agent_manage)."
                ),
            },
            "task":        {"type": "string", "description": "What the subagent should do, in 1-3 sentences"},
            "description": {"type": "string", "description": "Short 3-5 word label for telemetry"},
            "background":  {"type": "boolean", "description": "Run without blocking the conversation; result is voiced when done. Default false."},
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

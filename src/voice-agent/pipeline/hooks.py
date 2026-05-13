"""Hook dispatcher — fires user-installed shell scripts on JARVIS
lifecycle events.

Voice-adapted port of claude-code's hooks system. Each event has
zero or more associated executables at
`~/.jarvis/hooks/<event>/<name>` — they fire when the event lands,
receive the JSON payload on stdin, run detached (don't block the
agent turn), and have stdout/stderr logged for inspection.

Hooks are LOCAL automation:

  ~/.jarvis/hooks/task_created/append-to-journal.sh
      Append every new task to a daily file the user can grep.

  ~/.jarvis/hooks/evolution_tier_transition/git-record.sh
      git-commit the learned_rules.md change to a dedicated repo
      so every autonomous mutation has a separate audit trail.

  ~/.jarvis/hooks/worktree_created/open-in-editor.sh
      Auto-open the new worktree path in VS Code.

Design constraints (intentional v1 simplifications vs claude-code):

  - **Fire and forget.** Hooks don't gate the triggering action.
    Claude-code can VETO a tool call via hook exit code; voice
    JARVIS hooks are run AFTER the action lands. If a hook needs
    to block, it should be a real validator in the tool itself.

  - **Sync-spawn, async-drain.** Each hook is launched in parallel
    via `asyncio.create_subprocess_exec`; stdout/stderr are drained
    in a background task that doesn't block the caller. The
    triggering tool call returns immediately.

  - **Executables only.** A file in `<event>/` is fired iff
    `os.access(p, os.X_OK)` — drop the executable bit to disable a
    hook without deleting it. Directories within `<event>/` are
    ignored. Symlinks resolve.

  - **Best-effort.** Any exception during dispatch (missing dir,
    unreadable script, fork failure) logs at WARNING and returns
    silently. Never raises into the caller.

Canonical events JARVIS emits (v1 wiring):

  - session_start                 (jarvis_agent.entrypoint)
  - task_created                  (tools.tasks.task_create)
  - task_completed                (tools.tasks.task_update, status→completed)
  - worktree_created              (tools.worktree.enter_worktree)
  - worktree_removed              (tools.worktree.exit_worktree)
  - evolution_tier_transition     (pipeline.evolution.lifecycle, all paths)

Add new events by calling `fire_hook(event, payload)` from wherever
in the codebase the event happens. The dispatcher silently no-ops
when no scripts are installed for that event.

Environment variables passed to each script:
  - JARVIS_HOOK_EVENT — the event name (so one script can serve
    multiple events via symlink + branch on $JARVIS_HOOK_EVENT).
  - JARVIS_HOOK_PAYLOAD_JSON — the payload, JSON-encoded.
  (Stdin also receives the JSON payload — pick whichever is more
  convenient for your script.)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional


__all__ = ["HOOKS_DIR", "fire_hook", "fire_hook_sync"]


_logger = logging.getLogger("jarvis.pipeline.hooks")


HOOKS_DIR: Path = Path.home() / ".jarvis" / "hooks"

# Subprocess drain timeout — per-hook. If a script hangs, it gets
# SIGTERM'd and we move on. Keeps a slow / wedged hook from
# accumulating zombie tasks.
_HOOK_TIMEOUT_S = 30.0


def _executable_scripts(event_dir: Path) -> list[Path]:
    """Return sorted executable files (no directories, no symlinks
    to nonexistent targets) directly under `event_dir`."""
    if not event_dir.is_dir():
        return []
    out: list[Path] = []
    try:
        for entry in sorted(event_dir.iterdir()):
            if not entry.is_file():
                continue
            if not os.access(entry, os.X_OK):
                continue
            out.append(entry)
    except Exception as e:
        _logger.warning(f"[hooks] scan {event_dir} failed: {e}")
        return []
    return out


async def _drain_hook(
    proc: asyncio.subprocess.Process,
    script_name: str,
    event: str,
    payload_bytes: bytes,
) -> None:
    """Send payload on stdin, capture stdout/stderr, log result.
    Bounded by _HOOK_TIMEOUT_S so a stuck script doesn't leak the
    task forever.
    """
    try:
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(payload_bytes),
                timeout=_HOOK_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            _logger.warning(
                f"[hooks] {event}/{script_name} timed out after "
                f"{_HOOK_TIMEOUT_S}s; killing"
            )
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return

        rc = proc.returncode
        if rc != 0:
            err_text = stderr_b.decode("utf-8", "replace").strip()[:300]
            _logger.warning(
                f"[hooks] {event}/{script_name} exited {rc}; stderr={err_text!r}"
            )
        else:
            out_text = stdout_b.decode("utf-8", "replace").strip()[:200]
            if out_text:
                _logger.info(
                    f"[hooks] {event}/{script_name} ok; stdout={out_text!r}"
                )
            else:
                _logger.info(f"[hooks] {event}/{script_name} ok")
    except Exception as e:
        _logger.warning(f"[hooks] {event}/{script_name} drain error: {e}")


async def fire_hook(event: str, payload: Optional[dict] = None) -> int:
    """Fire every executable under `~/.jarvis/hooks/<event>/` in
    parallel. Returns the number of scripts launched.

    Best-effort, fire-and-forget. The triggering tool call returns
    as soon as all scripts are spawned — output drains happen in
    background tasks.

    Args:
        event:   Canonical event name (e.g. 'task_created').
        payload: Optional dict; passed to each hook as JSON on stdin
                 AND as the JARVIS_HOOK_PAYLOAD_JSON env var.

    Returns:
        Count of scripts launched (0 if the event dir is missing /
        empty / has no executables).
    """
    if not event or not isinstance(event, str):
        return 0
    event_dir = HOOKS_DIR / event
    scripts = _executable_scripts(event_dir)
    if not scripts:
        return 0

    payload = payload or {}
    payload_bytes = json.dumps({
        "event": event,
        "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "payload": payload,
    }).encode("utf-8")

    launched = 0
    for script in scripts:
        try:
            env = dict(os.environ)
            env["JARVIS_HOOK_EVENT"] = event
            env["JARVIS_HOOK_PAYLOAD_JSON"] = payload_bytes.decode("utf-8")
            proc = await asyncio.create_subprocess_exec(
                str(script),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            asyncio.create_task(
                _drain_hook(proc, script.name, event, payload_bytes)
            )
            launched += 1
        except Exception as e:
            _logger.warning(f"[hooks] failed to launch {script}: {e}")

    if launched:
        _logger.info(f"[hooks] fired {launched} script(s) for {event}")
    return launched


def fire_hook_sync(event: str, payload: Optional[dict] = None) -> int:
    """Synchronous companion to `fire_hook` for callers that don't
    live on an asyncio event loop.

    Used by `pipeline.evolution.lifecycle` (sync code path inside an
    async wireup). Spawns each hook detached via `subprocess.Popen`,
    closes its stdin after writing the payload, and returns. NO
    OUTPUT DRAIN — exit codes are lost; failures still log at
    WARNING from the launch path.

    Same contract as `fire_hook` otherwise: scans HOOKS_DIR/<event>/
    for executables, JSON payload on stdin + env var, fire-and-
    forget, never raises.
    """
    if not event or not isinstance(event, str):
        return 0
    event_dir = HOOKS_DIR / event
    scripts = _executable_scripts(event_dir)
    if not scripts:
        return 0

    payload = payload or {}
    payload_bytes = json.dumps({
        "event": event,
        "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "payload": payload,
    }).encode("utf-8")

    launched = 0
    for script in scripts:
        try:
            env = dict(os.environ)
            env["JARVIS_HOOK_EVENT"] = event
            env["JARVIS_HOOK_PAYLOAD_JSON"] = payload_bytes.decode("utf-8")
            proc = subprocess.Popen(
                [str(script)],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            try:
                if proc.stdin is not None:
                    proc.stdin.write(payload_bytes)
                    proc.stdin.close()
            except Exception:
                # Script may have closed stdin before we wrote; fine.
                pass
            launched += 1
        except Exception as e:
            _logger.warning(f"[hooks-sync] failed to launch {script}: {e}")

    if launched:
        _logger.info(f"[hooks-sync] fired {launched} script(s) for {event}")
    return launched

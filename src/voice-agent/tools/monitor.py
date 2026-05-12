"""Background command monitoring — voice-adapted port of claude-code's
Monitor tool (with elements of its BashOutput + KillBash pair).

**Voice-adapted shape: poll-based, not push-based.** Claude-code's
Monitor pushes each output line into the LLM mid-conversation; voice
JARVIS can't do that without spontaneous TTS interruptions, so the
voice variant returns a handle on start, accumulates output into a
ring buffer, and exposes status/stop/list tools the supervisor calls
on demand ("did the build finish?", "any errors yet?"). User-initiated
polling matches voice's natural cadence.

Four @function_tools surface to the supervisor:

  - monitor_start(command, description)
    Spawn a background process via /bin/bash -c <cmd> (same as
    tools/bash.py for bashism support). stderr is merged into
    stdout for one unified stream. Returns a short id (`m1`, `m2`,
    …) the supervisor uses for subsequent calls.

  - monitor_status(monitor_id, lines)
    Return current state (running / exited+code) plus the most
    recent `lines` of output (default 20, max 500). Voice-friendly
    summary including elapsed seconds.

  - monitor_stop(monitor_id)
    SIGTERM, then SIGKILL after 2s if it doesn't die. Returns the
    exit info so the supervisor can voice the outcome.

  - monitor_list()
    Inventory all active monitors with their state and elapsed time.

**Lifecycle + scope:**

State is an in-memory registry, **worker-scoped**. JARVIS uses
LiveKit forkserver workers; each job's supervisor runs in one
worker, so a monitor spawned during a session is accessible for
that session's supervisor only. When the worker job ends, in-memory
state is gone; the bash child process is in the worker's process
group and dies with it (no `start_new_session=True` — we want
cleanup, not orphans).

**Output buffer cap: 500 lines.** Each line gets a `HH:MM:SS `
timestamp prefix on append. Deque overwrites oldest when full so
long-running monitors don't unbounded-grow.

**Active-monitor cap: 10.** Prevents the supervisor from spawning
runaway watchers.

**Safety:** unlike `tools/bash.py`, no destructive-command warning
and no banned-utility redirect. Monitors are long-running watchers
(`tail -f`, `npm run dev`, polling) — `cat`, `tail`, `head` are
legitimate uses. The user knows what they're starting.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from livekit.agents.llm import function_tool


__all__ = [
    "monitor_start",
    "monitor_status",
    "monitor_stop",
    "monitor_list",
    "reset_for_test",
]


_logger = logging.getLogger("jarvis.tools.monitor")


_MAX_OUTPUT_LINES = 500
_MAX_MONITORS = 10
_STOP_GRACE_S = 2.0


@dataclass
class _Monitor:
    """Internal monitor state. id is the public handle."""
    id: str
    command: str
    description: str
    process: asyncio.subprocess.Process
    started: float
    output: deque = field(default_factory=lambda: deque(maxlen=_MAX_OUTPUT_LINES))
    reader_task: Optional[asyncio.Task] = None
    exit_code: Optional[int] = None


_monitors: dict[str, _Monitor] = {}
_next_id: int = 1


def reset_for_test() -> None:
    """Clear the registry. Test-only — production never calls this."""
    global _next_id
    _monitors.clear()
    _next_id = 1


def _new_id() -> str:
    """Return the next handle: m1, m2, m3 … fresh each worker."""
    global _next_id
    sid = f"m{_next_id}"
    _next_id += 1
    return sid


async def _drain_stream(monitor: _Monitor) -> None:
    """Read the merged stdout/stderr stream into the monitor's
    bounded buffer. Self-cleans the exit_code when the process ends.
    Errors are logged but never raised — a monitor that died mid-read
    just stops drawing lines; status calls still work.
    """
    proc = monitor.process
    try:
        if proc.stdout is None:
            return
        async for raw in proc.stdout:
            text = raw.decode("utf-8", errors="replace").rstrip()
            ts = time.strftime("%H:%M:%S", time.localtime())
            monitor.output.append(f"{ts} {text}")
    except Exception as e:
        _logger.warning(f"[monitor:{monitor.id}] drain error: {e}")
    # Ensure exit_code is set even if the stream EOF'd before .wait
    try:
        if proc.returncode is None:
            await proc.wait()
        monitor.exit_code = proc.returncode
    except Exception as e:
        _logger.warning(f"[monitor:{monitor.id}] wait error: {e}")


def _state_label(monitor: _Monitor) -> str:
    if monitor.exit_code is not None:
        return f"exited (code {monitor.exit_code})"
    rc = monitor.process.returncode
    if rc is not None:
        return f"exited (code {rc})"
    return "running"


# ── @function_tool surface ──────────────────────────────────────


@function_tool
async def monitor_start(command: str, description: str = "") -> str:
    """Start a long-running command in the background, tracked by id.

    The command runs under `/bin/bash -c <command>` (same as bash()),
    with stderr merged into stdout. Output accumulates into a 500-line
    ring buffer the supervisor can poll via `monitor_status`. Use this
    for tail-style watches, dev servers, polling loops, anything where
    you want to check progress later without blocking the conversation.

    For one-shot commands where you want the full output now, use
    `bash()` instead — that's the synchronous path.

    Args:
        command:     The shell command. Bash features work
                     (`[[ ]]`, brace expansion, etc.).
        description: Short human-readable label (max 80 chars used).
                     Helps the user follow what's being watched.

    Returns:
        A confirmation including the new monitor id ("m1", "m2", ...).
    """
    cmd = (command or "").strip()
    if not cmd:
        return "Empty command. Pass a non-empty bash command."

    if len(_monitors) >= _MAX_MONITORS:
        return (
            f"Too many monitors active ({len(_monitors)}/{_MAX_MONITORS} max). "
            f"Call monitor_stop on one before starting another."
        )

    desc = (description or "").strip() or cmd[:80]

    try:
        proc = await asyncio.create_subprocess_exec(
            "/bin/bash", "-c", cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as e:
        return f"Failed to start monitor: {type(e).__name__}: {e}"

    monitor = _Monitor(
        id=_new_id(),
        command=cmd,
        description=desc,
        process=proc,
        started=time.time(),
    )
    monitor.reader_task = asyncio.create_task(_drain_stream(monitor))
    _monitors[monitor.id] = monitor

    _logger.info(f"[monitor] started {monitor.id} pid={proc.pid} cmd={cmd[:80]!r}")
    return f"Monitor {monitor.id} started: {desc}"


@function_tool
async def monitor_status(monitor_id: str, lines: int = 20) -> str:
    """Get current state + recent output of a running monitor.

    Use when the user asks "did the build finish?", "any errors?",
    "what's the status of <thing>?". Default returns the last 20
    lines; pass `lines` up to 500 for more context.

    Args:
        monitor_id: The id from monitor_start ("m1", "m2", ...).
        lines:      How many tail lines to return (1-500, default 20).

    Returns:
        Voice-friendly state summary + last N output lines.
    """
    mid = (monitor_id or "").strip()
    monitor = _monitors.get(mid)
    if monitor is None:
        return f"Monitor {mid!r} not found. Call monitor_list to see active monitors."

    try:
        n = int(lines)
    except (TypeError, ValueError):
        n = 20
    n = max(1, min(n, _MAX_OUTPUT_LINES))

    elapsed = int(time.time() - monitor.started)
    state = _state_label(monitor)
    recent = list(monitor.output)[-n:]
    total = len(monitor.output)
    body = "\n".join(recent) if recent else "(no output yet)"

    return (
        f"Monitor {monitor.id} ({monitor.description}) — {state}, "
        f"elapsed {elapsed}s, buffered lines: {total}\n"
        f"--- last {len(recent)} line(s) ---\n{body}"
    )


@function_tool
async def monitor_stop(monitor_id: str) -> str:
    """Stop a running monitor. SIGTERM, then SIGKILL after 2s if it
    doesn't exit cleanly. Already-exited monitors return their
    captured exit code without re-killing.

    Args:
        monitor_id: The id from monitor_start.
    """
    mid = (monitor_id or "").strip()
    monitor = _monitors.get(mid)
    if monitor is None:
        return f"Monitor {mid!r} not found."

    if monitor.exit_code is not None or monitor.process.returncode is not None:
        rc = monitor.exit_code if monitor.exit_code is not None else monitor.process.returncode
        return f"Monitor {mid} already exited (code {rc})."

    try:
        monitor.process.terminate()
        try:
            await asyncio.wait_for(monitor.process.wait(), timeout=_STOP_GRACE_S)
        except asyncio.TimeoutError:
            try:
                monitor.process.kill()
                await monitor.process.wait()
            except Exception:
                pass
    except Exception as e:
        return f"Failed to stop monitor {mid}: {type(e).__name__}: {e}"

    rc = monitor.process.returncode
    monitor.exit_code = rc
    return f"Monitor {mid} stopped (exit code {rc})."


@function_tool
async def monitor_list() -> str:
    """List all active monitors with their state.

    Returns a one-per-line summary; use monitor_status for details
    on any single one.
    """
    if not _monitors:
        return "No monitors active."
    now = time.time()
    rows: list[str] = []
    for mid, m in sorted(_monitors.items()):
        elapsed = int(now - m.started)
        state = _state_label(m)
        rows.append(f"  {mid}  {state:24s}  elapsed {elapsed:4d}s  {m.description[:60]}")
    return f"{len(_monitors)} monitor(s):\n" + "\n".join(rows)

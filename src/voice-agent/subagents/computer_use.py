"""computer_use subagent — vision-plan-act loop on the user's desktop.

Spec: docs/superpowers/specs/2026-05-18-jarvis-computer-use-parity-design.md

The subagent runs a model-owned loop via tools/computer_loop.py. Its
LiveKit-side tool surface is just `task_done` (per the existing
HandoffSubagent gate); the actual `computer` tool calls happen
directly against the Anthropic client inside the loop.

Tool-less shape (tools_required=False) — same pattern as
screen_share — because the gate's purpose (catch confabulating LLMs
that bail before acting) is satisfied internally by the loop's own
audit trail.

Gated `JARVIS_SUBAGENT_COMPUTER_USE=1`, default OFF until soaked.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from .registry import HandoffSubagent, register


logger = logging.getLogger("jarvis.subagents.computer_use")


__all__ = ["register_computer_use", "_ensure_x11_session"]


COMPUTER_USE_INSTRUCTIONS = """\
You are JARVIS's computer-use subagent. The supervisor has handed you a
task that requires direct GUI interaction on Ulrich's Linux desktop.

Your tools:
- `computer` — Anthropic computer-use tool (you know the contract).
- `task_done(summary)` — call after the work is complete, voicing one
  short English sentence describing what you accomplished.

Rules:
1. **Observe first.** Take a screenshot before your first action; don't
   guess what's on screen.
2. **Iterate.** After each action, screenshot to verify the action
   produced the change you expected.
3. **Stop on sensitive screens.** Password fields, 2FA prompts, banking
   sites, system password dialogs → call `task_done` with summary
   "needs password / 2FA / sensitive screen — handing back to supervisor".
   Do NOT type credentials.
4. **Ask before destruction.** For Delete, Send, Submit, Format,
   Overwrite, Remove, Erase, Discard, Publish, Post, Drop, Wipe —
   the harness will voice a confirmation prompt. If declined you must
   skip the action; do not retry it without re-asking.
5. **Be efficient.** Max 30 iterations and $0.50 budget per task. If
   you repeat the same action 3 times without progress, the harness
   will escalate the model; if escalation also fails it will bail.
6. **Voice is the user's mic.** Don't narrate. The supervisor speaks;
   you only emit `task_done` when finished.
"""


async def _xdpyinfo_ok() -> bool:
    """Return True if xdpyinfo can talk to the X server."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "xdpyinfo",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=3.0)
        return proc.returncode == 0
    except Exception:
        return False


async def _ensure_x11_session(context, request, supervisor) -> Optional[str]:
    """Pre-transfer hook: verify we're on X11 with a live display.
    Aborts cleanly on Wayland or when X11 isn't reachable."""
    # Wayland detection — fast path; no subprocess.
    if os.environ.get("WAYLAND_DISPLAY"):
        logger.warning(
            "[cua.pre_transfer] WAYLAND_DISPLAY set; computer_use needs X11"
        )
        return (
            "Computer-use needs X11; you're on a Wayland session. "
            "Log out and pick the X11 session from the greeter, or use "
            "the browser subagent if your task is web-based."
        )
    if not await _xdpyinfo_ok():
        logger.warning("[cua.pre_transfer] xdpyinfo failed; no live X11 display")
        return (
            "Couldn't reach the X11 display; check your DISPLAY environment "
            "variable and that the X server is running."
        )
    return None


def _computer_use_tools() -> list:
    """The subagent exposes only task_done to LiveKit. The actual
    `computer` tool is passed directly to the Anthropic client inside
    the loop, not via the LiveKit tool framework."""
    return []


def register_computer_use() -> None:
    """Register the computer_use subagent — only when explicitly
    enabled via env. Default OFF until soak telemetry justifies."""
    if os.environ.get("JARVIS_SUBAGENT_COMPUTER_USE", "0") != "1":
        return
    register(HandoffSubagent(
        name="computer_use",
        transfer_tool="transfer_to_computer_use",
        when_to_use=(
            "Use when the user wants direct GUI control on the desktop — "
            "drive an unfamiliar GUI app, complete a multi-step UI flow, "
            "navigate dialogs, anything where pointing-and-clicking matters. "
            "Not for shell-only tasks (use bash) or simple browser actions "
            "(use transfer_to_browser)."
        ),
        instructions=COMPUTER_USE_INSTRUCTIONS,
        tool_factory=_computer_use_tools,
        ack_phrase="On it.",
        max_history_items=4,
        enabled=True,
        tools_required=False,   # tool-less; loop owns its own audit
        pre_transfer=_ensure_x11_session,
    ))
    logger.info("[computer_use] subagent registered (env flag is ON)")

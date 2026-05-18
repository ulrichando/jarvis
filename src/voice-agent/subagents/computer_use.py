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

from livekit.agents import Agent

from .registry import HandoffSubagent, register


logger = logging.getLogger("jarvis.subagents.computer_use")


__all__ = [
    "register_computer_use",
    "_ensure_x11_session",
    "build_safety_confirm_cb",
    "ComputerUseAgent",
]


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


def build_safety_confirm_cb(session, timeout_s: float = 30.0):
    """Build a callback the loop uses to voice destructive-action
    confirmations and await user yes/no.

    Mechanism:
      1. Push the phrase to TTS via session.say().
      2. Set session._cua_confirm_future = Future() so the supervisor's
         on_user_turn_completed hook can resolve it with True/False
         parsed from the next user transcript.
      3. Wait up to `timeout_s`; default-deny on timeout.
    """
    async def cb(phrase: str) -> bool:
        fut = asyncio.get_running_loop().create_future()
        session._cua_confirm_future = fut
        session._cua_confirm_phrase = phrase
        try:
            await session.say(f"{phrase} Say yes or no.")
        except Exception as e:
            logger.warning(f"[cua.safety_confirm] session.say raised: {e}")
        try:
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.info(
                f"[cua.safety_confirm] timeout after {timeout_s}s; default-deny"
            )
            return False
        finally:
            session._cua_confirm_future = None
            session._cua_confirm_phrase = None
    return cb


class ComputerUseAgent(Agent):
    """LiveKit Agent that overrides on_enter to launch the
    computer_use loop. The supervisor handoff returns here after the
    loop emits task_done.

    The loop runs against a direct anthropic.AsyncAnthropic client,
    NOT through LiveKit's LLM adapter — see tools/computer_loop.py."""

    def __init__(self, *, spec, supervisor, chat_ctx, **kw):
        super().__init__(
            instructions=spec.instructions,
            tools=[],
            chat_ctx=chat_ctx,
            **kw,
        )
        self._spec = spec
        self._supervisor = supervisor

    async def on_enter(self) -> None:
        """Pull the user's last request from chat_ctx, run the loop,
        voice the summary, hand back to supervisor."""
        import os as _os
        from anthropic import AsyncAnthropic
        from tools.computer_loop import run as run_loop

        # Extract the user's request from the last user turn in chat_ctx.
        request = "GUI task"
        try:
            items = getattr(self.chat_ctx, "items", None) or []
            for item in reversed(items):
                if getattr(item, "role", None) == "user":
                    content = getattr(item, "content", None)
                    if isinstance(content, list) and content:
                        request = str(content[-1])[:500]
                    elif isinstance(content, str):
                        request = content[:500]
                    break
        except Exception as e:
            logger.warning(f"[computer_use.on_enter] chat_ctx extract failed: {e}")

        api_key = _os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            await self.session.say(
                "Computer-use needs an Anthropic API key — none configured."
            )
            return

        client = AsyncAnthropic(api_key=api_key)
        cancel = asyncio.Event()

        confirm_cb = build_safety_confirm_cb(self.session, timeout_s=30.0)

        try:
            result = await run_loop(
                task=request,
                anthropic_client=client,
                safety_confirm_cb=confirm_cb,
                cancel_event=cancel,
            )
        except Exception as e:
            logger.exception("[computer_use] loop raised")
            await self.session.say(f"Couldn't complete the task — {e}")
            return

        # Stash steps + cost on the session so jarvis_agent's per-turn
        # telemetry write can pick them up and write to the new
        # computer_use_steps / computer_use_cost_usd columns.
        try:
            self.session._jarvis_last_cua_steps = result.steps
            self.session._jarvis_last_cua_cost = result.cost_usd
        except Exception:
            pass

        # Voice the summary and let the supervisor's normal flow take over.
        await self.session.say(result.summary)


def register_computer_use() -> None:
    """Register the computer_use subagent — only when explicitly
    enabled via env. Default OFF until soak telemetry justifies."""
    if os.environ.get("JARVIS_SUBAGENT_COMPUTER_USE", "0") != "1":
        return
    register(HandoffSubagent(
        name="computer_use",
        transfer_tool="transfer_to_computer_use",
        when_to_use=(
            "Use when the task needs you to LOOK AT THE SCREEN, plan a "
            "click sequence by visual inspection, and execute it — "
            "driving an unfamiliar GUI app, navigating a multi-step "
            "dialog, reading on-screen text and acting on it, finding a "
            "widget you can't address by name. The model takes a "
            "screenshot, decides where to click, clicks, screenshots "
            "again. Trigger phrases: 'click the X menu', 'find the X "
            "button', 'open X and navigate to Y', 'look at my screen and "
            "Z', 'select the X option in the open Y dialog'. NOT for "
            "blind-coord OS actions like launching apps by name (use "
            "transfer_to_desktop) or web-page interactions (use "
            "transfer_to_browser)."
        ),
        instructions=COMPUTER_USE_INSTRUCTIONS,
        tool_factory=_computer_use_tools,
        ack_phrase="On it.",
        max_history_items=4,
        enabled=True,
        tools_required=False,   # tool-less; loop owns its own audit
        pre_transfer=_ensure_x11_session,
        agent_class=ComputerUseAgent,
    ))
    logger.info("[computer_use] subagent registered (env flag is ON)")

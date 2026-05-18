"""Computer-use iterate-until-done driver.

Owns the see-plan-act loop: screenshot → AT-SPI ground → Anthropic
plan → safety gate → execute → audit → repeat. Direct
anthropic.AsyncAnthropic client; NOT routed through LiveKit's LLM
adapter (the loop is many-turn, LiveKit is one-turn).

Spec: docs/superpowers/specs/2026-05-18-jarvis-computer-use-parity-design.md §4-5
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional


logger = logging.getLogger("jarvis.computer_loop")


__all__ = ["LoopResult", "run"]


# Anthropic pricing per million tokens, computer-use beta as of 2026-05-18.
# Used by _compute_cost to track per-call cost so we can enforce the
# budget cap. Refresh when Anthropic announces price changes.
_PRICING = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-7":   {"input": 15.0, "output": 75.0},
}


@dataclass
class LoopResult:
    ok: bool
    summary: str
    steps: int
    cost_usd: float
    reason: str   # "completed" | "budget" | "max_iters" | "blocked" | "bailed" | "interrupted"
    handoff_id: str


# ── seams for monkey-patching in tests ────────────────────────────
# These wrap the underlying functions so tests don't have to import
# the upstream symbol path. Production binds them to real impls below.

_anthropic_call: Optional[Callable[..., Awaitable]] = None
_take_screenshot: Optional[Callable[[], Awaitable[bytes]]] = None
_scale_for_model: Optional[Callable[[bytes], tuple[bytes, float, float]]] = None
_enumerate_widgets: Optional[Callable[[], list]] = None
_backend_click: Optional[Callable[..., Awaitable]] = None
_backend_type: Optional[Callable[..., Awaitable]] = None
_backend_key: Optional[Callable[..., Awaitable]] = None
_log_action: Optional[Callable[..., None]] = None
_is_password_visible: Optional[Callable[..., Awaitable[bool]]] = None
_parse_destructive: Optional[Callable[..., Optional[str]]] = None


def _bind_production_seams() -> None:
    """Wire the seams to their production implementations. Called at
    import time; tests overwrite the seams after import."""
    global _anthropic_call, _take_screenshot, _scale_for_model
    global _enumerate_widgets, _backend_click, _backend_type, _backend_key
    global _log_action, _is_password_visible, _parse_destructive

    from tools import computer_backend, computer_atspi
    from pipeline.turn_telemetry import log_computer_use_action
    from tools.computer_safety import (
        is_password_field_visible,
        parse_destructive_intent,
    )

    async def _do_anthropic(**kw):
        # kw includes `client` (we strip it before forwarding); allows
        # tests to monkeypatch _anthropic_call without holding a client.
        client = kw.pop("client", None)
        if client is None:
            raise RuntimeError("anthropic client missing")
        return await client.beta.messages.create(**kw)

    _anthropic_call = _do_anthropic
    _take_screenshot = computer_backend.take_screenshot
    _scale_for_model = computer_backend.scale_for_model
    _enumerate_widgets = computer_atspi.enumerate_widgets
    _backend_click = computer_backend.click
    _backend_type = computer_backend.type_text
    _backend_key = computer_backend.key_combo
    _log_action = log_computer_use_action
    _is_password_visible = is_password_field_visible
    _parse_destructive = parse_destructive_intent


_bind_production_seams()


def _compute_cost(usage, model: str) -> float:
    """Per-call USD cost from Anthropic usage block + model name.

    Includes cache-read tokens at 10% of the base input rate
    (Anthropic's documented cache-read pricing). Cache write is
    priced at 1.25x input and not yet tracked here — Anthropic's
    `cache_creation_input_tokens` field can be added later if soak
    telemetry shows large cache-write volume.
    """
    rates = _PRICING.get(model, {"input": 3.0, "output": 15.0})
    in_tokens = getattr(usage, "input_tokens", 0) or 0
    out_tokens = getattr(usage, "output_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    return (
        (in_tokens / 1_000_000) * rates["input"]
        + (out_tokens / 1_000_000) * rates["output"]
        + (cache_read / 1_000_000) * rates["input"] * 0.10
    )


def _png_to_image_block(png: bytes) -> dict:
    """Anthropic image content block, base64-encoded."""
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(png).decode("ascii"),
        },
    }


def _widgets_to_text(widgets: list) -> str:
    """Compact text representation of the AT-SPI widget list. Used as
    a prompt side-channel. Empty string when widgets is empty (sparse
    tree — model relies on bare vision)."""
    if not widgets:
        return ""
    lines = []
    for w in widgets[:80]:  # cap to first 80 to keep tokens bounded
        x, y, ww, wh = w.bounds
        lines.append(
            f"- {w.role}@({x},{y}) {ww}x{wh}: {w.text[:60]!r}"
        )
    return "Visible interactive widgets (AT-SPI):\n" + "\n".join(lines)


async def run(
    task: str,
    *,
    anthropic_client,
    safety_confirm_cb: Callable[[str], Awaitable[bool]],
    cancel_event: asyncio.Event,
    max_iters: int = 30,
    budget_usd: float = 0.50,
    wall_timeout_s: float = 180.0,
    model_primary: str = "claude-sonnet-4-6",
    model_escalation: str = "claude-opus-4-7",
    no_progress_escalation_after: int = 3,
) -> LoopResult:
    """See-plan-act loop. Returns LoopResult with a structured reason."""
    handoff_id = uuid.uuid4().hex[:12]
    active_model = model_primary
    cost_usd = 0.0
    steps = 0
    messages: list[dict] = []
    started_at = time.monotonic()

    # No-progress detection state: last N (screenshot_hash, action_key)
    # tuples. If all N match, we either escalate Sonnet→Opus (first
    # time) or bail with reason='blocked' (already escalated).
    progress_history: list[tuple[str, str]] = []
    escalated: bool = False

    # Initial screenshot + widgets → first user message
    png = await _take_screenshot()
    scaled, sx, sy = _scale_for_model(png)
    widgets = _enumerate_widgets()
    widget_text = _widgets_to_text(widgets)

    initial_text = (
        f"Task: {task}\n\n"
        f"{widget_text}" if widget_text else f"Task: {task}"
    )
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": initial_text},
            _png_to_image_block(scaled),
        ],
    })

    for iteration in range(1, max_iters + 1):
        # Cancel event (user barged in via voice or tray)
        if cancel_event.is_set():
            return LoopResult(
                ok=False,
                summary=f"user interrupted after {steps} steps",
                steps=steps, cost_usd=cost_usd,
                reason="interrupted", handoff_id=handoff_id,
            )

        # Wall-clock watchdog — bail if the loop has been running too long
        if (time.monotonic() - started_at) > wall_timeout_s:
            return LoopResult(
                ok=False,
                summary=f"wall-clock timeout ({wall_timeout_s:.0f}s) after {steps} steps",
                steps=steps, cost_usd=cost_usd,
                reason="bailed", handoff_id=handoff_id,
            )

        steps += 1

        # Safety pre-check: password field visible → hard-stop
        pw_visible = await _is_password_visible(scaled, widgets)
        if pw_visible:
            logger.warning(
                f"[cua:{handoff_id}] password field visible — hard-stop"
            )
            _log_action(
                handoff_id=handoff_id, step=iteration,
                model_used=active_model, action="bail",
                params_json=json.dumps({"reason": "password_visible"}),
                success=False, notes="password field detected; aborting",
            )
            return LoopResult(
                ok=False,
                summary="password / sensitive screen detected — handing back to supervisor",
                steps=steps, cost_usd=cost_usd,
                reason="blocked", handoff_id=handoff_id,
            )

        # Plan
        try:
            response = await _anthropic_call(
                client=anthropic_client,
                model=active_model,
                max_tokens=1024,
                tools=[{
                    "type": "computer_20251124",
                    "name": "computer",
                    "display_width_px": 1280,
                    "display_height_px": 800,
                    "display_number": 1,
                }],
                messages=messages,
                # The "computer-use-2025-11-24" header isn't yet in the
                # installed anthropic SDK's AnthropicBetaParam Literal
                # (v0.102.0 only knows -10-22 and -01-24), but it IS the
                # documented header for the computer_20251124 tool type
                # we pass below. Passing via extra_headers bypasses the
                # Literal validation. Bump the header string when
                # Anthropic announces a successor.
                extra_headers={"anthropic-beta": "computer-use-2025-11-24"},
            )
        except Exception as e:
            logger.warning(f"[cua:{handoff_id}] anthropic call failed: {e}")
            _log_action(
                handoff_id=handoff_id, step=iteration,
                model_used=active_model, action="api_error",
                params_json=json.dumps({"error": str(e)[:200]}),
                success=False, notes="anthropic call raised",
            )
            return LoopResult(
                ok=False, summary=f"API error: {e}",
                steps=steps, cost_usd=cost_usd,
                reason="bailed", handoff_id=handoff_id,
            )

        cost_usd += _compute_cost(response.usage, active_model)

        # Budget cap — bail when accumulated cost exceeds the budget.
        if cost_usd > budget_usd:
            _log_action(
                handoff_id=handoff_id, step=iteration,
                model_used=active_model, action="bail",
                params_json=json.dumps({"reason": "budget", "cost": cost_usd}),
                success=False, notes=f"budget breach: ${cost_usd:.4f} > ${budget_usd:.4f}",
            )
            return LoopResult(
                ok=False,
                summary=f"task exceeded ${budget_usd} budget after {steps} steps",
                steps=steps, cost_usd=cost_usd,
                reason="budget", handoff_id=handoff_id,
            )

        # Find the tool_use block in the response. Anthropic returns
        # BetaToolUseBlock objects with .type=="tool_use"; the dict path
        # covers test fixtures that use plain dicts.
        tool_use = None
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" or \
               (isinstance(block, dict) and block.get("type") == "tool_use"):
                tool_use = block
                break
        if tool_use is None:
            logger.warning(f"[cua:{handoff_id}] no tool_use in response")
            return LoopResult(
                ok=False, summary="model emitted no tool_use",
                steps=steps, cost_usd=cost_usd,
                reason="bailed", handoff_id=handoff_id,
            )

        # tool_use can be a real Anthropic BetaToolUseBlock (attribute
        # access) or a dict-shaped test fixture (subscript access).
        if isinstance(tool_use, dict):
            action_input = tool_use["input"]
        else:
            action_input = tool_use.input
        action_name = action_input.get("action") if isinstance(action_input, dict) else None

        # task_done = clean exit
        if action_name == "task_done":
            summary = action_input.get("summary", "")
            _log_action(
                handoff_id=handoff_id, step=iteration,
                model_used=active_model, action="task_done",
                params_json=json.dumps(action_input),
                success=True,
            )
            return LoopResult(
                ok=True, summary=summary,
                steps=steps, cost_usd=cost_usd,
                reason="completed", handoff_id=handoff_id,
            )

        # Destructive-intent gate: voice-confirm before executing the
        # action; on denial, skip + replan.
        confirm_phrase = _parse_destructive(
            {"action": action_name, **action_input}, widgets
        )
        if confirm_phrase is not None:
            try:
                user_ok = await asyncio.wait_for(
                    safety_confirm_cb(confirm_phrase),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                user_ok = False
            if not user_ok:
                _log_action(
                    handoff_id=handoff_id, step=iteration,
                    model_used=active_model, action=action_name,
                    params_json=json.dumps(action_input),
                    success=False, notes="user declined destructive action",
                )
                # Re-screenshot and re-append tool_result as "skipped"
                # so the model gets feedback to replan.
                png = await _take_screenshot()
                scaled, sx, sy = _scale_for_model(png)
                widgets = _enumerate_widgets()
                tool_use_id = (
                    getattr(tool_use, "id", None) or
                    (tool_use["id"] if isinstance(tool_use, dict) else "toolu_xyz")
                )
                messages.append({"role": "assistant", "content": [
                    {"type": "tool_use", "id": tool_use_id,
                     "name": "computer", "input": action_input},
                ]})
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tool_use_id,
                     "content": [
                         {"type": "text", "text":
                          "ERROR: user declined this destructive action — try a different approach"},
                         _png_to_image_block(scaled),
                     ]},
                ]})
                continue

        # Execute the action (happy path; safety + caps added in later tasks)
        success, notes = await _execute_action(
            action_name, action_input, sx, sy,
        )
        _log_action(
            handoff_id=handoff_id, step=iteration,
            model_used=active_model, action=action_name,
            params_json=json.dumps(action_input),
            success=success, notes=notes,
        )

        # Capture post-action screenshot for next iteration
        png = await _take_screenshot()
        scaled, sx, sy = _scale_for_model(png)
        widgets = _enumerate_widgets()
        # Append assistant turn + tool_result turn
        tool_use_id = (
            getattr(tool_use, "id", None) or
            (tool_use["id"] if isinstance(tool_use, dict) else "toolu_xyz")
        )
        messages.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": tool_use_id,
             "name": "computer", "input": action_input},
        ]})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_use_id,
             "content": [
                 {"type": "text", "text": "OK" if success else f"ERROR: {notes}"},
                 _png_to_image_block(scaled),
             ]},
        ]})

        # Update progress history. Hash the screenshot we just took
        # post-action, plus the action key (name + coord). If the last
        # N tuples all match, escalate or block.
        import hashlib
        scr_hash = hashlib.md5(scaled).hexdigest()[:12]
        coord = action_input.get("coordinate", [None, None])
        action_key = f"{action_name}:{coord[0]}:{coord[1]}"
        progress_history.append((scr_hash, action_key))
        if len(progress_history) > no_progress_escalation_after:
            progress_history.pop(0)
        if (
            len(progress_history) >= no_progress_escalation_after
            and all(
                progress_history[0] == p for p in progress_history
            )
        ):
            if not escalated:
                logger.info(
                    f"[cua:{handoff_id}] no progress {no_progress_escalation_after}"
                    f"x — escalating {active_model} → {model_escalation}"
                )
                active_model = model_escalation
                escalated = True
                # Reset history so we give Opus a fresh window before
                # bailing on its own stuckness.
                progress_history = []
            else:
                logger.warning(
                    f"[cua:{handoff_id}] still stuck after escalation; bailing"
                )
                return LoopResult(
                    ok=False,
                    summary=f"stuck on same action even after escalation to "
                            f"{model_escalation} ({steps} steps)",
                    steps=steps, cost_usd=cost_usd,
                    reason="blocked", handoff_id=handoff_id,
                )

    # Iteration cap hit without task_done
    return LoopResult(
        ok=False,
        summary=f"reached {max_iters} iterations without completing the task",
        steps=steps, cost_usd=cost_usd,
        reason="max_iters", handoff_id=handoff_id,
    )


async def _execute_action(
    name: str, params: dict, scale_x: float, scale_y: float,
) -> tuple[bool, Optional[str]]:
    """Dispatch one action to the backend. Returns (success, notes)."""
    try:
        if name == "left_click":
            x, y = params["coordinate"]
            await _backend_click(round(x * scale_x), round(y * scale_y))
        elif name == "type":
            await _backend_type(params.get("text", ""))
        elif name == "key":
            await _backend_key(params.get("text", ""))
        elif name in ("screenshot", "wait"):
            pass  # both are no-ops on our side; the loop will re-screenshot anyway
        else:
            return False, f"unknown action: {name}"
        return True, None
    except Exception as e:
        return False, str(e)[:200]

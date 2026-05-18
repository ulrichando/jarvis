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


def _bind_production_seams() -> None:
    """Wire the seams to their production implementations. Called at
    import time; tests overwrite the seams after import."""
    global _anthropic_call, _take_screenshot, _scale_for_model
    global _enumerate_widgets, _backend_click, _backend_type, _backend_key
    global _log_action

    from tools import computer_backend, computer_atspi
    from pipeline.turn_telemetry import log_computer_use_action

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


_bind_production_seams()


def _compute_cost(usage, model: str) -> float:
    """Per-call USD cost from Anthropic usage block + model name."""
    rates = _PRICING.get(model, {"input": 3.0, "output": 15.0})
    in_tokens = getattr(usage, "input_tokens", 0) or 0
    out_tokens = getattr(usage, "output_tokens", 0) or 0
    return (in_tokens / 1_000_000) * rates["input"] + \
           (out_tokens / 1_000_000) * rates["output"]


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
        steps += 1

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

        # Find the tool_use block in the response
        tool_use = None
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" or \
               (isinstance(block, dict) and block.get("type") == "tool_use") or \
               hasattr(block, "name"):
                tool_use = block
                break
        if tool_use is None:
            logger.warning(f"[cua:{handoff_id}] no tool_use in response")
            return LoopResult(
                ok=False, summary="model emitted no tool_use",
                steps=steps, cost_usd=cost_usd,
                reason="bailed", handoff_id=handoff_id,
            )

        action_name = (
            tool_use.input.get("action") if hasattr(tool_use, "input")
            else tool_use["input"]["action"]
        )
        action_input = (
            tool_use.input if hasattr(tool_use, "input")
            else tool_use["input"]
        )

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
            await _backend_click(int(x * scale_x), int(y * scale_y))
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

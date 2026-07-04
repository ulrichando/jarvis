"""AnthropicCUAdapter — Claude via the custom computer_use tool (SOM), plus
AnthropicNativeCUAdapter — Claude via the NATIVE computer_20251124 tool."""
from __future__ import annotations

import asyncio
import base64
import os
import struct
from typing import Any, Dict, List, Optional, Tuple

import anthropic

from .base import (CUAdapter, StepResult, ToolCall, ToolResult,
                   computer_use_description, computer_use_tool_params)

# Keep image blocks on at most this many of the most recent image-bearing user
# turns within a live run; older screenshots are stripped to a text placeholder
# so a 30-step loop stops re-sending ~30 screenshots every step (Anthropic
# guidance: keep the last ~3 screenshots). Override via JARVIS_CU_KEEP_LAST_IMAGES.
try:
    _KEEP_LAST_IMAGES = int(os.environ.get("JARVIS_CU_KEEP_LAST_IMAGES", "3") or "3")
except ValueError:
    _KEEP_LAST_IMAGES = 3


def _img_block(b64: str) -> Dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}


def _clean_blocks(blocks: List[Any]) -> List[Any]:
    """Drop image blocks (top-level + nested in tool_result content); keep a text
    placeholder so tool_use/tool_result pairing stays valid. Shared by export-time
    image stripping and in-run pruning."""
    out: List[Any] = []
    for b in blocks:
        if not isinstance(b, dict):
            out.append(b)
            continue
        if b.get("type") == "image":
            continue
        if b.get("type") == "tool_result" and isinstance(b.get("content"), list):
            out.append({**b, "content": _clean_blocks(b["content"]) or [{"type": "text", "text": "(screenshot)"}]})
        else:
            out.append(b)
    return out


def _turn_has_image(content: Any) -> bool:
    """True if a message's content list carries a screenshot (top-level image or
    an image nested inside a tool_result)."""
    if not isinstance(content, list):
        return False
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "image":
            return True
        if b.get("type") == "tool_result" and isinstance(b.get("content"), list):
            if any(isinstance(c, dict) and c.get("type") == "image" for c in b["content"]):
                return True
    return False


def _strip_images(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop image blocks from every turn (image-free session persistence);
    placeholder keeps tool_use/tool_result pairing valid."""
    res: List[Dict[str, Any]] = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            res.append({**m, "content": _clean_blocks(c) or [{"type": "text", "text": "(screenshot)"}]})
        else:
            res.append(dict(m))
    return res


class AnthropicCUAdapter(CUAdapter):
    def __init__(self, model: str, system: str, client: Optional[Any] = None) -> None:
        super().__init__(model, system)
        self._client = client or anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self._tool = {
            "name": "computer_use",
            "description": computer_use_description(),
            "input_schema": computer_use_tool_params(),
        }
        self.messages: List[Dict[str, Any]] = []

    def _effort_kwargs(self) -> Dict[str, Any]:
        """Computer-use effort, model-aware (Anthropic's benchmarked settings):
        Opus 4.7/4.8 → high; Sonnet 4.6 / Opus 4.6 → medium (best accuracy-to-cost;
        `max` adds cost without UI-task accuracy). Haiku / unknown → API default.
        Disable with JARVIS_CU_EFFORT_DISABLED=1."""
        if os.environ.get("JARVIS_CU_EFFORT_DISABLED", "").strip().lower() in {"1", "true", "on"}:
            return {}
        mid = self.model.lower()
        if "opus-4-7" in mid or "opus-4-8" in mid:
            effort = "high"
        elif "sonnet-4-6" in mid or "opus-4-6" in mid:
            effort = "medium"
        else:
            return {}
        return {"output_config": {"effort": effort}}

    def seed(self, task: str, image_b64: Optional[str]) -> None:
        content: List[Dict[str, Any]] = [{"type": "text", "text": task}]
        if image_b64:
            content.append(_img_block(image_b64))
        self.messages.append({"role": "user", "content": content})

    async def next_step(self) -> StepResult:
        # Cache the stable prefix (render order is tools → system → messages): a
        # cache_control breakpoint on the system block caches tools+system across
        # the whole 30-step loop, so the ~700-token tool schema + system prompt
        # aren't re-billed at full price every step.
        system_blocks = [{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}]
        tool = {**self._tool, "cache_control": {"type": "ephemeral"}}
        resp = await asyncio.to_thread(
            self._client.messages.create, model=self.model, max_tokens=4096,
            system=system_blocks, messages=self.messages, tools=[tool],
            **self._effort_kwargs())
        assistant: List[Dict[str, Any]] = []
        calls: List[ToolCall] = []
        text_out: Optional[str] = None
        for b in resp.content:
            if b.type == "text":
                assistant.append({"type": "text", "text": b.text})
                text_out = (text_out or "") + b.text
            elif b.type == "tool_use":
                assistant.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                args = dict(b.input) if isinstance(b.input, dict) else {}
                calls.append(ToolCall(id=b.id, action=str(args.get("action") or "?"), args=args))
        self.messages.append({"role": "assistant", "content": assistant})
        return StepResult(text=(text_out.strip() if text_out else None), calls=calls)

    def add_results(self, results: List[ToolResult]) -> None:
        blocks: List[Dict[str, Any]] = []
        for r in results:
            content: List[Dict[str, Any]] = [{"type": "text", "text": r.text}]
            if r.image_b64:
                content.append(_img_block(r.image_b64))
            blocks.append({"type": "tool_result", "tool_use_id": r.call_id, "content": content})
        self.messages.append({"role": "user", "content": blocks})
        self._prune_old_images()

    def _prune_old_images(self) -> None:
        """Keep screenshots only on the most recent _KEEP_LAST_IMAGES image-bearing
        user turns; strip older ones to a text placeholder. Bounds token growth
        within a run — pairing is preserved (only image blocks are removed)."""
        if _KEEP_LAST_IMAGES < 0:
            return
        kept = 0
        for m in reversed(self.messages):
            if m.get("role") != "user":
                continue
            content = m.get("content")
            if not _turn_has_image(content):
                continue
            if kept < _KEEP_LAST_IMAGES:
                kept += 1
                continue
            m["content"] = _clean_blocks(content) or [{"type": "text", "text": "(screenshot)"}]

    def export_history(self) -> Any:
        return _strip_images(self.messages)

    def import_history(self, history: Any) -> None:
        self.messages = list(history or [])


# ── Native computer_20251124 adapter ─────────────────────────────────────────

_NATIVE_BETA = "computer-use-2025-11-24"
_ALLOWED_MODIFIERS = {"ctrl", "shift", "alt", "super", "cmd", "option"}
# Native actions whose `text` param carries held modifier keys (not a payload).
_MODIFIER_TEXT_ACTIONS = {
    "left_click", "right_click", "middle_click", "double_click", "triple_click", "scroll",
}
# Native names the executor already speaks verbatim (coordinate-addressed).
_PASSTHROUGH_MOUSE = {
    "right_click", "middle_click", "double_click", "triple_click",
    "mouse_move", "left_mouse_down", "left_mouse_up", "cursor_position",
}


def _png_dims(png_b64: Optional[str]) -> Optional[Tuple[int, int]]:
    """(width, height) from a base64 PNG's IHDR header — no image decode."""
    if not png_b64 or len(png_b64) < 44:
        return None
    try:
        head = base64.b64decode(png_b64[:64])
        if head[:8] != b"\x89PNG\r\n\x1a\n" or len(head) < 24:
            return None
        w, h = struct.unpack(">II", head[16:24])
        return (int(w), int(h)) if w and h else None
    except Exception:  # noqa: BLE001
        return None


def _parse_modifier_text(text: Any) -> Optional[List[str]]:
    """Native click/scroll pass held modifiers in `text` (e.g. 'ctrl+shift')."""
    if not text or not isinstance(text, str):
        return None
    out = [t.strip().lower() for t in text.split("+") if t.strip().lower() in _ALLOWED_MODIFIERS]
    return out or None


class AnthropicNativeCUAdapter(AnthropicCUAdapter):
    """Claude driving the NATIVE ``computer_20251124`` tool (screenshot / clicks /
    zoom, beta ``computer-use-2025-11-24``), translated onto the shared executor.

    Native means: the trained-for tool contract, the zoom action, and Anthropic's
    screenshot prompt-injection classifiers (which only run on the native beta).
    The model sees RAW frames (``frame_mode = 'vision'`` — no SOM overlays) and
    emits pixel coordinates in the frame's space; ``_translate`` scales them back
    to native screen space and renames actions/args to the COMPUTER_USE_SCHEMA
    vocabulary so ``handle_computer_use`` (tier gate, blocklist, audit) is
    unchanged. Reuses the SOM adapter's caching, effort, image pruning, and
    history handling wholesale.
    """

    frame_mode = "vision"

    def __init__(self, model: str, system: str, client: Optional[Any] = None) -> None:
        super().__init__(model, system, client=client)
        # Set by the loop after the first full-screen capture; frames are the
        # (possibly downscaled) copies the model sees. Both fixed for the run.
        self.screen_size: Optional[Tuple[int, int]] = None
        self._frame_size: Optional[Tuple[int, int]] = None

    def seed(self, task: str, image_b64: Optional[str]) -> None:
        # Frame geometry is recorded ONLY here: mid-run captures can be zoom
        # crops whose dims must not poison the display declaration / scale.
        dims = _png_dims(image_b64)
        if dims:
            self._frame_size = dims
        super().seed(task, image_b64)

    def _scale(self) -> Tuple[float, float]:
        if not (self.screen_size and self._frame_size):
            return 1.0, 1.0
        fw, fh = self._frame_size
        sw, sh = self.screen_size
        if fw <= 0 or fh <= 0:
            return 1.0, 1.0
        return sw / float(fw), sh / float(fh)

    def _native_tool(self) -> Dict[str, Any]:
        w, h = self._frame_size or self.screen_size or (1280, 800)
        return {
            "type": "computer_20251124", "name": "computer",
            "display_width_px": int(w), "display_height_px": int(h),
            "enable_zoom": True,
        }

    def _translate(self, inp: Dict[str, Any]) -> Dict[str, Any]:
        """Native computer_20251124 input → COMPUTER_USE_SCHEMA args."""
        a = str(inp.get("action") or "")
        sx, sy = self._scale()

        def pt(c: Any) -> Optional[List[int]]:
            try:
                return [int(round(float(c[0]) * sx)), int(round(float(c[1]) * sy))]
            except Exception:  # noqa: BLE001
                return None

        coord = pt(inp.get("coordinate")) if inp.get("coordinate") is not None else None
        mods = _parse_modifier_text(inp.get("text")) if a in _MODIFIER_TEXT_ACTIONS else None

        if a == "screenshot":
            return {"action": "capture", "mode": "vision"}
        if a == "zoom":
            r = inp.get("region")
            if isinstance(r, (list, tuple)) and len(r) == 4:
                try:
                    region = [
                        int(round(float(r[0]) * sx)), int(round(float(r[1]) * sy)),
                        int(round(float(r[2]) * sx)), int(round(float(r[3]) * sy)),
                    ]
                    return {"action": "capture", "mode": "vision", "region": region}
                except Exception:  # noqa: BLE001
                    pass
            return {"action": "capture", "mode": "vision"}
        if a == "type":
            return {"action": "type", "text": inp.get("text") or ""}
        if a == "key":
            return {"action": "key", "keys": inp.get("text") or ""}
        if a == "hold_key":
            return {"action": "hold_key", "keys": inp.get("text") or "",
                    "seconds": inp.get("duration", 1)}
        if a == "wait":
            return {"action": "wait", "seconds": inp.get("duration", 1)}

        out: Dict[str, Any]
        if a == "left_click":
            out = {"action": "click", "coordinate": coord, "button": "left"}
        elif a in _PASSTHROUGH_MOUSE:
            # cursor_position note: the executor answers in native screen coords;
            # exact when scale is 1.0 (hi-res models — the common case).
            out = {"action": a, "coordinate": coord}
        elif a == "left_click_drag":
            out = {"action": "drag",
                   "from_coordinate": pt(inp.get("start_coordinate")),
                   "to_coordinate": coord}
        elif a == "scroll":
            out = {"action": "scroll", "coordinate": coord,
                   "direction": inp.get("scroll_direction") or "down",
                   "amount": inp.get("scroll_amount", 3)}
        else:
            return dict(inp)  # unknown → executor returns a clear error, model adapts
        if mods:
            out["modifiers"] = mods
        return {k: v for k, v in out.items() if v is not None}

    async def next_step(self) -> StepResult:
        # Same prefix-cache shape as the SOM adapter: breakpoint on the system
        # block caches tools+system across the loop (tools render first).
        system_blocks = [{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}]
        resp = await asyncio.to_thread(
            self._client.messages.create, model=self.model, max_tokens=4096,
            system=system_blocks, messages=self.messages, tools=[self._native_tool()],
            extra_headers={"anthropic-beta": _NATIVE_BETA},
            **self._effort_kwargs())
        assistant: List[Dict[str, Any]] = []
        calls: List[ToolCall] = []
        text_out: Optional[str] = None
        for b in resp.content:
            if b.type == "text":
                assistant.append({"type": "text", "text": b.text})
                text_out = (text_out or "") + b.text
            elif b.type == "tool_use":
                # History echoes the ORIGINAL native input; only the executor
                # sees the translated args.
                assistant.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                args = self._translate(dict(b.input) if isinstance(b.input, dict) else {})
                calls.append(ToolCall(id=b.id, action=str(args.get("action") or "?"), args=args))
        self.messages.append({"role": "assistant", "content": assistant})
        return StepResult(text=(text_out.strip() if text_out else None), calls=calls)

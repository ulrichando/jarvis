"""AnthropicCUAdapter — Claude via the custom computer_use tool (SOM)."""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

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

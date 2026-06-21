"""AnthropicCUAdapter — Claude via the custom computer_use tool (SOM)."""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

import anthropic

from .base import (CUAdapter, StepResult, ToolCall, ToolResult,
                   computer_use_description, computer_use_tool_params)


def _img_block(b64: str) -> Dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}


def _strip_images(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop image blocks (top-level + nested in tool_result content); placeholder
    keeps tool_use/tool_result pairing valid."""
    def clean(blocks: List[Any]) -> List[Any]:
        out: List[Any] = []
        for b in blocks:
            if not isinstance(b, dict):
                out.append(b)
                continue
            if b.get("type") == "image":
                continue
            if b.get("type") == "tool_result" and isinstance(b.get("content"), list):
                out.append({**b, "content": clean(b["content"]) or [{"type": "text", "text": "(screenshot)"}]})
            else:
                out.append(b)
        return out

    res: List[Dict[str, Any]] = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            res.append({**m, "content": clean(c) or [{"type": "text", "text": "(screenshot)"}]})
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

    def seed(self, task: str, image_b64: Optional[str]) -> None:
        content: List[Dict[str, Any]] = [{"type": "text", "text": task}]
        if image_b64:
            content.append(_img_block(image_b64))
        self.messages.append({"role": "user", "content": content})

    async def next_step(self) -> StepResult:
        resp = await asyncio.to_thread(
            self._client.messages.create, model=self.model, max_tokens=4096,
            system=self.system, messages=self.messages, tools=[self._tool])
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

    def export_history(self) -> Any:
        return _strip_images(self.messages)

    def import_history(self, history: Any) -> None:
        self.messages = list(history or [])

"""OpenAICUAdapter — GPT-5.5 driving the custom computer_use tool.

Uses Chat Completions with a function tool + image_url content (the broadly
supported function-calling + vision surface; GPT-5.5 supports both). GPT-5.5
also exposes NATIVE computer use via the Responses API — a future upgrade.

NOTE (verify live): the exact request/response shape should be confirmed against
the installed `openai` SDK with a real OPENAI_API_KEY before trusting in prod —
the parsing here is unit-tested with a mock, not a live call.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Optional

import openai

from .base import (CUAdapter, StepResult, ToolCall, ToolResult,
                   computer_use_description, computer_use_tool_params)


def _img_part(b64: str) -> Dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}


def _strip(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop image_url parts from history (only the current screen matters next turn)."""
    out: List[Dict[str, Any]] = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            kept = [b for b in c if not (isinstance(b, dict) and b.get("type") == "image_url")]
            out.append({**m, "content": kept or [{"type": "text", "text": "(screenshot)"}]})
        else:
            out.append(dict(m))
    return out


class OpenAICUAdapter(CUAdapter):
    def __init__(self, model: str, system: str, client: Optional[Any] = None) -> None:
        super().__init__(model, system)
        self._client = client or openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        self._tools = [{
            "type": "function",
            "function": {
                "name": "computer_use",
                "description": computer_use_description(),
                "parameters": computer_use_tool_params(),
            },
        }]
        self.messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]

    def seed(self, task: str, image_b64: Optional[str]) -> None:
        content: List[Dict[str, Any]] = [{"type": "text", "text": task}]
        if image_b64:
            content.append(_img_part(image_b64))
        self.messages.append({"role": "user", "content": content})

    async def next_step(self) -> StepResult:
        resp = await asyncio.to_thread(
            self._client.chat.completions.create,
            model=self.model, messages=self.messages, tools=self._tools)
        msg = resp.choices[0].message
        raw_tcs = getattr(msg, "tool_calls", None) or []
        self.messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [{
                "id": tc.id, "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            } for tc in raw_tcs],
        })
        calls: List[ToolCall] = []
        for tc in raw_tcs:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=tc.id, action=str(args.get("action") or "?"), args=args))
        return StepResult(text=(msg.content or None), calls=calls)

    def add_results(self, results: List[ToolResult]) -> None:
        # The tool role carries the textual result; the screenshot follows as a
        # user image (the tool role can't hold images in Chat Completions).
        for r in results:
            self.messages.append({"role": "tool", "tool_call_id": r.call_id, "content": r.text})
        imgs = [_img_part(r.image_b64) for r in results if r.image_b64]
        if imgs:
            self.messages.append({"role": "user", "content": [{"type": "text", "text": "Current screen:"}, *imgs]})

    def export_history(self) -> Any:
        return _strip(self.messages)

    def import_history(self, history: Any) -> None:
        self.messages = list(history or [{"role": "system", "content": self.system}])

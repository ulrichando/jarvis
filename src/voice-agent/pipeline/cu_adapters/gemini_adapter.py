"""GeminiCUAdapter — Gemini driving the custom computer_use tool.

Verified against google-genai 2.6.0: Client(api_key=), types.Tool /
FunctionDeclaration / GenerateContentConfig / Content / Part,
Part.from_bytes, Part.from_function_response.

v1 limitation: Content objects aren't trivially serialisable, so Gemini history
is in-process only (continuity within a run, not persisted across /run calls).
NOTE (verify live): confirm the model id (gemini-3-flash-preview / Gemini 2.5
Computer Use) + a real call with GEMINI_API_KEY — parsing is mock-tested only.
"""
from __future__ import annotations

import asyncio
import base64
import os
from typing import Any, List, Optional

from google import genai
from google.genai import types

from .base import (CUAdapter, StepResult, ToolCall, ToolResult,
                   computer_use_description, computer_use_tool_params)


class GeminiCUAdapter(CUAdapter):
    def __init__(self, model: str, system: str, client: Optional[Any] = None) -> None:
        super().__init__(model, system)
        self._client = client or genai.Client(
            api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        self._tool = types.Tool(function_declarations=[types.FunctionDeclaration(
            name="computer_use",
            description=computer_use_description(),
            parameters=computer_use_tool_params())])
        self.contents: List[Any] = []

    def _img(self, b64: str) -> Any:
        return types.Part.from_bytes(data=base64.b64decode(b64), mime_type="image/png")

    def seed(self, task: str, image_b64: Optional[str]) -> None:
        parts = [types.Part(text=task)]
        if image_b64:
            parts.append(self._img(image_b64))
        self.contents.append(types.Content(role="user", parts=parts))

    async def next_step(self) -> StepResult:
        resp = await asyncio.to_thread(
            self._client.models.generate_content,
            model=self.model, contents=self.contents,
            config=types.GenerateContentConfig(system_instruction=self.system, tools=[self._tool]))
        cand = resp.candidates[0]
        self.contents.append(cand.content)
        calls: List[ToolCall] = []
        text_out: Optional[str] = None
        for p in cand.content.parts:
            if getattr(p, "text", None):
                text_out = (text_out or "") + p.text
            fc = getattr(p, "function_call", None)
            if fc:
                args = dict(fc.args or {})
                calls.append(ToolCall(id=getattr(fc, "name", "computer_use"),
                                      action=str(args.get("action") or "?"), args=args))
        return StepResult(text=(text_out.strip() if text_out else None), calls=calls)

    def add_results(self, results: List[ToolResult]) -> None:
        parts: List[Any] = [
            types.Part.from_function_response(name=r.call_id, response={"result": r.text})
            for r in results
        ]
        for r in results:
            if r.image_b64:
                parts.append(self._img(r.image_b64))
        self.contents.append(types.Content(role="user", parts=parts))

    def export_history(self) -> Any:
        return None  # Content objects not persisted across runs (v1 limitation)

    def import_history(self, history: Any) -> None:
        return

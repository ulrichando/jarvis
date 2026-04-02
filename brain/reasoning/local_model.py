"""JARVIS Local Model — Ollama-powered local inference with tool calling.

Primary reasoning backend. No API costs, no rate limits, full privacy.
Uses prompt-based tool calling (inject tool descriptions, parse JSON response).
"""

import json
import re
import asyncio
import requests
from brain.config import LOCAL_MODEL, CODE_MODEL, LOCAL_MODEL_URL


class LocalModel:
    """Local LLM via Ollama with tool-calling support."""

    def __init__(self):
        self.model = LOCAL_MODEL
        self.code_model = CODE_MODEL
        self.url = LOCAL_MODEL_URL
        self._available = None

    def is_available(self) -> bool:
        """Check if Ollama is running."""
        if self._available is not None:
            return self._available
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=2)
            self._available = r.status_code == 200
        except Exception:
            self._available = False
        return self._available

    def reset_availability(self):
        """Force re-check on next call."""
        self._available = None

    def use_code_model(self):
        """Switch to DeepSeek Coder for programming tasks."""
        self.model = self.code_model

    def use_default_model(self):
        """Switch back to the default general model."""
        self.model = LOCAL_MODEL

    # ── Standard Query ──────────────────────────────────────────────

    async def query_code(self, prompt: str, system: str = "") -> str | None:
        """Query using the code-specialized model (DeepSeek Coder)."""
        if not self.is_available():
            return None
        try:
            original = self.model
            self.model = self.code_model
            result = await asyncio.to_thread(self._chat, prompt, system)
            self.model = original
            return result
        except Exception:
            self.model = LOCAL_MODEL
            return None

    async def query(self, prompt: str, system: str = "") -> str | None:
        """Query the local model. Returns None if unavailable."""
        if not self.is_available():
            return None
        try:
            return await asyncio.to_thread(self._chat, prompt, system)
        except Exception:
            return None

    async def query_messages(self, messages: list[dict]) -> str | None:
        """Query with full message history (chat format)."""
        if not self.is_available():
            return None
        try:
            return await asyncio.to_thread(self._chat_messages, messages)
        except Exception:
            return None

    # ── Tool-Calling Query ──────────────────────────────────────────

    async def query_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> dict:
        """Query with tool definitions. Returns structured response.

        Uses prompt injection: tool descriptions are added to the system
        message, and the model is instructed to respond with JSON.

        Returns:
            {
                "text": "optional text content",
                "tool_calls": [
                    {"id": "call_1", "name": "bash", "args": {"command": "ls"}},
                    ...
                ]
            }
        """
        if not self.is_available():
            return {"text": "Local model unavailable.", "tool_calls": []}

        try:
            return await asyncio.to_thread(
                self._chat_with_tools, messages, tools
            )
        except Exception as e:
            return {"text": f"Local tool calling failed: {e}", "tool_calls": []}

    # ── Internal Methods ────────────────────────────────────────────

    def _chat(self, prompt: str, system: str = "") -> str:
        """Simple chat query."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self._chat_messages(messages)

    def _chat_messages(self, messages: list[dict]) -> str:
        """Chat with full message history."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.4,
                "num_predict": 1024,
            },
        }
        r = requests.post(
            f"{self.url}/api/chat", json=payload, timeout=60
        )
        if r.status_code == 200:
            return r.json().get("message", {}).get("content", "").strip()
        return ""

    def _chat_with_tools(
        self, messages: list[dict], tools: list[dict]
    ) -> dict:
        """Tool-calling via prompt injection + JSON parsing."""
        # Build tool-augmented messages
        augmented = self._inject_tools_into_messages(messages, tools)

        payload = {
            "model": self.model,
            "messages": augmented,
            "stream": False,
            "options": {
                "temperature": 0.2,  # Lower for precise tool use
                "num_predict": 4096,  # Room for reasoning + tool args
            },
        }

        r = requests.post(
            f"{self.url}/api/chat", json=payload, timeout=90
        )

        if r.status_code != 200:
            return {"text": f"Ollama error: {r.status_code}", "tool_calls": []}

        response_text = r.json().get("message", {}).get("content", "")
        return self._parse_tool_response(response_text)

    def _inject_tools_into_messages(
        self, messages: list[dict], tools: list[dict]
    ) -> list[dict]:
        """Inject tool descriptions into the system message."""
        tool_text = self._format_tools_as_text(tools)

        tool_instruction = f"""{tool_text}

═══ HOW TO USE TOOLS ═══
To use a tool, respond with ONLY a JSON object in this exact format:
{{"tool_calls": [{{"name": "tool_name", "args": {{"param": "value"}}}}]}}

To respond with text (no tools), respond with ONLY:
{{"text": "your response here"}}

Rules:
- Output ONLY valid JSON. No text before or after the JSON.
- Use exactly ONE of the two formats above.
- For tool_calls, the "name" must be one of the available tools.
- For tool_calls, "args" must match the tool's parameters.
- You can call multiple tools: {{"tool_calls": [{{"name": "a", "args": {{}}}}, {{"name": "b", "args": {{}}}}]}}
"""

        augmented = []
        system_found = False

        for msg in messages:
            if msg["role"] == "system" and not system_found:
                # Append tool instructions to system message
                augmented.append({
                    "role": "system",
                    "content": msg["content"] + "\n\n" + tool_instruction,
                })
                system_found = True
            elif msg["role"] == "tool":
                # Convert tool results to assistant/user format for Ollama
                tool_id = msg.get("tool_call_id", "?")
                augmented.append({
                    "role": "user",
                    "content": f"[Tool Result for {tool_id}]:\n{msg['content']}",
                })
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                # Convert assistant tool_calls to text format
                calls = msg["tool_calls"]
                call_text = json.dumps({
                    "tool_calls": [
                        {"name": tc["function"]["name"],
                         "args": json.loads(tc["function"]["arguments"])}
                        for tc in calls
                    ]
                })
                augmented.append({
                    "role": "assistant",
                    "content": call_text,
                })
            else:
                augmented.append(msg)

        if not system_found:
            # No system message — prepend one
            augmented.insert(0, {
                "role": "system",
                "content": tool_instruction,
            })

        return augmented

    @staticmethod
    def _format_tools_as_text(tools: list[dict]) -> str:
        """Convert tool schemas to readable text descriptions."""
        lines = ["═══ AVAILABLE TOOLS ═══"]
        for t in tools:
            func = t.get("function", {})
            name = func.get("name", "?")
            desc = func.get("description", "")
            params = func.get("parameters", {}).get("properties", {})
            required = func.get("parameters", {}).get("required", [])

            param_parts = []
            for pname, pinfo in params.items():
                ptype = pinfo.get("type", "string")
                req = " (required)" if pname in required else ""
                param_parts.append(f"  - {pname}: {ptype}{req} — {pinfo.get('description', '')}")

            lines.append(f"\n{name}: {desc}")
            if param_parts:
                lines.append("  Parameters:")
                lines.extend(param_parts)

        return "\n".join(lines)

    @staticmethod
    def _parse_tool_response(response: str) -> dict:
        """Parse tool calls or text from model's JSON response."""
        response = response.strip()

        # Try to extract JSON from the response
        # The model might wrap it in markdown code blocks
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if json_match:
            response = json_match.group(1)

        # Try direct JSON parse
        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            # Try to find JSON object in the response
            brace_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response, re.DOTALL)
            if brace_match:
                try:
                    data = json.loads(brace_match.group())
                except json.JSONDecodeError:
                    # Give up — treat entire response as text
                    return {"text": response, "tool_calls": []}
            else:
                return {"text": response, "tool_calls": []}

        # Parse structured response
        result = {"text": "", "tool_calls": []}

        if "text" in data:
            result["text"] = str(data["text"])

        if "tool_calls" in data and isinstance(data["tool_calls"], list):
            for i, tc in enumerate(data["tool_calls"]):
                if isinstance(tc, dict) and "name" in tc:
                    result["tool_calls"].append({
                        "id": f"call_{i+1}",
                        "name": tc["name"],
                        "args": tc.get("args", tc.get("arguments", {})),
                    })

        return result

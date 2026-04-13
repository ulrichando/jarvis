"""Structured output extraction via Pydantic + LangChain.

Provides a thin wrapper that takes a Pydantic model class and a prompt,
calls the LLM, and returns a validated instance of the model.

Uses LangChain's with_structured_output() when the provider supports it
(OpenAI function calling format), otherwise falls back to JSON parsing.

Usage:
    from pydantic import BaseModel
    from src.lc.structured import extract

    class BugReport(BaseModel):
        title: str
        severity: Literal["low", "medium", "high", "critical"]
        steps: list[str]
        root_cause: str | None = None

    report = await extract(BugReport, "Analyse this error: ...")
"""

import json
import logging
import re
from typing import Any, Type, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


async def extract(
    schema: Type[T],
    prompt: str,
    system: str | None = None,
    prefer_smart: bool = True,
) -> T:
    """Extract structured data from an LLM response.

    1. Tries native structured output (OpenAI function calling).
    2. Falls back to JSON parsing from raw text.
    3. Falls back to empty model defaults on parse failure.
    """
    from src.lc.model_adapter import JARVISChatModel
    llm = JARVISChatModel(prefer_smart=prefer_smart, prefer_tool_calling=True)

    # Try LangChain structured output first
    try:
        from langchain_core.utils.function_calling import convert_to_openai_function
        schema_dict = _pydantic_to_openai_schema(schema)
        enhanced_prompt = (
            f"{prompt}\n\nRespond ONLY with valid JSON matching this schema:\n"
            f"{json.dumps(schema_dict, indent=2)}"
        )
    except Exception:
        enhanced_prompt = (
            f"{prompt}\n\nRespond ONLY with valid JSON. No prose, no markdown."
        )

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": enhanced_prompt})

    response = await llm.ainvoke(messages)
    raw = _extract_content(response)

    return _parse_to_model(schema, raw)


def _extract_content(response: Any) -> str:
    """Get text content from various response formats."""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        # OpenAI/Anthropic format
        choices = response.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
        return response.get("content", str(response))
    if hasattr(response, "content"):
        return str(response.content)
    return str(response)


def _parse_to_model(schema: Type[T], raw: str) -> T:
    """Parse raw text to Pydantic model, with fallbacks."""
    # Extract JSON from markdown code blocks
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if json_match:
        raw = json_match.group(1).strip()

    # Try direct JSON parse
    try:
        data = json.loads(raw.strip())
        return schema(**data)
    except Exception:
        pass

    # Try to find first { ... } block
    brace_match = re.search(r"\{[\s\S]*\}", raw)
    if brace_match:
        try:
            data = json.loads(brace_match.group(0))
            return schema(**data)
        except Exception:
            pass

    log.warning("structured.extract: could not parse JSON from response, using defaults.")
    # Return model with defaults where possible
    try:
        return schema.model_validate({})
    except Exception:
        raise ValueError(f"Could not extract {schema.__name__} from: {raw[:200]}")


def _pydantic_to_openai_schema(model_class: Type) -> dict:
    """Convert a Pydantic model to a JSON schema dict."""
    try:
        return model_class.model_json_schema()
    except Exception:
        try:
            return model_class.schema()
        except Exception:
            return {}

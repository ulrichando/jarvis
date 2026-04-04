"""SyntheticOutputTool -- generates synthetic output for testing/demo."""
from __future__ import annotations

from typing import Any

SYNTHETIC_OUTPUT_TOOL_NAME = "SyntheticOutput"


async def synthetic_output(content: str, **kwargs: Any) -> dict[str, Any]:
    """Generate synthetic output. Stub."""
    return {"type": "text", "text": content}

"""CUAdapter — provider-agnostic computer-use step interface.

Each provider adapter owns its SDK's message/tool/image format and parses
tool-calls into the uniform vocab; the loop in computer_use_service stays
provider-agnostic. The action vocab is the custom COMPUTER_USE_SCHEMA enum, so
handle_computer_use is unchanged for every provider.
"""
from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolCall:
    id: str
    action: str
    args: Dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    text: str
    image_b64: Optional[str] = None


@dataclass
class StepResult:
    text: Optional[str]
    calls: List[ToolCall] = field(default_factory=list)


def strictify(node: Any) -> Any:
    """Recursively set additionalProperties:false on every object node (Anthropic
    rejects tool schemas without it; harmless for the others)."""
    if isinstance(node, dict):
        out = {k: strictify(v) for k, v in node.items()}
        if out.get("type") == "object" and "additionalProperties" not in out:
            out["additionalProperties"] = False
        return out
    if isinstance(node, list):
        return [strictify(v) for v in node]
    return node


def computer_use_tool_params() -> Dict[str, Any]:
    """The COMPUTER_USE_SCHEMA parameters object (strictified), shared by all
    adapters. Imported lazily to avoid a hard dep at module import."""
    from tools.computer_use import COMPUTER_USE_SCHEMA
    params = COMPUTER_USE_SCHEMA.get("parameters") or {"type": "object", "properties": {}}
    return strictify(copy.deepcopy(params))


def computer_use_description() -> str:
    from tools.computer_use import COMPUTER_USE_SCHEMA
    return COMPUTER_USE_SCHEMA["description"]


class CUAdapter(ABC):
    """One turn-driver per provider. Owns the provider-format conversation state."""

    def __init__(self, model: str, system: str) -> None:
        self.model = model
        self.system = system

    @abstractmethod
    def seed(self, task: str, image_b64: Optional[str]) -> None:
        """Append the first user turn (task text + optional screenshot)."""

    @abstractmethod
    async def next_step(self) -> StepResult:
        """Call the model; append the assistant turn; return text + tool calls."""

    @abstractmethod
    def add_results(self, results: List[ToolResult]) -> None:
        """Append tool results (each with the post-action screenshot) as the next
        user turn."""

    @abstractmethod
    def export_history(self) -> Any:
        """Image-free history snapshot for session persistence (None if unsupported)."""

    @abstractmethod
    def import_history(self, history: Any) -> None:
        """Restore a prior image-free history before seeding the new turn."""

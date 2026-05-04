"""Typed schemas for the three blackboard channel families.

  - ScreenFact   — written by vision_tap, key `screen:<surface>`,  TTL 30s
  - ToolResult   — written by specialists, key `tool:<call_id>`,    no TTL within session
  - Intent       — written by classify_node, key `intent:<turn_id>`, no TTL within session

Designed for stable JSON serialization (Pydantic v2 model_dump_json /
model_validate_json) so we can store them as strings in Redis.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ScreenFact(BaseModel):
    """One observation of the user's active screen. Vision_tap writes
    these on screen-change events or every 30s. The supervisor reads
    the freshest non-stale fact when the user references 'this',
    'that', 'screen', 'page', etc."""
    active_app: Optional[str] = None
    foreground_url: Optional[str] = None
    tab_count: Optional[int] = None
    dom_summary: Optional[str] = None
    uncertain: bool = False
    reason: Optional[str] = None
    captured_at: float = Field(default_factory=lambda: 0.0)


class ToolResult(BaseModel):
    """One specialist tool dispatch outcome. Written by RegistrySpecialist
    when each ext_*/web_search/transfer_to_X completes (success or
    failure). The grounding_gate reads these to validate past-tense
    claims in supervisor output."""
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    ok: bool = True
    ts: float = 0.0
    call_id: str = ""


class Intent(BaseModel):
    """One user-turn classification record. Written by classify_node.
    Diagnostic — not load-bearing for grounding, but useful for telemetry
    and post-hoc analysis."""
    turn_id: str
    route: str
    confidence: float
    raw_text: str
    ts: float = 0.0

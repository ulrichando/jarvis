"""Zero-initialized usage object."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ServerToolUse:
    web_search_requests: int = 0
    web_fetch_requests: int = 0


@dataclass
class CacheCreation:
    ephemeral_1h_input_tokens: int = 0
    ephemeral_5m_input_tokens: int = 0


@dataclass
class Usage:
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int = 0
    server_tool_use: ServerToolUse = field(default_factory=ServerToolUse)
    service_tier: str = "standard"
    cache_creation: CacheCreation = field(default_factory=CacheCreation)
    inference_geo: str = ""
    iterations: List = field(default_factory=list)
    speed: str = "standard"


EMPTY_USAGE = Usage()

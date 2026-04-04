"""MCP type definitions -- converted from TypeScript."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


@dataclass
class MCPServerConnection:
    type: str = ""  # 'connected' | 'disconnected' | 'connecting'
    name: str = ""
    server_type: Optional[str] = None
    base_url: Optional[str] = None

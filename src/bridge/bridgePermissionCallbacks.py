"""Bridge permission callback types and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol


@dataclass
class BridgePermissionResponse:
    behavior: str  # 'allow' | 'deny'
    updated_input: Optional[dict[str, Any]] = None
    updated_permissions: Optional[list[dict]] = None
    message: Optional[str] = None


class BridgePermissionCallbacks(Protocol):
    def send_request(
        self, request_id: str, tool_name: str, input_data: dict,
        tool_use_id: str, description: str,
        permission_suggestions: Optional[list] = None,
        blocked_path: Optional[str] = None,
    ) -> None: ...
    def send_response(self, request_id: str, response: BridgePermissionResponse) -> None: ...
    def cancel_request(self, request_id: str) -> None: ...
    def on_response(self, request_id: str, handler: Callable) -> Callable: ...


def is_bridge_permission_response(value: Any) -> bool:
    """Validate a parsed control_response payload as a BridgePermissionResponse."""
    if not isinstance(value, dict):
        return False
    return value.get("behavior") in ("allow", "deny")

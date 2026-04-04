"""Shared transport-layer helpers for bridge message handling."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def is_sdk_message(value: Any) -> bool:
    """Type predicate for parsed WebSocket messages."""
    return isinstance(value, dict) and isinstance(value.get("type"), str)


def is_sdk_control_response(value: Any) -> bool:
    """Type predicate for control_response messages from the server."""
    return (isinstance(value, dict) and value.get("type") == "control_response"
            and "response" in value)


def is_sdk_control_request(value: Any) -> bool:
    """Type predicate for control_request messages from the server."""
    return (isinstance(value, dict) and value.get("type") == "control_request"
            and "request_id" in value and "request" in value)


def is_eligible_bridge_message(m: dict) -> bool:
    """True for message types that should be forwarded to the bridge transport."""
    msg_type = m.get("type")
    if msg_type in ("user", "assistant") and m.get("isVirtual"):
        return False
    return msg_type in ("user", "assistant") or (
        msg_type == "system" and m.get("subtype") == "local_command"
    )


def extract_title_text(m: dict) -> Optional[str]:
    """Extract title-worthy text from a Message for onUserMessage."""
    if m.get("type") != "user" or m.get("isMeta") or m.get("toolUseResult") or m.get("isCompactSummary"):
        return None
    origin = m.get("origin")
    if origin and origin.get("kind") != "human":
        return None
    content = m.get("message", {}).get("content", "")
    if isinstance(content, str):
        raw = content
    elif isinstance(content, list):
        raw = None
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                raw = block.get("text")
                break
        if not raw:
            return None
    else:
        return None
    clean = raw.strip()
    return clean or None


class BoundedUUIDSet:
    """FIFO-bounded set backed by a circular buffer."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._ring: list[Optional[str]] = [None] * capacity
        self._set: set[str] = set()
        self._write_idx = 0

    def add(self, uuid_val: str) -> None:
        if uuid_val in self._set:
            return
        evicted = self._ring[self._write_idx]
        if evicted is not None:
            self._set.discard(evicted)
        self._ring[self._write_idx] = uuid_val
        self._set.add(uuid_val)
        self._write_idx = (self._write_idx + 1) % self._capacity

    def has(self, uuid_val: str) -> bool:
        return uuid_val in self._set

    def clear(self) -> None:
        self._set.clear()
        self._ring = [None] * self._capacity
        self._write_idx = 0


def handle_ingress_message(
    data: str,
    recent_posted_uuids: BoundedUUIDSet,
    recent_inbound_uuids: BoundedUUIDSet,
    on_inbound_message: Optional[Callable] = None,
    on_permission_response: Optional[Callable] = None,
    on_control_request: Optional[Callable] = None,
) -> None:
    """Parse an ingress WebSocket message and route it."""
    try:
        parsed = json.loads(data)
        if is_sdk_control_response(parsed):
            if on_permission_response:
                on_permission_response(parsed)
            return
        if is_sdk_control_request(parsed):
            if on_control_request:
                on_control_request(parsed)
            return
        if not is_sdk_message(parsed):
            return
        msg_uuid = parsed.get("uuid") if isinstance(parsed.get("uuid"), str) else None
        if msg_uuid and recent_posted_uuids.has(msg_uuid):
            return
        if msg_uuid and recent_inbound_uuids.has(msg_uuid):
            return
        if parsed.get("type") == "user":
            if msg_uuid:
                recent_inbound_uuids.add(msg_uuid)
            if on_inbound_message:
                on_inbound_message(parsed)
    except Exception as err:
        logger.debug("[bridge:repl] Failed to parse ingress message: %s", err)


OUTBOUND_ONLY_ERROR = (
    "This session is outbound-only. Enable Remote Control locally to allow inbound control."
)


def handle_server_control_request(
    request: dict,
    transport: Any,
    session_id: str,
    outbound_only: bool = False,
    on_interrupt: Optional[Callable] = None,
    on_set_model: Optional[Callable] = None,
    on_set_max_thinking_tokens: Optional[Callable] = None,
    on_set_permission_mode: Optional[Callable] = None,
) -> None:
    """Respond to inbound control_request messages from the server."""
    if not transport:
        return

    req = request.get("request", {})
    request_id = request.get("request_id", "")
    subtype = req.get("subtype", "")

    if outbound_only and subtype != "initialize":
        response = {
            "type": "control_response",
            "response": {"subtype": "error", "request_id": request_id, "error": OUTBOUND_ONLY_ERROR},
        }
        transport.write({**response, "session_id": session_id})
        return

    if subtype == "initialize":
        response = {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": request_id,
                "response": {"commands": [], "output_style": "normal", "models": [], "account": {}},
            },
        }
    elif subtype == "set_model":
        if on_set_model:
            on_set_model(req.get("model"))
        response = {"type": "control_response", "response": {"subtype": "success", "request_id": request_id}}
    elif subtype == "set_max_thinking_tokens":
        if on_set_max_thinking_tokens:
            on_set_max_thinking_tokens(req.get("max_thinking_tokens"))
        response = {"type": "control_response", "response": {"subtype": "success", "request_id": request_id}}
    elif subtype == "set_permission_mode":
        verdict = None
        if on_set_permission_mode:
            verdict = on_set_permission_mode(req.get("mode"))
        if verdict and verdict.get("ok"):
            response = {"type": "control_response", "response": {"subtype": "success", "request_id": request_id}}
        else:
            error_msg = verdict.get("error", "Not supported") if verdict else "Not supported"
            response = {"type": "control_response", "response": {"subtype": "error", "request_id": request_id, "error": error_msg}}
    elif subtype == "interrupt":
        if on_interrupt:
            on_interrupt()
        response = {"type": "control_response", "response": {"subtype": "success", "request_id": request_id}}
    else:
        response = {
            "type": "control_response",
            "response": {"subtype": "error", "request_id": request_id, "error": f"Unknown control_request subtype: {subtype}"},
        }

    transport.write({**response, "session_id": session_id})


def make_result_message(session_id: str) -> dict:
    """Build a minimal result message for session archival."""
    return {
        "type": "result",
        "subtype": "success",
        "duration_ms": 0,
        "duration_api_ms": 0,
        "is_error": False,
        "num_turns": 0,
        "result": "",
        "stop_reason": None,
        "total_cost_usd": 0,
        "usage": {},
        "modelUsage": {},
        "permission_denials": [],
        "session_id": session_id,
        "uuid": str(uuid.uuid4()),
    }

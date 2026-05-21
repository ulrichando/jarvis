"""Discord server management and messaging tool for JARVIS voice-agent.

Provides two tools for interacting with Discord servers when a bot token
is configured:

  - ``discord``       -- core read/messaging actions (fetch_messages,
                         search_members, create_thread)
  - ``discord_admin`` -- server management actions (list_guilds, server_info,
                         list_channels, channel_info, list_roles, member_info,
                         list_pins, pin_message, unpin_message, delete_message,
                         add_role, remove_role)

Authentication uses a bot token via the ``DISCORD_BOT_TOKEN`` env var.
Both tools are gated inert when the token is unset.

Ported from the upstream discord_tool. No upstream brand tokens.
Deps: stdlib only (urllib, json).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from .registry import registry, tool_error

logger = logging.getLogger(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"


# ---------------------------------------------------------------------------
# Availability gate
# ---------------------------------------------------------------------------

def _check_discord_available() -> bool:
    return bool(os.getenv("DISCORD_BOT_TOKEN", "").strip())


# ---------------------------------------------------------------------------
# Low-level HTTP helper
# ---------------------------------------------------------------------------

class DiscordAPIError(Exception):
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"Discord API error {status}: {body}")


def _discord_request(
    method: str,
    path: str,
    token: str,
    params: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout: int = 15,
) -> Any:
    url = f"{DISCORD_API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "jarvis-voice-agent-discord/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 204:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise DiscordAPIError(e.code, error_body) from e


# ---------------------------------------------------------------------------
# Channel type mapping
# ---------------------------------------------------------------------------

_CHANNEL_TYPE_NAMES: Dict[int, str] = {
    0: "text", 2: "voice", 4: "category", 5: "announcement",
    10: "announcement_thread", 11: "public_thread", 12: "private_thread",
    13: "stage", 15: "forum", 16: "media",
}


def _channel_type_name(type_id: int) -> str:
    return _CHANNEL_TYPE_NAMES.get(type_id, f"unknown({type_id})")


# ---------------------------------------------------------------------------
# Action implementations
# ---------------------------------------------------------------------------

def _list_guilds(token: str, **_kw: Any) -> str:
    guilds = _discord_request("GET", "/users/@me/guilds", token)
    result = [
        {"id": g["id"], "name": g["name"], "owner": g.get("owner", False)}
        for g in guilds
    ]
    return json.dumps({"guilds": result, "count": len(result)})


def _server_info(token: str, guild_id: str, **_kw: Any) -> str:
    g = _discord_request("GET", f"/guilds/{guild_id}", token, params={"with_counts": "true"})
    return json.dumps({
        "id": g["id"],
        "name": g["name"],
        "description": g.get("description"),
        "owner_id": g.get("owner_id"),
        "member_count": g.get("approximate_member_count"),
        "online_count": g.get("approximate_presence_count"),
        "features": g.get("features", []),
        "premium_tier": g.get("premium_tier"),
        "verification_level": g.get("verification_level"),
    })


def _list_channels(token: str, guild_id: str, **_kw: Any) -> str:
    channels = _discord_request("GET", f"/guilds/{guild_id}/channels", token)
    categories: Dict[Optional[str], Dict[str, Any]] = {}
    uncategorized: List[Dict[str, Any]] = []

    for ch in channels:
        if ch["type"] == 4:
            categories[ch["id"]] = {
                "id": ch["id"], "name": ch["name"],
                "position": ch.get("position", 0), "channels": [],
            }

    for ch in channels:
        if ch["type"] == 4:
            continue
        entry = {
            "id": ch["id"], "name": ch.get("name", ""),
            "type": _channel_type_name(ch["type"]),
            "position": ch.get("position", 0),
            "topic": ch.get("topic"),
        }
        parent = ch.get("parent_id")
        if parent and parent in categories:
            categories[parent]["channels"].append(entry)
        else:
            uncategorized.append(entry)

    sorted_cats = sorted(categories.values(), key=lambda c: c["position"])
    for cat in sorted_cats:
        cat["channels"].sort(key=lambda c: c["position"])

    result: List[Dict[str, Any]] = []
    if uncategorized:
        result.append({"category": None, "channels": uncategorized})
    for cat in sorted_cats:
        result.append({"category": {"id": cat["id"], "name": cat["name"]}, "channels": cat["channels"]})

    return json.dumps({"channel_groups": result, "total_channels": sum(len(g["channels"]) for g in result)})


def _channel_info(token: str, channel_id: str, **_kw: Any) -> str:
    ch = _discord_request("GET", f"/channels/{channel_id}", token)
    return json.dumps({
        "id": ch["id"], "name": ch.get("name"),
        "type": _channel_type_name(ch["type"]),
        "guild_id": ch.get("guild_id"), "topic": ch.get("topic"),
        "nsfw": ch.get("nsfw", False), "position": ch.get("position"),
        "parent_id": ch.get("parent_id"),
    })


def _list_roles(token: str, guild_id: str, **_kw: Any) -> str:
    roles = _discord_request("GET", f"/guilds/{guild_id}/roles", token)
    result = [
        {
            "id": r["id"], "name": r["name"],
            "color": f"#{r.get('color', 0):06x}" if r.get("color") else None,
            "position": r.get("position", 0),
            "mentionable": r.get("mentionable", False),
            "managed": r.get("managed", False),
        }
        for r in sorted(roles, key=lambda r: r.get("position", 0), reverse=True)
    ]
    return json.dumps({"roles": result, "count": len(result)})


def _member_info(token: str, guild_id: str, user_id: str, **_kw: Any) -> str:
    m = _discord_request("GET", f"/guilds/{guild_id}/members/{user_id}", token)
    user = m.get("user", {})
    return json.dumps({
        "user_id": user.get("id"), "username": user.get("username"),
        "display_name": user.get("global_name"), "nickname": m.get("nick"),
        "bot": user.get("bot", False), "roles": m.get("roles", []),
        "joined_at": m.get("joined_at"),
    })


def _search_members(token: str, guild_id: str, query: str, limit: int = 20, **_kw: Any) -> str:
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 20
    params = {"query": query, "limit": str(limit)}
    members = _discord_request("GET", f"/guilds/{guild_id}/members/search", token, params=params)
    result = [
        {
            "user_id": m.get("user", {}).get("id"),
            "username": m.get("user", {}).get("username"),
            "display_name": m.get("user", {}).get("global_name"),
            "nickname": m.get("nick"),
            "bot": m.get("user", {}).get("bot", False),
        }
        for m in members
    ]
    return json.dumps({"members": result, "count": len(result)})


def _fetch_messages(
    token: str, channel_id: str, limit: int = 50,
    before: Optional[str] = None, after: Optional[str] = None, **_kw: Any
) -> str:
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 50
    params: Dict[str, str] = {"limit": str(limit)}
    if before:
        params["before"] = before
    if after:
        params["after"] = after
    messages = _discord_request("GET", f"/channels/{channel_id}/messages", token, params=params)
    result = [
        {
            "id": msg["id"],
            "content": msg.get("content", ""),
            "author": {
                "id": msg.get("author", {}).get("id"),
                "username": msg.get("author", {}).get("username"),
                "bot": msg.get("author", {}).get("bot", False),
            },
            "timestamp": msg.get("timestamp"),
            "attachments": len(msg.get("attachments", [])),
            "pinned": msg.get("pinned", False),
        }
        for msg in messages
    ]
    return json.dumps({"messages": result, "count": len(result)})


def _list_pins(token: str, channel_id: str, **_kw: Any) -> str:
    messages = _discord_request("GET", f"/channels/{channel_id}/pins", token)
    result = [
        {
            "id": msg["id"],
            "content": msg.get("content", "")[:200],
            "author": msg.get("author", {}).get("username"),
            "timestamp": msg.get("timestamp"),
        }
        for msg in messages
    ]
    return json.dumps({"pinned_messages": result, "count": len(result)})


def _pin_message(token: str, channel_id: str, message_id: str, **_kw: Any) -> str:
    _discord_request("PUT", f"/channels/{channel_id}/pins/{message_id}", token)
    return json.dumps({"success": True, "message": f"Message {message_id} pinned."})


def _unpin_message(token: str, channel_id: str, message_id: str, **_kw: Any) -> str:
    _discord_request("DELETE", f"/channels/{channel_id}/pins/{message_id}", token)
    return json.dumps({"success": True, "message": f"Message {message_id} unpinned."})


def _delete_message(token: str, channel_id: str, message_id: str, **_kw: Any) -> str:
    _discord_request("DELETE", f"/channels/{channel_id}/messages/{message_id}", token)
    return json.dumps({"success": True, "message": f"Message {message_id} deleted."})


def _create_thread(
    token: str, channel_id: str, name: str,
    message_id: Optional[str] = None,
    auto_archive_duration: int = 1440, **_kw: Any
) -> str:
    if message_id:
        path = f"/channels/{channel_id}/messages/{message_id}/threads"
        body: Dict[str, Any] = {"name": name, "auto_archive_duration": auto_archive_duration}
    else:
        path = f"/channels/{channel_id}/threads"
        body = {"name": name, "auto_archive_duration": auto_archive_duration, "type": 11}
    thread = _discord_request("POST", path, token, body=body)
    return json.dumps({"success": True, "thread_id": thread["id"], "name": thread.get("name")})


def _add_role(token: str, guild_id: str, user_id: str, role_id: str, **_kw: Any) -> str:
    _discord_request("PUT", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}", token)
    return json.dumps({"success": True, "message": f"Role {role_id} added to user {user_id}."})


def _remove_role(token: str, guild_id: str, user_id: str, role_id: str, **_kw: Any) -> str:
    _discord_request("DELETE", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}", token)
    return json.dumps({"success": True, "message": f"Role {role_id} removed from user {user_id}."})


# ---------------------------------------------------------------------------
# Action dispatch
# ---------------------------------------------------------------------------

_ALL_ACTIONS: Dict[str, Any] = {
    "list_guilds": _list_guilds,
    "server_info": _server_info,
    "list_channels": _list_channels,
    "channel_info": _channel_info,
    "list_roles": _list_roles,
    "member_info": _member_info,
    "search_members": _search_members,
    "fetch_messages": _fetch_messages,
    "list_pins": _list_pins,
    "pin_message": _pin_message,
    "unpin_message": _unpin_message,
    "delete_message": _delete_message,
    "create_thread": _create_thread,
    "add_role": _add_role,
    "remove_role": _remove_role,
}

# Core = read/messaging actions any Discord user would care about.
_CORE_ACTION_NAMES: frozenset[str] = frozenset({"fetch_messages", "search_members", "create_thread"})
# Admin = server management.
_ADMIN_ACTION_NAMES: frozenset[str] = frozenset(_ALL_ACTIONS.keys()) - _CORE_ACTION_NAMES

# Per-action required params for runtime validation.
_REQUIRED_PARAMS: Dict[str, List[str]] = {
    "server_info": ["guild_id"],
    "list_channels": ["guild_id"],
    "list_roles": ["guild_id"],
    "member_info": ["guild_id", "user_id"],
    "search_members": ["guild_id", "query"],
    "channel_info": ["channel_id"],
    "fetch_messages": ["channel_id"],
    "list_pins": ["channel_id"],
    "pin_message": ["channel_id", "message_id"],
    "unpin_message": ["channel_id", "message_id"],
    "delete_message": ["channel_id", "message_id"],
    "create_thread": ["channel_id", "name"],
    "add_role": ["guild_id", "user_id", "role_id"],
    "remove_role": ["guild_id", "user_id", "role_id"],
}


def _run_action(args: dict, action_set: frozenset[str], tool_label: str) -> str:
    """Dispatch a discord tool call to the appropriate action."""
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        return tool_error("DISCORD_BOT_TOKEN is not configured.")

    action = str(args.get("action", "")).strip().lower().replace("-", "_")
    if not action:
        return tool_error(f"'action' is required. Available: {', '.join(sorted(action_set))}")
    if action not in action_set:
        return tool_error(
            f"Unknown action '{action}' for {tool_label}. "
            f"Available: {', '.join(sorted(action_set))}"
        )

    action_fn = _ALL_ACTIONS[action]
    required = _REQUIRED_PARAMS.get(action, [])
    missing = [p for p in required if not args.get(p)]
    if missing:
        return tool_error(f"Missing required parameters for '{action}': {', '.join(missing)}")

    try:
        return action_fn(
            token=token,
            guild_id=args.get("guild_id", ""),
            channel_id=args.get("channel_id", ""),
            user_id=args.get("user_id", ""),
            role_id=args.get("role_id", ""),
            message_id=args.get("message_id", ""),
            query=args.get("query", ""),
            name=args.get("name", ""),
            limit=args.get("limit", 50),
            before=args.get("before", ""),
            after=args.get("after", ""),
            auto_archive_duration=args.get("auto_archive_duration", 1440),
        )
    except DiscordAPIError as e:
        logger.warning("Discord API error in %s action '%s': %s", tool_label, action, e)
        if e.status == 403:
            return tool_error(
                f"Permission denied for '{action}'. "
                "The bot may lack the required guild permission or intent."
            )
        return tool_error(str(e))
    except Exception as e:
        logger.exception("Unexpected error in %s action '%s'", tool_label, action)
        return tool_error(f"Unexpected error: {e}")


def _handle_discord_core(args: dict) -> str:
    return _run_action(args, _CORE_ACTION_NAMES, "discord")


def _handle_discord_admin(args: dict) -> str:
    return _run_action(args, _ADMIN_ACTION_NAMES, "discord_admin")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_DISCORD_CORE_SCHEMA = {
    "name": "discord",
    "description": (
        "Interact with Discord servers: fetch messages, search members, create threads. "
        "Requires DISCORD_BOT_TOKEN. "
        "Actions: "
        "fetch_messages (channel_id, limit?, before?, after?) — read channel messages; "
        "search_members (guild_id, query, limit?) — find members by name; "
        "create_thread (channel_id, name, message_id?, auto_archive_duration?) — start a thread."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": sorted(_CORE_ACTION_NAMES),
                "description": "Action to perform.",
            },
            "guild_id": {
                "type": "string",
                "description": "Discord guild (server) snowflake ID.",
            },
            "channel_id": {
                "type": "string",
                "description": "Discord channel or thread snowflake ID.",
            },
            "user_id": {
                "type": "string",
                "description": "Discord user snowflake ID.",
            },
            "message_id": {
                "type": "string",
                "description": "Discord message snowflake ID.",
            },
            "query": {
                "type": "string",
                "description": "Search query string.",
            },
            "name": {
                "type": "string",
                "description": "Thread name for create_thread.",
            },
            "limit": {
                "type": "integer",
                "description": "Max number of results (default 50, max 100).",
            },
            "before": {
                "type": "string",
                "description": "Snowflake ID to fetch messages before (for pagination).",
            },
            "after": {
                "type": "string",
                "description": "Snowflake ID to fetch messages after (for pagination).",
            },
            "auto_archive_duration": {
                "type": "integer",
                "description": "Minutes until thread auto-archives (60, 1440, 4320, 10080). Default 1440.",
            },
        },
        "required": ["action"],
    },
}

_DISCORD_ADMIN_SCHEMA = {
    "name": "discord_admin",
    "description": (
        "Manage Discord servers: list guilds/channels/roles, lookup members, "
        "pin/delete messages, assign roles. "
        "Requires DISCORD_BOT_TOKEN with appropriate guild permissions. "
        "Actions: list_guilds, server_info, list_channels, channel_info, "
        "list_roles, member_info, list_pins, pin_message, unpin_message, "
        "delete_message, add_role, remove_role."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": sorted(_ADMIN_ACTION_NAMES),
                "description": "Management action to perform.",
            },
            "guild_id": {
                "type": "string",
                "description": "Discord guild (server) snowflake ID.",
            },
            "channel_id": {
                "type": "string",
                "description": "Discord channel snowflake ID.",
            },
            "user_id": {
                "type": "string",
                "description": "Discord user snowflake ID.",
            },
            "role_id": {
                "type": "string",
                "description": "Discord role snowflake ID.",
            },
            "message_id": {
                "type": "string",
                "description": "Discord message snowflake ID.",
            },
            "name": {
                "type": "string",
                "description": "Name for create_thread.",
            },
            "query": {
                "type": "string",
                "description": "Search query for search_members.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results for list operations (default 50, max 100).",
            },
        },
        "required": ["action"],
    },
}


# ---------------------------------------------------------------------------
# Registration — both tools gated by DISCORD_BOT_TOKEN
# ---------------------------------------------------------------------------

registry.register(
    name="discord",
    schema=_DISCORD_CORE_SCHEMA,
    handler=_handle_discord_core,
    check_fn=_check_discord_available,
    requires_env=["DISCORD_BOT_TOKEN"],
    description=_DISCORD_CORE_SCHEMA["description"],
)

registry.register(
    name="discord_admin",
    schema=_DISCORD_ADMIN_SCHEMA,
    handler=_handle_discord_admin,
    check_fn=_check_discord_available,
    requires_env=["DISCORD_BOT_TOKEN"],
    description=_DISCORD_ADMIN_SCHEMA["description"],
)

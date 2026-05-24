"""Home Assistant smart-home tools for JARVIS voice-agent.

Provides four tools for controlling smart home devices via the
Home Assistant REST API:

  - ``ha_list_entities``  -- list/filter entities by domain or area
  - ``ha_get_state``      -- get detailed state of a single entity
  - ``ha_list_services``  -- list available services per domain
  - ``ha_call_service``   -- call a HA service (turn_on, set_temperature, etc.)

Authentication uses a Long-Lived Access Token via ``HASS_TOKEN`` env var.
The HA instance URL is read from ``HASS_URL`` (default: http://homeassistant.local:8123).

All four tools are gated inert via ``check_fn`` when ``HASS_TOKEN`` is unset —
they vanish from the LLM's tool surface rather than appearing and returning
auth errors at call time.

Ported from the upstream homeassistant_tool. No upstream brand tokens.
Deps: aiohttp (available in voice-agent .venv).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import re
from typing import Any, Dict, Optional

from .registry import registry, tool_error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Regex for valid HA entity_id format (e.g. "light.living_room")
_ENTITY_ID_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9_]+$")

# Regex for valid HA service/domain names — prevents path traversal in URL.
_SERVICE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Service domains blocked for security (arbitrary command execution on HA host).
_BLOCKED_DOMAINS = frozenset({
    "shell_command",
    "command_line",
    "python_script",
    "pyscript",
    "hassio",
    "rest_command",
})


def _get_config() -> tuple[str, str]:
    """Return (hass_url, hass_token) from env vars at call time."""
    return (
        os.getenv("HASS_URL", "http://homeassistant.local:8123").rstrip("/"),
        os.getenv("HASS_TOKEN", ""),
    )


def _check_ha_available() -> bool:
    """Return True when HASS_TOKEN is configured."""
    return bool(os.getenv("HASS_TOKEN", "").strip())


def _get_headers(token: str = "") -> Dict[str, str]:
    if not token:
        _, token = _get_config()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------

def _run_async(coro):
    """Run an async coroutine from a sync handler."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=30)
    else:
        return asyncio.run(coro)


def _filter_and_summarize(
    states: list,
    domain: Optional[str] = None,
    area: Optional[str] = None,
) -> Dict[str, Any]:
    """Filter raw HA states by domain/area and return a compact summary."""
    if domain:
        states = [s for s in states if s.get("entity_id", "").startswith(f"{domain}.")]
    if area:
        area_lower = area.lower()
        states = [
            s for s in states
            if area_lower in (s.get("attributes", {}).get("friendly_name", "") or "").lower()
            or area_lower in (s.get("attributes", {}).get("area", "") or "").lower()
        ]

    entities = []
    for s in states:
        entities.append({
            "entity_id": s["entity_id"],
            "state": s["state"],
            "friendly_name": s.get("attributes", {}).get("friendly_name", ""),
        })

    return {"count": len(entities), "entities": entities}


async def _async_list_entities(
    domain: Optional[str] = None,
    area: Optional[str] = None,
) -> Dict[str, Any]:
    import aiohttp
    hass_url, hass_token = _get_config()
    url = f"{hass_url}/api/states"
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url, headers=_get_headers(hass_token),
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            resp.raise_for_status()
            states = await resp.json()
    return _filter_and_summarize(states, domain, area)


async def _async_get_state(entity_id: str) -> Dict[str, Any]:
    import aiohttp
    hass_url, hass_token = _get_config()
    url = f"{hass_url}/api/states/{entity_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url, headers=_get_headers(hass_token),
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
    return {
        "entity_id": data["entity_id"],
        "state": data["state"],
        "attributes": data.get("attributes", {}),
        "last_changed": data.get("last_changed"),
        "last_updated": data.get("last_updated"),
    }


async def _async_list_services(domain: Optional[str] = None) -> Dict[str, Any]:
    import aiohttp
    hass_url, hass_token = _get_config()
    url = f"{hass_url}/api/services"
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url, headers=_get_headers(hass_token),
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            resp.raise_for_status()
            services_data = await resp.json()

    result = []
    for svc in services_data:
        svc_domain = svc.get("domain", "")
        if domain and svc_domain != domain:
            continue
        services = svc.get("services", {})
        result.append({
            "domain": svc_domain,
            "services": {name: info.get("description", "") for name, info in services.items()},
        })
    return {"domains": result, "count": len(result)}


async def _async_call_service(
    domain: str,
    service: str,
    entity_id: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    import aiohttp
    hass_url, hass_token = _get_config()
    url = f"{hass_url}/api/services/{domain}/{service}"

    payload: Dict[str, Any] = {}
    if data:
        payload.update(data)
    if entity_id:
        payload["entity_id"] = entity_id

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            headers=_get_headers(hass_token),
            json=payload,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            resp.raise_for_status()
            result = await resp.json()

    affected = []
    if isinstance(result, list):
        for s in result:
            affected.append({"entity_id": s.get("entity_id", ""), "state": s.get("state", "")})
    return {
        "success": True,
        "service": f"{domain}.{service}",
        "affected_entities": affected,
    }


# ---------------------------------------------------------------------------
# Sync handlers
# ---------------------------------------------------------------------------

def _handle_list_entities(args: dict) -> str:
    domain = args.get("domain")
    area = args.get("area")
    try:
        result = _run_async(_async_list_entities(domain=domain, area=area))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("ha_list_entities error: %s", e)
        return tool_error(f"Failed to list entities: {e}")


def _handle_get_state(args: dict) -> str:
    entity_id = args.get("entity_id", "")
    if not entity_id:
        return tool_error("Missing required parameter: entity_id")
    if not _ENTITY_ID_RE.match(entity_id):
        return tool_error(f"Invalid entity_id format: {entity_id!r}. Expected format: domain.name")
    try:
        result = _run_async(_async_get_state(entity_id))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("ha_get_state error: %s", e)
        return tool_error(f"Failed to get state for {entity_id}: {e}")


def _handle_list_services(args: dict) -> str:
    domain = args.get("domain")
    try:
        result = _run_async(_async_list_services(domain=domain))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("ha_list_services error: %s", e)
        return tool_error(f"Failed to list services: {e}")


def _handle_call_service(args: dict) -> str:
    domain = args.get("domain", "").strip().lower()
    service = args.get("service", "").strip().lower()
    entity_id: Optional[str] = args.get("entity_id")
    raw_data = args.get("data")

    if not domain:
        return tool_error("Missing required parameter: domain")
    if not service:
        return tool_error("Missing required parameter: service")

    if not _SERVICE_NAME_RE.match(domain):
        return tool_error(f"Invalid domain name: {domain!r}. Use lowercase letters, digits, underscores.")
    if domain in _BLOCKED_DOMAINS:
        return tool_error(
            f"Domain '{domain}' is blocked for security. "
            "It allows arbitrary code/command execution on the HA host."
        )
    if not _SERVICE_NAME_RE.match(service):
        return tool_error(f"Invalid service name: {service!r}. Use lowercase letters, digits, underscores.")

    if entity_id and not _ENTITY_ID_RE.match(entity_id):
        return tool_error(f"Invalid entity_id format: {entity_id!r}. Expected format: domain.name")

    # Parse the optional data string as JSON
    data: Optional[Dict[str, Any]] = None
    if raw_data:
        if isinstance(raw_data, str):
            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError as e:
                return tool_error(f"Invalid JSON in 'data' parameter: {e}")
        elif isinstance(raw_data, dict):
            data = raw_data
        else:
            return tool_error("'data' must be a JSON string or object.")

    try:
        result = _run_async(_async_call_service(domain, service, entity_id=entity_id, data=data))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("ha_call_service error: %s", e)
        return tool_error(f"Failed to call {domain}.{service}: {e}")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_HA_LIST_ENTITIES_SCHEMA = {
    "name": "ha_list_entities",
    "description": (
        "List Home Assistant entities (smart home devices and sensors). "
        "Filter by domain (e.g. 'light', 'switch', 'sensor', 'climate', "
        "'media_player') or by area name. Returns entity IDs, states, and "
        "friendly names. Use this to discover what devices are available "
        "before calling ha_call_service."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Filter by device domain (e.g. 'light', 'switch', 'sensor', 'climate').",
            },
            "area": {
                "type": "string",
                "description": "Filter by area/room name (partial match, e.g. 'living room', 'bedroom').",
            },
        },
        "required": [],
    },
}

_HA_GET_STATE_SCHEMA = {
    "name": "ha_get_state",
    "description": (
        "Get the detailed state of a single Home Assistant entity, including "
        "all attributes. Use this to check the current value of a sensor, "
        "the brightness of a light, or the mode of a thermostat."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "Entity ID to query (e.g. 'light.living_room', 'sensor.temperature_1').",
            },
        },
        "required": ["entity_id"],
    },
}

_HA_LIST_SERVICES_SCHEMA = {
    "name": "ha_list_services",
    "description": (
        "List available Home Assistant services (actions) for device control. "
        "Shows what actions can be performed on each device type and what "
        "parameters they accept. Use this to discover how to control devices "
        "found via ha_list_entities."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Filter by domain (e.g. 'light', 'climate', 'switch'). Omit to list all.",
            },
        },
        "required": [],
    },
}

_HA_CALL_SERVICE_SCHEMA = {
    "name": "ha_call_service",
    "description": (
        "Call a Home Assistant service to control a smart home device. "
        "Use ha_list_services to discover available services and their "
        "parameters for each domain before calling."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Service domain (e.g. 'light', 'switch', 'climate', 'cover', 'media_player').",
            },
            "service": {
                "type": "string",
                "description": "Service name (e.g. 'turn_on', 'turn_off', 'toggle', 'set_temperature').",
            },
            "entity_id": {
                "type": "string",
                "description": "Target entity ID (e.g. 'light.living_room'). Some services may not need this.",
            },
            "data": {
                "type": "string",
                "description": (
                    "Additional service data as a JSON string. Examples: "
                    '{"brightness": 255, "color_name": "blue"} for lights, '
                    '{"temperature": 22, "hvac_mode": "heat"} for climate.'
                ),
            },
        },
        "required": ["domain", "service"],
    },
}


# ---------------------------------------------------------------------------
# Registration — all four tools gated by HASS_TOKEN
# ---------------------------------------------------------------------------

registry.register(
    name="ha_list_entities",
    schema=_HA_LIST_ENTITIES_SCHEMA,
    handler=_handle_list_entities,
    check_fn=_check_ha_available,
    requires_env=["HASS_TOKEN"],
    description=_HA_LIST_ENTITIES_SCHEMA["description"],
    emoji="",
)

registry.register(
    name="ha_get_state",
    schema=_HA_GET_STATE_SCHEMA,
    handler=_handle_get_state,
    check_fn=_check_ha_available,
    requires_env=["HASS_TOKEN"],
    description=_HA_GET_STATE_SCHEMA["description"],
    emoji="",
)

registry.register(
    name="ha_list_services",
    schema=_HA_LIST_SERVICES_SCHEMA,
    handler=_handle_list_services,
    check_fn=_check_ha_available,
    requires_env=["HASS_TOKEN"],
    description=_HA_LIST_SERVICES_SCHEMA["description"],
    emoji="",
)

registry.register(
    name="ha_call_service",
    schema=_HA_CALL_SERVICE_SCHEMA,
    handler=_handle_call_service,
    check_fn=_check_ha_available,
    requires_env=["HASS_TOKEN"],
    description=_HA_CALL_SERVICE_SCHEMA["description"],
    emoji="",
)

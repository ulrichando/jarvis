"""Minimal MCP (Model Context Protocol) client for the JARVIS voice agent.

Connects to external MCP servers, discovers their tools, and **registers each
discovered tool into the JARVIS tool registry** under a namespaced name
(``mcp__<server>__<tool>``) so they appear on the supervisor through
``tools._adapter.load_all_livekit_tools()`` like any built-in tool. Each
registered handler proxies the call to its MCP server and returns the result.

Configuration
-------------
Read from ``~/.jarvis/mcp.json`` (via :mod:`tools.runtime`). The file maps a
logical server name to a connection spec. Two transports are supported:

  * **stdio** — ``{"command": "npx", "args": [...], "env": {...}}``
  * **HTTP / SSE** — ``{"url": "https://host/mcp", "headers": {...}}``;
    add ``"transport": "sse"`` to use the SSE protocol instead of
    Streamable HTTP.

Example ``~/.jarvis/mcp.json``::

    {
      "servers": {
        "filesystem": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        },
        "remote": {
          "url": "https://my-mcp.example.com/mcp",
          "headers": {"Authorization": "Bearer sk-..."}
        }
      }
    }

A bare top-level object (server-name → spec, no ``"servers"`` wrapper) is also
accepted.

Inert without config
--------------------
With **no** ``~/.jarvis/mcp.json`` (or an empty/malformed one), this module is a
complete no-op: no event loop is started, no subprocess is spawned, no network
connection is opened, and no tools are registered. Discovery is invoked once at
import time by ``discover_mcp_tools()`` — it returns immediately when there is
nothing to do.

Per-server isolation
-------------------
Each server connects independently. A connection failure for one server is
caught, logged, and skipped — it never aborts discovery of the others and never
crashes the import. The whole thing also degrades to a clean no-op when the
optional ``mcp`` SDK is not installed (guarded import — never raises at module
top).

Minimal by design
-----------------
This is a deliberately small client: a single background event loop, one
long-lived task per server, synchronous tool-call proxying, and a JSON-Schema
normalizer + name sanitizer. The upstream gateway/OAuth/sampling/
dynamic-rediscovery/circuit-breaker machinery is intentionally **not** ported —
JARVIS reads a local config file and proxies tool calls; nothing more.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import re
import threading
from typing import Any, Dict, List, Optional

from .registry import registry
from .runtime import get_jarvis_home

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Guarded SDK import — the ``mcp`` package is an optional dependency.
# ---------------------------------------------------------------------------
#
# If it is missing, every entry point in this module short-circuits to a no-op
# so the voice agent imports cleanly without it. Never import the package at a
# point where ImportError could escape module scope.

_MCP_AVAILABLE = False
_MCP_HTTP_AVAILABLE = False
_MCP_SSE_AVAILABLE = False
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    _MCP_AVAILABLE = True
    try:
        from mcp.client.streamable_http import streamablehttp_client

        _MCP_HTTP_AVAILABLE = True
    except ImportError:  # pragma: no cover - depends on SDK build
        _MCP_HTTP_AVAILABLE = False
    try:
        from mcp.client.sse import sse_client

        _MCP_SSE_AVAILABLE = True
    except ImportError:  # pragma: no cover - depends on SDK build
        sse_client = None
        _MCP_SSE_AVAILABLE = False
except ImportError:  # pragma: no cover - exercised only without the SDK
    logger.debug("mcp package not installed — MCP tool support disabled")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Logical-name prefix marking a registry tool as MCP-sourced.
MCP_TOOL_PREFIX = "mcp__"

#: Per-tool-call timeout (seconds), overridable per server via ``"timeout"``.
_DEFAULT_TOOL_TIMEOUT = 120.0

#: Initial connect timeout (seconds), overridable per server via ``"connect_timeout"``.
_DEFAULT_CONNECT_TIMEOUT = 60.0

#: Env vars safe to pass through to stdio subprocesses (everything else from
#: the user's ``env`` block is added on top, but the parent process env is NOT
#: leaked wholesale — credentials must be opted in explicitly).
_SAFE_ENV_KEYS = frozenset(
    {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TMPDIR"}
)

#: Credential-ish substrings scrubbed from error text before it reaches the LLM.
_CREDENTIAL_PATTERN = re.compile(
    r"(?:"
    r"ghp_[A-Za-z0-9_]{1,255}"
    r"|sk-[A-Za-z0-9_]{1,255}"
    r"|Bearer\s+\S+"
    r"|token=[^\s&,;\"']{1,255}"
    r"|key=[^\s&,;\"']{1,255}"
    r"|api[_-]?key=[^\s&,;\"']{1,255}"
    r"|password=[^\s&,;\"']{1,255}"
    r"|secret=[^\s&,;\"']{1,255}"
    r")",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Module state — guarded by ``_lock``. Touched from caller threads (discovery,
# tool handlers) and the background event loop thread.
# ---------------------------------------------------------------------------

_lock = threading.RLock()
_servers: Dict[str, "MCPServer"] = {}
_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_thread: Optional[threading.Thread] = None
_registered_tool_names: List[str] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_error(text: str) -> str:
    """Strip credential-like patterns from error text before returning to LLM."""
    return _CREDENTIAL_PATTERN.sub("[REDACTED]", str(text))


def sanitize_mcp_name_component(value: str) -> str:
    """Make a server/tool name safe for a function-name component.

    Replaces every character outside ``[A-Za-z0-9_]`` with ``_`` so the
    generated ``mcp__<server>__<tool>`` name passes provider tool-name
    validation (OpenAI/Anthropic/Groq all require this character class).
    """
    return re.sub(r"[^A-Za-z0-9_]", "_", str(value or ""))


def make_tool_name(server_name: str, tool_name: str) -> str:
    """Return the namespaced registry name for an MCP tool."""
    return (
        f"{MCP_TOOL_PREFIX}{sanitize_mcp_name_component(server_name)}__"
        f"{sanitize_mcp_name_component(tool_name)}"
    )


def _build_safe_env(user_env: Optional[dict]) -> Optional[dict]:
    """Build a filtered environment dict for a stdio subprocess.

    Passes through only safe baseline vars (PATH/HOME/…) plus any ``XDG_*`` and
    whatever the user explicitly listed in the server's ``env`` block. Prevents
    leaking the voice-agent's API keys to arbitrary MCP subprocesses.
    """
    import os

    env: Dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _SAFE_ENV_KEYS or key.startswith("XDG_"):
            env[key] = value
    if user_env:
        for k, v in user_env.items():
            env[str(k)] = str(v)
    return env or None


def _normalize_mcp_input_schema(schema: Optional[dict]) -> dict:
    """Normalize an MCP tool inputSchema into a provider-portable object schema.

    MCP servers emit plain JSON Schema that LLM tool-calling APIs sometimes
    reject. This applies the minimal, provider-agnostic repairs that matter in
    practice (ported from the upstream normalizer, trimmed to stdlib-only):

    * ``definitions`` / ``#/definitions/...`` refs are rewritten to
      ``$defs`` / ``#/$defs/...`` (Kimi/Moonshot requirement).
    * A missing/``null`` ``type`` on an object-shaped node is coerced to
      ``"object"``; an ``object`` node missing ``properties`` gets an empty one.
    * ``required`` entries that don't exist in ``properties`` are pruned
      (Gemini 400s otherwise).
    * Nullable unions (``anyOf: [{...}, {"type": "null"}]``) are collapsed to
      the non-null branch (Anthropic rejects nullable branches in tool inputs);
      optionality stays represented by the parent's ``required`` list.

    The adapter's :func:`tools._adapter.sanitize_schema` still runs afterward to
    force ``additionalProperties: false`` — we don't duplicate that here.
    """
    if not schema or not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    def _rewrite_local_refs(node):
        if isinstance(node, dict):
            out: Dict[str, Any] = {}
            for key, value in node.items():
                out_key = "$defs" if key == "definitions" else key
                out[out_key] = _rewrite_local_refs(value)
            ref = out.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/definitions/"):
                out["$ref"] = "#/$defs/" + ref[len("#/definitions/"):]
            return out
        if isinstance(node, list):
            return [_rewrite_local_refs(item) for item in node]
        return node

    def _strip_nullable_union(node):
        """Collapse ``anyOf``/``oneOf`` containing a ``{"type": "null"}`` branch."""
        if isinstance(node, list):
            return [_strip_nullable_union(item) for item in node]
        if not isinstance(node, dict):
            return node

        out = {k: _strip_nullable_union(v) for k, v in node.items()}
        for union_key in ("anyOf", "oneOf"):
            branches = out.get(union_key)
            if not isinstance(branches, list):
                continue
            non_null = [
                b
                for b in branches
                if not (isinstance(b, dict) and b.get("type") == "null")
            ]
            if non_null and len(non_null) < len(branches):
                if len(non_null) == 1 and isinstance(non_null[0], dict):
                    # Single surviving branch → hoist it up, drop the union.
                    out.pop(union_key, None)
                    for k, v in non_null[0].items():
                        out.setdefault(k, v)
                    out["nullable"] = True
                else:
                    out[union_key] = non_null
                    out["nullable"] = True
        return out

    def _repair_object_shape(node):
        if isinstance(node, list):
            return [_repair_object_shape(item) for item in node]
        if not isinstance(node, dict):
            return node

        repaired = {k: _repair_object_shape(v) for k, v in node.items()}
        if not repaired.get("type") and ("properties" in repaired or "required" in repaired):
            repaired["type"] = "object"
        if repaired.get("type") == "object":
            if not isinstance(repaired.get("properties"), dict):
                repaired["properties"] = {}
            required = repaired.get("required")
            if isinstance(required, list):
                props = repaired.get("properties") or {}
                valid = [r for r in required if isinstance(r, str) and r in props]
                if len(valid) != len(required):
                    if valid:
                        repaired["required"] = valid
                    else:
                        repaired.pop("required", None)
        return repaired

    normalized = _repair_object_shape(_strip_nullable_union(_rewrite_local_refs(schema)))
    if not isinstance(normalized, dict):
        return {"type": "object", "properties": {}}
    if normalized.get("type") == "object" and "properties" not in normalized:
        normalized = {**normalized, "properties": {}}
    return normalized


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _config_path():
    """Return the ``~/.jarvis/mcp.json`` path (does not create it)."""
    return get_jarvis_home() / "mcp.json"


def load_mcp_config() -> Dict[str, dict]:
    """Return ``{server_name: spec}`` from ``~/.jarvis/mcp.json``.

    Returns an empty dict when the file is absent, empty, malformed, or has no
    server entries — that empty result is what makes the whole module inert. A
    parse error is logged at warning level and treated as "no servers".
    """
    path = _config_path()
    try:
        if not path.is_file():
            return {}
        raw = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("Could not read MCP config %s: %s", path, exc)
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Malformed MCP config %s: %s — ignoring", path, exc)
        return {}

    # Accept either {"servers": {...}} or a bare {name: spec, ...} mapping.
    if isinstance(data, dict) and isinstance(data.get("servers"), dict):
        servers = data["servers"]
    elif isinstance(data, dict):
        servers = data
    else:
        logger.warning("MCP config %s is not a JSON object — ignoring", path)
        return {}

    out: Dict[str, dict] = {}
    for name, spec in servers.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(spec, dict):
            logger.warning("MCP server %r spec is not an object — skipping", name)
            continue
        if spec.get("disabled") is True or spec.get("enabled") is False:
            continue
        if not spec.get("command") and not spec.get("url"):
            logger.warning(
                "MCP server %r has neither 'command' nor 'url' — skipping", name
            )
            continue
        out[name.strip()] = spec
    return out


# ---------------------------------------------------------------------------
# Background event loop — one daemon thread hosts all server tasks so their
# anyio transport context managers are entered/exited in the same task.
# ---------------------------------------------------------------------------


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Return the shared MCP event loop, starting its daemon thread on first use."""
    global _loop, _loop_thread
    with _lock:
        if _loop is not None and _loop.is_running():
            return _loop
        loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(target=_run, name="jarvis-mcp-loop", daemon=True)
        thread.start()
        _loop = loop
        _loop_thread = thread
        return loop


def _run_on_loop(coro_factory, timeout: float):
    """Schedule ``coro_factory()`` on the MCP loop and block for its result.

    ``coro_factory`` is a zero-arg callable returning a fresh coroutine (so the
    coroutine is created on the loop thread, never the caller thread).
    """
    loop = _ensure_loop()
    fut = asyncio.run_coroutine_threadsafe(coro_factory(), loop)
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        fut.cancel()
        raise TimeoutError(f"MCP operation timed out after {timeout}s")


# ---------------------------------------------------------------------------
# Per-server connection task
# ---------------------------------------------------------------------------


class MCPServer:
    """One MCP server connection living in a single long-lived asyncio task.

    The entire lifecycle (connect → initialize → discover → serve → shut down)
    runs inside one task so the transport's anyio cancel-scopes are entered and
    exited in the same task context (an anyio requirement).
    """

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self.session: Optional[Any] = None
        self.tools: list = []
        self.error: Optional[str] = None
        self.tool_timeout: float = float(config.get("timeout", _DEFAULT_TOOL_TIMEOUT))
        self._task: Optional[asyncio.Task] = None
        self._ready: Optional[asyncio.Event] = None
        self._shutdown: Optional[asyncio.Event] = None
        self._rpc_lock: Optional[asyncio.Lock] = None

    def _is_http(self) -> bool:
        return bool(self.config.get("url"))

    async def _serve(self) -> None:
        """Connect, discover tools, then idle until shutdown is signalled."""
        self._ready = asyncio.Event()
        self._shutdown = asyncio.Event()
        self._rpc_lock = asyncio.Lock()
        connect_timeout = float(self.config.get("connect_timeout", _DEFAULT_CONNECT_TIMEOUT))
        try:
            if self._is_http():
                await self._serve_http(connect_timeout)
            else:
                await self._serve_stdio(connect_timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — one server's failure is isolated
            self.error = _sanitize_error(f"{type(exc).__name__}: {exc}")
            logger.warning("MCP server %r connection failed: %s", self.name, self.error)
        finally:
            self.session = None
            if self._ready is not None:
                self._ready.set()  # unblock waiters even on failure

    async def _after_connect(self, session) -> None:
        """Initialize + discover tools, then wait for shutdown. Shared by transports."""
        await asyncio.wait_for(session.initialize(), timeout=60.0)
        self.session = session
        async with self._rpc_lock:
            listed = await session.list_tools()
        self.tools = list(getattr(listed, "tools", []) or [])
        self._ready.set()
        await self._shutdown.wait()

    async def _serve_stdio(self, connect_timeout: float) -> None:
        import os

        command = self.config.get("command")
        if not command:
            raise ValueError("stdio server has no 'command'")
        args = list(self.config.get("args") or [])
        env = _build_safe_env(self.config.get("env"))
        params = StdioServerParameters(command=str(command), args=args, env=env)
        errlog = open(os.devnull, "w", encoding="utf-8")  # keep server banners off the TTY
        try:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await self._after_connect(session)
        finally:
            try:
                errlog.close()
            except Exception:  # noqa: BLE001
                pass

    async def _serve_http(self, connect_timeout: float) -> None:
        url = self.config["url"]
        headers = dict(self.config.get("headers") or {})
        if self.config.get("transport") == "sse":
            if not _MCP_SSE_AVAILABLE or sse_client is None:
                raise ImportError("SSE transport requested but mcp.client.sse is unavailable")
            async with sse_client(url=url, headers=headers or None, timeout=connect_timeout) as (read, write):
                async with ClientSession(read, write) as session:
                    await self._after_connect(session)
            return
        if not _MCP_HTTP_AVAILABLE:
            raise ImportError("HTTP transport requested but mcp.client.streamable_http is unavailable")
        async with streamablehttp_client(url, headers=headers or None) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await self._after_connect(session)

    async def _call_tool_async(self, tool_name: str, args: dict) -> str:
        """Invoke one tool on this server's session; return a JSON result string."""
        if self.session is None:
            return json.dumps(
                {"error": f"MCP server '{self.name}' is not connected"},
                ensure_ascii=False,
            )
        async with self._rpc_lock:
            result = await self.session.call_tool(tool_name, arguments=args)

        if getattr(result, "isError", False):
            error_text = "".join(
                getattr(b, "text", "") for b in (result.content or [])
            )
            return json.dumps(
                {"error": _sanitize_error(error_text or "MCP tool returned an error")},
                ensure_ascii=False,
            )

        parts: List[str] = []
        for block in (result.content or []):
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        text_result = "\n".join(parts)

        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            payload: Dict[str, Any] = {"result": text_result} if text_result else {}
            payload["structuredContent"] = structured
            return json.dumps(payload, ensure_ascii=False)
        return json.dumps({"result": text_result}, ensure_ascii=False)


def _make_tool_handler(server_name: str, tool_name: str):
    """Return a sync ``handler(args, **kw) -> str`` proxying to the MCP server.

    Matches the registry's dispatch contract (sync handler returning a JSON
    string). The blocking work is scheduled onto the shared MCP event loop.
    """

    def _handler(args: dict, **_kw: Any) -> str:
        with _lock:
            server = _servers.get(server_name)
        if server is None or server.session is None:
            return json.dumps(
                {"error": f"MCP server '{server_name}' is not connected"},
                ensure_ascii=False,
            )
        call_args = args if isinstance(args, dict) else {}
        try:
            return _run_on_loop(
                lambda: server._call_tool_async(tool_name, call_args),
                timeout=server.tool_timeout,
            )
        except TimeoutError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 — never crash the turn
            logger.warning("MCP tool %s/%s failed: %s", server_name, tool_name, exc)
            return json.dumps(
                {"error": _sanitize_error(f"MCP call failed: {type(exc).__name__}: {exc}")},
                ensure_ascii=False,
            )

    return _handler


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def _register_server_tools(server: MCPServer) -> List[str]:
    """Register every discovered tool of *server* into the JARVIS registry.

    Returns the list of registry names registered. Each tool's description is
    prefixed with ``[MCP:<server>]`` so the supervisor knows the provenance.
    """
    registered: List[str] = []
    for tool in server.tools:
        raw_name = getattr(tool, "name", None)
        if not raw_name:
            continue
        reg_name = make_tool_name(server.name, raw_name)
        description = (getattr(tool, "description", "") or "").strip()
        description = f"[MCP:{server.name}] {description}".strip()
        parameters = _normalize_mcp_input_schema(getattr(tool, "inputSchema", None))
        schema = {
            "name": reg_name,
            "description": description,
            "parameters": parameters,
        }
        try:
            registry.register(
                name=reg_name,
                toolset="mcp",
                schema=schema,
                handler=_make_tool_handler(server.name, raw_name),
                is_async=False,
                emoji="🔌",
            )
            registered.append(reg_name)
        except Exception as exc:  # noqa: BLE001 — one bad tool must not break the rest
            logger.warning(
                "MCP server %r: could not register tool %r: %s",
                server.name, raw_name, exc,
            )
    if registered:
        logger.info(
            "MCP server %r: registered %d tool(s): %s",
            server.name, len(registered), ", ".join(registered),
        )
    return registered


def _connect_server(name: str, config: dict) -> Optional[MCPServer]:
    """Connect to one server and wait until it is ready (or fails). Returns it.

    Returns ``None`` only when the connect task could not be scheduled at all
    (e.g. loop failure). A server that connects but reports an error returns the
    :class:`MCPServer` with ``.error`` set and an empty ``.tools`` list.
    """
    server = MCPServer(name, config)
    connect_timeout = float(config.get("connect_timeout", _DEFAULT_CONNECT_TIMEOUT))
    # Generous ceiling: connect + initialize + list_tools, plus headroom.
    wait_ceiling = connect_timeout + 90.0

    async def _spawn_and_wait():
        server._task = asyncio.ensure_future(server._serve())
        # _ready is created at the top of _serve(); poll briefly until it exists.
        for _ in range(200):
            if server._ready is not None:
                break
            await asyncio.sleep(0.01)
        if server._ready is not None:
            await server._ready.wait()
        return True

    try:
        _run_on_loop(_spawn_and_wait, timeout=wait_ceiling)
    except Exception as exc:  # noqa: BLE001 — isolate per-server connect failures
        server.error = _sanitize_error(f"{type(exc).__name__}: {exc}")
        logger.warning("MCP server %r did not become ready: %s", name, server.error)
    return server


def discover_mcp_tools() -> List[str]:
    """Connect to every configured MCP server and register their tools.

    Returns the list of registry tool names registered (empty when inert). This
    is the single entry point invoked at import time. It is a no-op — returning
    ``[]`` immediately — when:

      * the ``mcp`` SDK is not installed, or
      * ``~/.jarvis/mcp.json`` is absent / empty / has no usable server entries.

    Idempotent: a second call with servers already connected returns the
    already-registered names without reconnecting.
    """
    if not _MCP_AVAILABLE:
        logger.debug("MCP discovery skipped — mcp SDK not installed")
        return []

    config = load_mcp_config()
    if not config:
        logger.debug("MCP discovery skipped — no servers in ~/.jarvis/mcp.json")
        return []

    with _lock:
        if _servers:  # already connected in this process
            return list(_registered_tool_names)

    all_registered: List[str] = []
    connected: Dict[str, MCPServer] = {}
    for name, spec in config.items():
        try:
            server = _connect_server(name, spec)
        except Exception as exc:  # noqa: BLE001 — defensive: never abort discovery
            logger.warning("MCP server %r connect raised: %s", name, exc)
            continue
        if server is None:
            continue
        connected[name] = server
        if server.session is not None and server.tools:
            all_registered.extend(_register_server_tools(server))
        elif server.error:
            logger.warning(
                "MCP server %r unavailable (%s) — its tools are not registered",
                name, server.error,
            )

    with _lock:
        _servers.update(connected)
        _registered_tool_names[:] = all_registered
    return all_registered


def get_mcp_status() -> List[dict]:
    """Return a per-server status summary (diagnostics; not a tool)."""
    with _lock:
        servers = list(_servers.values())
    return [
        {
            "name": s.name,
            "connected": s.session is not None,
            "transport": "http/sse" if s._is_http() else "stdio",
            "tool_count": len(s.tools),
            "error": s.error,
        }
        for s in servers
    ]


def shutdown_mcp_servers() -> None:
    """Signal every server task to exit and deregister its tools.

    Test/teardown helper — the daemon loop thread is left running (cheap, idle).
    """
    with _lock:
        servers = list(_servers.values())
        names = list(_registered_tool_names)
        _servers.clear()
        _registered_tool_names.clear()

    for reg_name in names:
        registry.deregister(reg_name)

    loop = _loop
    if loop is not None and loop.is_running():
        for server in servers:
            ev = server._shutdown
            if ev is not None:
                loop.call_soon_threadsafe(ev.set)


# ---------------------------------------------------------------------------
# Discovery trigger
# ---------------------------------------------------------------------------
#
# This module is intentionally NOT picked up by the registry's AST walk — it has
# no top-level ``registry.register(...)`` call (registrations happen dynamically
# inside discover_mcp_tools() after a server is actually reached). Discovery is
# instead invoked explicitly by tools._adapter.load_all_livekit_tools(), right
# after plugin discovery and before the registry snapshot — the same hook the
# plugin system uses. Keeping it there (rather than at import time) makes the
# trigger deterministic and avoids running before the registry is ready.
#
# discover_mcp_tools() is a no-op (returns []) when the mcp SDK is absent or
# ~/.jarvis/mcp.json has no usable servers, so importing this module is always
# cheap and side-effect-free.

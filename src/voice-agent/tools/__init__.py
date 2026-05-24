"""JARVIS voice-agent tool layer.

Tools follow the ``ToolEntry`` model (a JSON schema + a handler callable,
self-registered at module level via ``registry.register(...)``).
``_adapter`` converts each registered entry into a LiveKit 1.5.x
``RawFunctionTool`` the supervisor can call.

Public entry points:
  * ``from tools.registry import registry, ToolEntry, all_entries``
  * ``from tools._adapter import to_livekit_tool, load_all_livekit_tools``
"""

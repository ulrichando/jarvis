"""LangGraph state-shape supervisor for the JARVIS voice agent.

Replaces the free-form supervisor LLM with a compiled state machine
whose terminal `speak_gate` node refuses to fire while any tool call
is unresolved. Spec: docs/superpowers/specs/2026-05-04-supervisor-
langgraph-design.md.

Public surface stays minimal — `build_graph()` returns a compiled
graph; `JarvisSupervisorGraphLLM` adapts it behind the LiveKit
`livekit.agents.llm.LLM` interface.
"""
from __future__ import annotations

"""Expose JARVIS tools as LangChain BaseTool objects.

This lets LangChain chains and agents use JARVIS tools without
reimplementing them. The bridge is one-way: JARVIS tools → LangChain.

Usage:
    from src.lc.tools_bridge import get_lc_tools
    tools = get_lc_tools(["rag_search", "web_search", "bash"])

    # Use in a LangChain chain/agent
    agent = create_react_agent(llm, tools, prompt)
"""

import json
import logging
from typing import Any, List, Optional

log = logging.getLogger(__name__)


class JARVISTool:
    """Wraps a JARVIS tool as a LangChain-compatible tool object."""

    def __init__(self, name: str, description: str, schema: dict):
        self.name = name
        self.description = description
        self._schema = schema

    def _run(self, **kwargs) -> str:
        from src.agent.tools import execute_tool
        try:
            return execute_tool(self.name, kwargs)
        except Exception as e:
            return f"Tool error: {e}"

    async def _arun(self, **kwargs) -> str:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self._run(**kwargs))

    def __call__(self, *args, **kwargs) -> str:
        if args and isinstance(args[0], str):
            # Try parsing as JSON first
            try:
                parsed = json.loads(args[0])
                if isinstance(parsed, dict):
                    return self._run(**parsed)
            except Exception:
                pass
            # Fallback: pass as the first required param
            required = self._schema.get("required", [])
            if required:
                return self._run(**{required[0]: args[0]})
        return self._run(**kwargs)

    @property
    def args_schema(self) -> dict:
        return self._schema

    def __repr__(self) -> str:
        return f"JARVISTool(name={self.name!r})"


def get_lc_tools(tool_names: List[str] | None = None) -> List[JARVISTool]:
    """Return JARVIS tools as LangChain-compatible tool objects.

    If tool_names is None, returns all tools.
    If a name is not found in TOOL_SCHEMAS, it is silently skipped.
    """
    from src.agent.tools import TOOL_SCHEMAS

    # Build lookup
    schema_by_name = {}
    for schema in TOOL_SCHEMAS:
        fn = schema.get("function", {})
        name = fn.get("name", "")
        if name:
            schema_by_name[name] = fn

    if tool_names is None:
        tool_names = list(schema_by_name.keys())

    tools = []
    for name in tool_names:
        if name not in schema_by_name:
            log.debug("Tool %r not found in TOOL_SCHEMAS, skipping.", name)
            continue
        fn = schema_by_name[name]
        tools.append(JARVISTool(
            name=name,
            description=fn.get("description", ""),
            schema=fn.get("parameters", {}),
        ))
    return tools


def get_rag_tool() -> JARVISTool:
    """Convenience: return just the rag_search tool."""
    tools = get_lc_tools(["rag_search"])
    if not tools:
        raise RuntimeError("rag_search tool not found in TOOL_SCHEMAS")
    return tools[0]

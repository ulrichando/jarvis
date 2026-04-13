"""LangSmith tracing — opt-in observability.

Activated when env LANGCHAIN_TRACING_V2=true is set.
Requires LANGCHAIN_API_KEY.

Uses langsmith.trace() — the context-manager API that properly propagates
parent context so nested trace_call() spans are correctly nested in the UI.

Usage:
    from src.lc.tracing import setup_tracing, trace_call
    setup_tracing()  # call once at startup

    with trace_call("agent_loop", {"input": query}, tags=["voice"], metadata={"model": "gpt-4"}) as run:
        result = ...
"""

import logging
import os
from contextlib import contextmanager
from typing import Any, Generator

log = logging.getLogger(__name__)

_VALID_RUN_TYPES = {"tool", "chain", "llm", "retriever", "embedding", "prompt", "parser"}


def is_tracing_enabled() -> bool:
    return os.environ.get("LANGCHAIN_TRACING_V2", "").lower() in ("true", "1", "yes")


def setup_tracing(project: str | None = None) -> bool:
    """Configure LangSmith. Returns True if tracing is active."""
    if not is_tracing_enabled():
        return False

    api_key = os.environ.get("LANGCHAIN_API_KEY", "")
    if not api_key:
        log.warning("LANGCHAIN_TRACING_V2=true but LANGCHAIN_API_KEY not set — tracing disabled.")
        return False

    if project:
        os.environ["LANGCHAIN_PROJECT"] = project

    try:
        import langsmith  # noqa: F401
        log.info(
            "LangSmith tracing enabled (project=%s)",
            os.environ.get("LANGCHAIN_PROJECT", "default"),
        )
        return True
    except ImportError:
        log.warning("langsmith not installed. Run: pip install langsmith")
        return False


class _NoopRun:
    """Dummy run object when tracing is off or unavailable."""
    id = None
    outputs = None

    def end(self, **kwargs): pass
    def patch(self, **kwargs): pass
    def post(self, **kwargs): pass


@contextmanager
def trace_call(
    name: str,
    inputs: dict[str, Any] | None = None,
    run_type: str = "chain",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Generator[Any, None, None]:
    """Context manager that traces a block with LangSmith if enabled.

    Uses langsmith.trace() which sets LangSmith's internal context variables
    so any nested trace_call() spans are automatically attached as children,
    producing a properly nested trace tree in the UI.

    Args:
        name:      Display name for this span in the UI.
        inputs:    Input data to record on the run.
        run_type:  One of chain, tool, llm, retriever, etc.
        tags:      List of string tags for filtering runs in the UI.
        metadata:  Arbitrary key-value metadata shown in run details.
    """
    if not is_tracing_enabled():
        yield _NoopRun()
        return

    if run_type not in _VALID_RUN_TYPES:
        run_type = "chain"

    try:
        from langsmith import trace

        with trace(
            name=name,
            run_type=run_type,
            inputs=inputs or {},
            project_name=os.environ.get("LANGCHAIN_PROJECT", "jarvis"),
            tags=tags or [],
            metadata=metadata or {},
        ) as run:
            try:
                yield run
            except Exception:
                raise

    except ImportError:
        log.debug("langsmith not available — tracing disabled.")
        yield _NoopRun()
    except Exception as e:
        log.debug("LangSmith trace_call error: %s", e)
        yield _NoopRun()

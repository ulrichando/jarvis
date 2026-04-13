"""LangSmith tracing — opt-in observability.

Activated when env LANGCHAIN_TRACING_V2=true is set.
Requires LANGCHAIN_API_KEY.

Uses langsmith.RunTree — the stable low-level API that works across versions.

Usage:
    from src.lc.tracing import setup_tracing, trace_call
    setup_tracing()  # call once at startup

    with trace_call("agent_loop", {"input": query}) as run:
        result = ...
        run.end(outputs={"output": result})
"""

import logging
import os
from contextlib import contextmanager
from typing import Any, Generator

log = logging.getLogger(__name__)

# Valid LangSmith run types
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
    def end(self, **kwargs): pass
    def patch(self, **kwargs): pass
    def post(self, **kwargs): pass


@contextmanager
def trace_call(
    name: str,
    inputs: dict[str, Any] | None = None,
    run_type: str = "chain",
) -> Generator[Any, None, None]:
    """Context manager that traces a block with LangSmith if enabled.

    Uses langsmith.RunTree which is the stable low-level API.
    Falls back to a no-op if tracing is disabled or LangSmith is unreachable.
    """
    if not is_tracing_enabled():
        yield _NoopRun()
        return

    # Normalise run_type
    if run_type not in _VALID_RUN_TYPES:
        run_type = "chain"

    try:
        from langsmith.run_trees import RunTree

        run = RunTree(
            name=name,
            run_type=run_type,
            inputs=inputs or {},
            project_name=os.environ.get("LANGCHAIN_PROJECT", "jarvis"),
        )
        run.post()  # sends the run-start event

        try:
            yield run
        except Exception as exc:
            run.end(error=str(exc))
            run.patch()
            raise
        else:
            # Only call end() if the caller hasn't already done so
            if not getattr(run, "_end_time", None):
                run.end(outputs={})
            run.patch()  # sends the run-end event

    except ImportError:
        log.debug("langsmith.run_trees not available — tracing disabled.")
        yield _NoopRun()
    except Exception as e:
        log.debug("LangSmith trace_call error: %s", e)
        yield _NoopRun()

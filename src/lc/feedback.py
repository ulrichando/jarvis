"""LangSmith feedback logging — let users rate JARVIS responses.

When tracing is enabled, each agent_loop run gets a run_id.
Feedback (thumbs up/down, score, comment) is posted back to that run.
"""

import logging
import os

log = logging.getLogger(__name__)

# Thread-local storage for the current run ID (set by agent loop)
_current_run_id: str | None = None


def set_current_run_id(run_id: str | None) -> None:
    """Called by agent loop to register the active trace run ID."""
    global _current_run_id
    _current_run_id = run_id


def get_current_run_id() -> str | None:
    return _current_run_id


def log_feedback(
    score: float,                # 0.0 = bad, 1.0 = good
    comment: str = "",
    key: str = "user_feedback",
    run_id: str | None = None,
) -> bool:
    """Post feedback to LangSmith for the given run_id.

    Args:
        score: 0.0-1.0 (0 = thumbs down, 1 = thumbs up)
        comment: Optional text comment from user
        key: Feedback key name in LangSmith
        run_id: Run ID to attach feedback to (defaults to current run)

    Returns:
        True if feedback was posted successfully.
    """
    from src.lc.tracing import is_tracing_enabled
    if not is_tracing_enabled():
        log.debug("Tracing disabled — feedback not logged")
        return False

    target_id = run_id or _current_run_id
    if not target_id:
        log.warning("No run_id available for feedback — was agent_loop traced?")
        return False

    try:
        from langsmith import Client
        client = Client()
        client.create_feedback(
            run_id=target_id,
            key=key,
            score=score,
            comment=comment or None,
        )
        log.info("Feedback logged: run=%s score=%.1f key=%s", target_id, score, key)
        return True
    except ImportError:
        log.warning("langsmith not installed — cannot log feedback")
        return False
    except Exception as e:
        log.warning("Failed to log feedback: %s", e)
        return False

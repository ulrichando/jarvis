"""LangSmith feedback logging — let users rate JARVIS responses.

When tracing is enabled, each agent_loop run gets a run_id.
Feedback (thumbs up/down, score, comment) is posted back to that run.

Feedback dimensions configured at startup:
  quality    — overall response quality (0.0–1.0)
  accuracy   — factual/task correctness (0.0–1.0)
  helpful    — did it actually help? (0.0 or 1.0)
  user_score — raw score from explicit rating
"""

import logging
from contextvars import ContextVar

log = logging.getLogger(__name__)

# Per-coroutine run ID — safe for concurrent async requests
_current_run_id: ContextVar[str | None] = ContextVar("_current_run_id", default=None)


def set_current_run_id(run_id: str | None) -> None:
    """Called by agent loop to register the active trace run ID."""
    _current_run_id.set(run_id)


def get_current_run_id() -> str | None:
    return _current_run_id.get()


# ── Feedback config ───────────────────────────────────────────────────────────

_FEEDBACK_CONFIGS = [
    {
        "key": "quality",
        "type": "continuous",
        "min": 0.0,
        "max": 1.0,
        "comment": "Overall response quality",
    },
    {
        "key": "accuracy",
        "type": "continuous",
        "min": 0.0,
        "max": 1.0,
        "comment": "Factual / task correctness",
    },
    {
        "key": "helpful",
        "type": "continuous",
        "min": 0.0,
        "max": 1.0,
        "comment": "Did the response actually help?",
    },
    {
        "key": "user_score",
        "type": "continuous",
        "min": 0.0,
        "max": 1.0,
        "comment": "Explicit user rating",
    },
]


def setup_feedback_configs() -> bool:
    """Create or update feedback dimension configs in LangSmith.

    Called once at startup. Safe to call multiple times — idempotent.
    Returns True if configs were applied.
    """
    from src.lc.tracing import is_tracing_enabled
    if not is_tracing_enabled():
        return False
    try:
        from langsmith import Client
        client = Client()
        existing = {fc.key for fc in client.list_feedback_configs()}
        for cfg in _FEEDBACK_CONFIGS:
            if cfg["key"] not in existing:
                client.create_feedback_config(
                    feedback_key=cfg["key"],
                    feedback_config={
                        "type": cfg["type"],
                        "min": cfg["min"],
                        "max": cfg["max"],
                    },
                )
                log.debug("Created feedback config: %s", cfg["key"])
        log.info("LangSmith feedback configs ready (%d dimensions)", len(_FEEDBACK_CONFIGS))
        return True
    except Exception as e:
        log.debug("setup_feedback_configs error: %s", e)
        return False


# ── Feedback logging ──────────────────────────────────────────────────────────

def log_feedback(
    score: float,
    comment: str = "",
    key: str = "quality",
    run_id: str | None = None,
) -> bool:
    """Post feedback to LangSmith for the given run.

    Args:
        score:   0.0–1.0 (0 = bad, 1 = good)
        comment: Optional text comment
        key:     Feedback dimension (quality, accuracy, helpful, user_score)
        run_id:  Run to attach to; defaults to current agent_loop run
    """
    from src.lc.tracing import is_tracing_enabled
    if not is_tracing_enabled():
        return False

    target_id = run_id or _current_run_id.get()
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
        log.info("Feedback logged: run=%s key=%s score=%.2f", target_id, key, score)
        return True
    except ImportError:
        log.warning("langsmith not installed — cannot log feedback")
        return False
    except Exception as e:
        log.warning("Failed to log feedback: %s", e)
        return False


def thumbs_up(comment: str = "", run_id: str | None = None) -> bool:
    """Convenience: log positive feedback across all dimensions."""
    ok = True
    for key in ("quality", "helpful", "user_score"):
        ok = log_feedback(1.0, comment=comment, key=key, run_id=run_id) and ok
    return ok


def thumbs_down(comment: str = "", run_id: str | None = None) -> bool:
    """Convenience: log negative feedback across all dimensions."""
    ok = True
    for key in ("quality", "helpful", "user_score"):
        ok = log_feedback(0.0, comment=comment, key=key, run_id=run_id) and ok
    return ok

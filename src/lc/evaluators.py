"""LangSmith evaluation and annotation queue management.

Provides:
  - Automated dataset evaluation (run model against known-good examples)
  - Annotation queues (route low-quality or interesting runs for human review)
  - Online evaluation (score live runs as they arrive)
"""

import logging
from typing import Any, Callable

log = logging.getLogger(__name__)

# Name of the annotation queue for runs needing human review
ANNOTATION_QUEUE_NAME = "jarvis-review"


# ── Annotation queue ──────────────────────────────────────────────────────────

def setup_annotation_queue() -> str | None:
    """Create (or get) the JARVIS human review annotation queue.

    Returns the queue ID if successful, None otherwise.
    Called once at startup.
    """
    from src.lc.tracing import is_tracing_enabled
    if not is_tracing_enabled():
        return None
    try:
        from langsmith import Client
        client = Client()
        # Check if queue already exists
        queues = list(client.list_annotation_queues(name=ANNOTATION_QUEUE_NAME))
        if queues:
            log.info("LangSmith annotation queue ready: %s (id=%s)", ANNOTATION_QUEUE_NAME, queues[0].id)
            return str(queues[0].id)
        # Create it
        q = client.create_annotation_queue(
            name=ANNOTATION_QUEUE_NAME,
            description="JARVIS runs flagged for human review — errors, low quality, or edge cases",
        )
        log.info("Created LangSmith annotation queue: %s (id=%s)", ANNOTATION_QUEUE_NAME, q.id)
        return str(q.id)
    except Exception as e:
        log.debug("setup_annotation_queue error: %s", e)
        return None


def flag_run_for_review(run_id: str, reason: str = "") -> bool:
    """Add a run to the human review annotation queue.

    Call this when a run produced an error, low-quality output,
    or is otherwise interesting to review.

    Args:
        run_id: The LangSmith run ID to flag.
        reason: Optional human-readable reason for flagging.
    """
    from src.lc.tracing import is_tracing_enabled
    if not is_tracing_enabled() or not run_id:
        return False
    try:
        from langsmith import Client
        client = Client()
        queues = list(client.list_annotation_queues(name=ANNOTATION_QUEUE_NAME))
        if not queues:
            queue_id = setup_annotation_queue()
        else:
            queue_id = str(queues[0].id)
        if not queue_id:
            return False
        client.add_runs_to_annotation_queue(
            queue_id=queue_id,
            run_ids=[run_id],
        )
        log.info("Flagged run %s for review (reason: %s)", run_id, reason or "unspecified")
        return True
    except Exception as e:
        log.debug("flag_run_for_review error: %s", e)
        return False


# ── Dataset evaluation ────────────────────────────────────────────────────────

def evaluate_dataset(
    dataset_name: str,
    evaluator_fn: Callable[[dict, dict], dict],
    experiment_prefix: str = "jarvis",
) -> dict | None:
    """Run JARVIS against a LangSmith dataset and score each example.

    Args:
        dataset_name:    Name of the LangSmith dataset to evaluate against.
        evaluator_fn:    Function(inputs, outputs) → {"score": 0-1, "comment": str}
        experiment_prefix: Prefix for the experiment name in LangSmith UI.

    Returns:
        Dict with experiment results, or None on failure.
    """
    from src.lc.tracing import is_tracing_enabled
    if not is_tracing_enabled():
        log.warning("evaluate_dataset: tracing disabled — skipping evaluation")
        return None
    try:
        from langsmith import Client, evaluate
        from src.lc.model_adapter import JARVISChatModel

        client = Client()
        llm = JARVISChatModel(prefer_smart=True)

        def _target(inputs: dict) -> dict:
            query = inputs.get("input", inputs.get("query", str(inputs)))
            response = llm.invoke(query)
            if isinstance(response, dict):
                content = response.get("content", str(response))
            elif hasattr(response, "content"):
                content = response.content
            else:
                content = str(response)
            return {"output": content}

        def _wrapped_evaluator(run, example) -> dict:
            try:
                outputs = run.outputs or {}
                expected = example.outputs or {}
                result = evaluator_fn(example.inputs, outputs)
                return {"key": "custom_eval", "score": result.get("score", 0.0), "comment": result.get("comment", "")}
            except Exception as e:
                return {"key": "custom_eval", "score": 0.0, "comment": f"Evaluator error: {e}"}

        results = evaluate(
            _target,
            data=dataset_name,
            evaluators=[_wrapped_evaluator],
            experiment_prefix=experiment_prefix,
        )
        log.info("Evaluation complete: dataset=%s experiment=%s", dataset_name, experiment_prefix)
        return {"dataset": dataset_name, "results": results}

    except ImportError:
        log.warning("langsmith evaluate not available")
        return None
    except Exception as e:
        log.warning("evaluate_dataset error: %s", e)
        return None


# ── Run sharing ───────────────────────────────────────────────────────────────

def get_run_url(run_id: str) -> str | None:
    """Return a shareable URL for a specific run.

    Args:
        run_id: LangSmith run ID.

    Returns:
        URL string, or None on failure.
    """
    from src.lc.tracing import is_tracing_enabled
    if not is_tracing_enabled() or not run_id:
        return None
    try:
        from langsmith import Client
        client = Client()
        return client.get_run_url(run_id=run_id)
    except Exception as e:
        log.debug("get_run_url error: %s", e)
        return None


def share_run(run_id: str) -> str | None:
    """Make a run public and return its shared URL.

    Args:
        run_id: LangSmith run ID to share.

    Returns:
        Shareable URL string, or None on failure.
    """
    from src.lc.tracing import is_tracing_enabled
    if not is_tracing_enabled() or not run_id:
        return None
    try:
        from langsmith import Client
        client = Client()
        shared_url = client.share_run(run_id=run_id)
        log.info("Run shared: %s → %s", run_id, shared_url)
        return shared_url
    except Exception as e:
        log.debug("share_run error: %s", e)
        return None


# ── Run stats ─────────────────────────────────────────────────────────────────

def get_project_stats() -> dict | None:
    """Return aggregated stats for the JARVIS project.

    Returns a dict with error_rate, latency, token usage, etc.
    """
    from src.lc.tracing import is_tracing_enabled
    if not is_tracing_enabled():
        return None
    import os
    try:
        from langsmith import Client
        client = Client()
        project_name = os.environ.get("LANGCHAIN_PROJECT", "jarvis")
        project = client.read_project(project_name=project_name)
        return {
            "run_count": project.run_count,
            "latency_p50": project.latency_p50,
            "latency_p99": project.latency_p99,
            "total_tokens": project.total_tokens,
            "total_cost": project.total_cost,
            "error_rate": project.error_rate,
            "feedback_stats": project.feedback_stats,
        }
    except Exception as e:
        log.debug("get_project_stats error: %s", e)
        return None

"""LangSmith dataset management — curate JARVIS conversations into test sets.

A dataset is a collection of (input, expected_output) pairs used for:
- Regression testing: run new model versions against known-good examples
- Evaluation: score agent responses automatically
- Fine-tuning data: export to JSONL for training
"""

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


def get_or_create_dataset(name: str, description: str = "") -> Any | None:
    """Get an existing LangSmith dataset by name, or create it."""
    from src.lc.tracing import is_tracing_enabled
    if not is_tracing_enabled():
        return None
    try:
        from langsmith import Client
        client = Client()
        # Check if dataset exists
        try:
            datasets = list(client.list_datasets(dataset_name=name))
            if datasets:
                log.debug("Found existing dataset: %s", name)
                return datasets[0]
        except Exception:
            pass
        # Create it
        ds = client.create_dataset(
            dataset_name=name,
            description=description or f"JARVIS auto-curated dataset: {name}",
        )
        log.info("Created LangSmith dataset: %s (id=%s)", name, ds.id)
        return ds
    except ImportError:
        log.warning("langsmith not installed")
        return None
    except Exception as e:
        log.warning("Dataset creation failed: %s", e)
        return None


def add_example(
    dataset_name: str,
    inputs: dict,
    outputs: dict,
    metadata: dict | None = None,
) -> bool:
    """Add a single (input, output) example to a dataset."""
    from src.lc.tracing import is_tracing_enabled
    if not is_tracing_enabled():
        return False
    try:
        from langsmith import Client
        client = Client()
        ds = get_or_create_dataset(dataset_name)
        if not ds:
            return False
        client.create_example(
            inputs=inputs,
            outputs=outputs,
            metadata=metadata or {},
            dataset_id=ds.id,
        )
        log.info("Added example to dataset '%s'", dataset_name)
        return True
    except Exception as e:
        log.warning("Failed to add example: %s", e)
        return False


def add_from_conversation(
    dataset_name: str,
    user_input: str,
    jarvis_output: str,
    metadata: dict | None = None,
) -> bool:
    """Convenience: add a conversation turn as a dataset example."""
    return add_example(
        dataset_name=dataset_name,
        inputs={"input": user_input},
        outputs={"output": jarvis_output},
        metadata=metadata,
    )


def list_datasets() -> list[dict]:
    """List all JARVIS datasets in LangSmith."""
    from src.lc.tracing import is_tracing_enabled
    if not is_tracing_enabled():
        return []
    try:
        from langsmith import Client
        client = Client()
        datasets = list(client.list_datasets())
        return [{"name": d.name, "id": str(d.id), "example_count": getattr(d, 'example_count', '?')} for d in datasets]
    except Exception as e:
        log.warning("Failed to list datasets: %s", e)
        return []

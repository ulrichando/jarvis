"""LangSmith Prompt Hub — version and manage JARVIS system prompts.

Allows pushing/pulling the JARVIS system prompt to LangSmith so you can:
  - Version prompts with commit hashes
  - A/B test prompt variants
  - See prompt change history in the LangSmith UI
  - Collaborate on prompt engineering
"""

import logging
import os

log = logging.getLogger(__name__)

# Registry of prompt names managed in LangSmith Hub
PROMPT_REGISTRY = {
    "jarvis-system": "Main JARVIS system prompt",
    "jarvis-agent-scout": "Scout sub-agent system prompt",
    "jarvis-agent-worker": "Worker sub-agent system prompt",
    "jarvis-agent-planner": "Planner sub-agent system prompt",
    "jarvis-agent-verifier": "Verifier sub-agent system prompt",
}


def push_prompt(name: str, prompt_text: str, description: str = "") -> bool:
    """Push a prompt to LangSmith Hub, versioning it with a new commit.

    Args:
        name:        Prompt name in the registry (e.g. "jarvis-system").
        prompt_text: The full prompt text.
        description: Optional description of what changed.

    Returns:
        True if pushed successfully.
    """
    from src.lc.tracing import is_tracing_enabled
    if not is_tracing_enabled():
        return False
    try:
        from langsmith import Client
        from langsmith.schemas import PromptCommit
        client = Client()
        # Build a simple chat prompt template
        manifest = {
            "lc": 1,
            "type": "constructor",
            "id": ["langchain", "prompts", "chat", "ChatPromptTemplate"],
            "kwargs": {
                "messages": [
                    {
                        "lc": 1,
                        "type": "constructor",
                        "id": ["langchain", "prompts", "chat", "SystemMessagePromptTemplate"],
                        "kwargs": {
                            "prompt": {
                                "lc": 1,
                                "type": "constructor",
                                "id": ["langchain", "prompts", "prompt", "PromptTemplate"],
                                "kwargs": {
                                    "template": prompt_text,
                                    "input_variables": [],
                                    "template_format": "f-string",
                                }
                            }
                        }
                    }
                ]
            }
        }
        client.push_prompt(
            prompt_identifier=name,
            object=manifest,
            description=description or PROMPT_REGISTRY.get(name, ""),
        )
        log.info("Pushed prompt to LangSmith Hub: %s", name)
        return True
    except Exception as e:
        log.debug("push_prompt error for %s: %s", name, e)
        return False


def pull_prompt(name: str) -> str | None:
    """Pull the latest version of a prompt from LangSmith Hub.

    Args:
        name: Prompt name in the registry.

    Returns:
        Prompt text string, or None if not found.
    """
    from src.lc.tracing import is_tracing_enabled
    if not is_tracing_enabled():
        return None
    try:
        from langsmith import Client
        client = Client()
        prompt = client.get_prompt(prompt_identifier=name)
        if not prompt:
            return None
        # Extract text from the manifest
        messages = (prompt.manifest or {}).get("kwargs", {}).get("messages", [])
        if messages:
            template = (
                messages[0]
                .get("kwargs", {})
                .get("prompt", {})
                .get("kwargs", {})
                .get("template", "")
            )
            return template or None
        return None
    except Exception as e:
        log.debug("pull_prompt error for %s: %s", name, e)
        return None


def push_all_agent_prompts() -> dict[str, bool]:
    """Push all JARVIS agent prompts to LangSmith Hub in one call.

    Reads prompts from src.agent.agents and the main system prompt builder.
    Returns a dict of {prompt_name: success}.
    """
    from src.lc.tracing import is_tracing_enabled
    if not is_tracing_enabled():
        return {}

    results: dict[str, bool] = {}

    try:
        from src.agent.agents import SCOUT_PROMPT, WORKER_PROMPT, PLANNER_PROMPT, VERIFIER_PROMPT
        agent_prompts = {
            "jarvis-agent-scout": SCOUT_PROMPT,
            "jarvis-agent-worker": WORKER_PROMPT,
            "jarvis-agent-planner": PLANNER_PROMPT,
            "jarvis-agent-verifier": VERIFIER_PROMPT,
        }
        for name, text in agent_prompts.items():
            results[name] = push_prompt(name, text)
    except Exception as e:
        log.debug("push_all_agent_prompts error: %s", e)

    try:
        from src.prompt_builder import build_system_prompt
        system_prompt = build_system_prompt()
        results["jarvis-system"] = push_prompt("jarvis-system", system_prompt)
    except Exception as e:
        log.debug("push_all_agent_prompts (system) error: %s", e)

    log.info(
        "Pushed prompts to LangSmith Hub: %d/%d",
        sum(results.values()),
        len(results),
    )
    return results


def list_prompt_versions(name: str) -> list[dict]:
    """List all committed versions of a prompt in LangSmith Hub.

    Args:
        name: Prompt name in the registry.

    Returns:
        List of commit dicts with hash, created_at, etc.
    """
    from src.lc.tracing import is_tracing_enabled
    if not is_tracing_enabled():
        return []
    try:
        from langsmith import Client
        client = Client()
        commits = list(client.list_prompt_commits(prompt_identifier=name))
        return [
            {
                "commit_hash": c.commit_hash,
                "created_at": str(c.created_at),
                "parent_commit": c.parent_commit,
            }
            for c in commits
        ]
    except Exception as e:
        log.debug("list_prompt_versions error for %s: %s", name, e)
        return []

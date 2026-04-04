"""
Advisor tool types and configuration.

Provides types for advisor blocks, configuration checks, and
the advisor tool instruction prompt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional, TypedDict, Union


class AdvisorServerToolUseBlock(TypedDict):
    type: str  # "server_tool_use"
    id: str
    name: str  # "advisor"
    input: dict[str, Any]


class AdvisorResultContent(TypedDict):
    type: str  # "advisor_result"
    text: str


class AdvisorRedactedResultContent(TypedDict):
    type: str  # "advisor_redacted_result"
    encrypted_content: str


class AdvisorToolResultErrorContent(TypedDict):
    type: str  # "advisor_tool_result_error"
    error_code: str


AdvisorToolResultContent = Union[
    AdvisorResultContent, AdvisorRedactedResultContent, AdvisorToolResultErrorContent
]


class AdvisorToolResultBlock(TypedDict):
    type: str  # "advisor_tool_result"
    tool_use_id: str
    content: AdvisorToolResultContent


AdvisorBlock = Union[AdvisorServerToolUseBlock, AdvisorToolResultBlock]


def is_advisor_block(param: dict[str, Any]) -> bool:
    """Check if a block is an advisor block."""
    return param.get("type") == "advisor_tool_result" or (
        param.get("type") == "server_tool_use" and param.get("name") == "advisor"
    )


@dataclass
class AdvisorConfig:
    enabled: bool = False
    can_user_configure: bool = False
    base_model: Optional[str] = None
    advisor_model: Optional[str] = None


def _get_advisor_config() -> AdvisorConfig:
    """Get advisor configuration. Stub -- real impl depends on feature flags."""
    return AdvisorConfig()


def is_advisor_enabled() -> bool:
    """Check if the advisor tool is enabled."""
    if os.environ.get("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        return False
    return _get_advisor_config().enabled


def can_user_configure_advisor() -> bool:
    return is_advisor_enabled() and _get_advisor_config().can_user_configure


def get_experiment_advisor_models() -> Optional[dict[str, str]]:
    config = _get_advisor_config()
    if (
        is_advisor_enabled()
        and not can_user_configure_advisor()
        and config.base_model
        and config.advisor_model
    ):
        return {
            "base_model": config.base_model,
            "advisor_model": config.advisor_model,
        }
    return None


def model_supports_advisor(model: str) -> bool:
    m = model.lower()
    return "opus-4-6" in m or "sonnet-4-6" in m or os.environ.get("USER_TYPE") == "ant"


def is_valid_advisor_model(model: str) -> bool:
    m = model.lower()
    return "opus-4-6" in m or "sonnet-4-6" in m or os.environ.get("USER_TYPE") == "ant"


def get_initial_advisor_setting() -> Optional[str]:
    if not is_advisor_enabled():
        return None
    return None  # Stub: would call getInitialSettings().advisorModel


def get_advisor_usage(usage: dict[str, Any]) -> list[dict[str, Any]]:
    iterations = usage.get("iterations")
    if not iterations:
        return []
    return [it for it in iterations if it.get("type") == "advisor_message"]


ADVISOR_TOOL_INSTRUCTIONS = """# Advisor Tool

You have access to an `advisor` tool backed by a stronger reviewer model. It takes NO parameters -- when you call it, your entire conversation history is automatically forwarded. The advisor sees the task, every tool call you've made, every result you've seen.

Call advisor BEFORE substantive work -- before writing code, before committing to an interpretation, before building on an assumption. If the task requires orientation first (finding files, reading code, seeing what's there), do that, then call advisor. Orientation is not substantive work. Writing, editing, and declaring an answer are.

Also call advisor:
- When you believe the task is complete. BEFORE this call, make your deliverable durable: write the file, stage the change, save the result. The advisor call takes time; if the session ends during it, a durable result persists and an unwritten one doesn't.
- When stuck -- errors recurring, approach not converging, results that don't fit.
- When considering a change of approach.

On tasks longer than a few steps, call advisor at least once before committing to an approach and once before declaring done. On short reactive tasks where the next action is dictated by tool output you just read, you don't need to keep calling -- the advisor adds most of its value on the first call, before the approach crystallizes.

Give the advice serious weight. If you follow a step and it fails empirically, or you have primary-source evidence that contradicts a specific claim (the file says X, the code does Y), adapt. A passing self-test is not evidence the advice is wrong -- it's evidence your test doesn't check what the advice is checking.

If you've already retrieved data pointing one way and the advisor points another: don't silently switch. Surface the conflict in one more advisor call -- "I found X, you suggest Y, which constraint breaks the tie?" The advisor saw your evidence but may have underweighted it; a reconcile call is cheaper than committing to the wrong branch."""

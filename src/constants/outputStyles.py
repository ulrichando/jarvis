"""Output style configuration and management."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, List, Literal, Optional, Union


SettingSource = str  # Simplified from TypeScript's SettingSource type
OutputStyle = str  # Simplified from TypeScript's OutputStyle type


@dataclass
class OutputStyleConfig:
    name: str
    description: str
    prompt: str
    source: Union[str, Literal["built-in", "plugin"]]
    keep_coding_instructions: bool = False
    force_for_plugin: bool = False


DEFAULT_OUTPUT_STYLE_NAME = "default"

# Used in both the Explanatory and Learning modes
_EXPLANATORY_FEATURE_PROMPT = """
## Insights
In order to encourage learning, before and after writing code, always provide brief educational explanations about implementation choices using (with backticks):
"`* Insight ─────────────────────────────────────`
[2-3 key educational points]
`─────────────────────────────────────────────────`"

These insights should be included in the conversation, not in the codebase. You should generally focus on interesting insights that are specific to the codebase or the code you just wrote, rather than general programming concepts."""

OUTPUT_STYLE_CONFIG: Dict[str, Optional[OutputStyleConfig]] = {
    DEFAULT_OUTPUT_STYLE_NAME: None,
    "Explanatory": OutputStyleConfig(
        name="Explanatory",
        source="built-in",
        description="JARVIS explains its implementation choices and codebase patterns",
        keep_coding_instructions=True,
        prompt=(
            "You are an interactive CLI tool that helps users with software engineering "
            "tasks. In addition to software engineering tasks, you should provide "
            "educational insights about the codebase along the way.\n\n"
            "You should be clear and educational, providing helpful explanations while "
            "remaining focused on the task. Balance educational content with task "
            "completion. When providing insights, you may exceed typical length "
            "constraints, but remain focused and relevant.\n\n"
            "# Explanatory Style Active\n"
            f"{_EXPLANATORY_FEATURE_PROMPT}"
        ),
    ),
    "Learning": OutputStyleConfig(
        name="Learning",
        source="built-in",
        description="JARVIS pauses and asks you to write small pieces of code for hands-on practice",
        keep_coding_instructions=True,
        prompt=(
            "You are an interactive CLI tool that helps users with software engineering "
            "tasks. In addition to software engineering tasks, you should help users "
            "learn more about the codebase through hands-on practice and educational "
            "insights.\n\n"
            "You should be collaborative and encouraging. Balance task completion with "
            "learning by requesting user input for meaningful design decisions while "
            "handling routine implementation yourself.\n\n"
            "# Learning Style Active\n"
            "## Requesting Human Contributions\n"
            "In order to encourage learning, ask the human to contribute 2-10 line "
            "code pieces when generating 20+ lines involving:\n"
            "- Design decisions (error handling, data structures)\n"
            "- Business logic with multiple valid approaches\n"
            "- Key algorithms or interface definitions\n\n"
            f"{_EXPLANATORY_FEATURE_PROMPT}"
        ),
    ),
}


def clear_all_output_styles_cache() -> None:
    """Clear the output styles cache."""
    get_all_output_styles.cache_clear()


@lru_cache(maxsize=1)
def get_all_output_styles() -> Dict[str, Optional[OutputStyleConfig]]:
    """Get all available output styles including custom and plugin styles."""
    # Start with built-in modes
    all_styles = dict(OUTPUT_STYLE_CONFIG)
    # Custom and plugin styles would be loaded here in a full implementation
    return all_styles


def get_output_style_config() -> Optional[OutputStyleConfig]:
    """Get the currently active output style configuration."""
    all_styles = get_all_output_styles()
    # Default to the default style
    return all_styles.get(DEFAULT_OUTPUT_STYLE_NAME)


def has_custom_output_style() -> bool:
    """Check if a custom output style is set."""
    return False  # Simplified - would check settings in full implementation

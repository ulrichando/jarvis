"""Advisor command - Configure the advisor model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class CommandResult:
    type: str
    value: str


async def call(args: str, context: Any) -> CommandResult:
    """Configure the advisor model."""
    from ..utils.advisor import (
        can_user_configure_advisor,
        is_valid_advisor_model,
        model_supports_advisor,
    )
    from ..utils.model.model import (
        get_default_main_loop_model_setting,
        normalize_model_string_for_api,
        parse_user_specified_model,
    )
    from ..utils.model.validate_model import validate_model
    from ..utils.settings.settings import update_settings_for_source

    arg = args.strip().lower()
    app_state = context.get_app_state()
    base_model = parse_user_specified_model(
        app_state.main_loop_model or get_default_main_loop_model_setting()
    )

    if not arg:
        current = app_state.advisor_model
        if not current:
            return CommandResult(
                type="text",
                value='Advisor: not set\nUse "/advisor <model>" to enable (e.g. "/advisor opus").',
            )
        if not model_supports_advisor(base_model):
            return CommandResult(
                type="text",
                value=f"Advisor: {current} (inactive)\nThe current model ({base_model}) does not support advisors.",
            )
        return CommandResult(
            type="text",
            value=f'Advisor: {current}\nUse "/advisor unset" to disable or "/advisor <model>" to change.',
        )

    if arg in ("unset", "off"):
        prev = app_state.advisor_model
        context.set_app_state(lambda s: {**s, "advisor_model": None})
        update_settings_for_source("userSettings", {"advisorModel": None})
        return CommandResult(
            type="text",
            value=f"Advisor disabled (was {prev})." if prev else "Advisor already unset.",
        )

    normalized_model = normalize_model_string_for_api(arg)
    resolved_model = parse_user_specified_model(arg)
    valid, error = await validate_model(resolved_model)
    if not valid:
        return CommandResult(
            type="text",
            value=f"Invalid advisor model: {error}" if error else f"Unknown model: {arg} ({resolved_model})",
        )

    if not is_valid_advisor_model(resolved_model):
        return CommandResult(
            type="text",
            value=f"The model {arg} ({resolved_model}) cannot be used as an advisor",
        )

    context.set_app_state(lambda s: {**s, "advisor_model": normalized_model})
    update_settings_for_source("userSettings", {"advisorModel": normalized_model})

    if not model_supports_advisor(base_model):
        return CommandResult(
            type="text",
            value=(
                f"Advisor set to {normalized_model}.\n"
                f"Note: Your current model ({base_model}) does not support advisors. "
                "Switch to a supported model to use the advisor."
            ),
        )

    return CommandResult(
        type="text",
        value=f"Advisor set to {normalized_model}.",
    )


advisor = {
    "type": "local",
    "name": "advisor",
    "description": "Configure the advisor model",
    "argument_hint": "[<model>|off]",
    "is_enabled": lambda: can_user_configure_advisor(),
    "supports_non_interactive": True,
    "call": call,
}

# Re-import at module level for is_enabled
from ..utils.advisor import can_user_configure_advisor  # noqa: E402

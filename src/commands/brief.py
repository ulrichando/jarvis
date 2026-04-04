"""Brief command - Toggle brief-only mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class CommandResult:
    type: str
    value: str


@dataclass
class BriefConfig:
    enable_slash_command: bool = False


DEFAULT_BRIEF_CONFIG = BriefConfig(enable_slash_command=False)


def get_brief_config() -> BriefConfig:
    """Get brief configuration from feature flags."""
    from ..services.analytics.growthbook import get_feature_value_cached_may_be_stale

    raw = get_feature_value_cached_may_be_stale(
        "tengu_kairos_brief_config", DEFAULT_BRIEF_CONFIG
    )
    if isinstance(raw, dict):
        try:
            return BriefConfig(enable_slash_command=raw.get("enable_slash_command", False))
        except (TypeError, KeyError):
            return DEFAULT_BRIEF_CONFIG
    return DEFAULT_BRIEF_CONFIG


async def call(on_done: Any, context: Any) -> None:
    """Toggle brief-only mode."""
    from ..bootstrap.state import get_kairos_active, set_user_msg_opt_in
    from ..services.analytics import log_event
    from ..tools.BriefTool.BriefTool import is_brief_entitled
    from ..tools.BriefTool.prompt import BRIEF_TOOL_NAME

    current = context.get_app_state().is_brief_only
    new_state = not current

    # Entitlement check only gates the on-transition
    if new_state and not is_brief_entitled():
        log_event("tengu_brief_mode_toggled", {
            "enabled": False,
            "gated": True,
            "source": "slash_command",
        })
        on_done("Brief tool is not enabled for your account", {"display": "system"})
        return None

    set_user_msg_opt_in(new_state)

    context.set_app_state(lambda prev: {**prev, "is_brief_only": new_state})

    log_event("tengu_brief_mode_toggled", {
        "enabled": new_state,
        "gated": False,
        "source": "slash_command",
    })

    meta_messages = None
    if not get_kairos_active():
        if new_state:
            msg = (
                f"<system-reminder>\nBrief mode is now enabled. Use the {BRIEF_TOOL_NAME} tool "
                "for all user-facing output -- plain text outside it is hidden from the user's view."
                "\n</system-reminder>"
            )
        else:
            msg = (
                f"<system-reminder>\nBrief mode is now disabled. The {BRIEF_TOOL_NAME} tool "
                "is no longer available -- reply with plain text."
                "\n</system-reminder>"
            )
        meta_messages = [msg]

    on_done(
        "Brief-only mode enabled" if new_state else "Brief-only mode disabled",
        {"display": "system", "meta_messages": meta_messages},
    )
    return None


brief = {
    "type": "local-jsx",
    "name": "brief",
    "description": "Toggle brief-only mode",
    "is_enabled": lambda: get_brief_config().enable_slash_command,
    "immediate": True,
    "call": call,
}

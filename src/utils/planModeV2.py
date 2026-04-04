"""Plan mode v2 configuration and helpers."""

from __future__ import annotations

import os
from typing import Literal, Optional


def get_plan_mode_v2_agent_count() -> int:
    """Get the number of agents for plan mode v2."""
    env_val = os.environ.get("CLAUDE_CODE_PLAN_V2_AGENT_COUNT")
    if env_val:
        try:
            count = int(env_val)
            if 0 < count <= 10:
                return count
        except ValueError:
            pass
    return 1


def get_plan_mode_v2_explore_agent_count() -> int:
    """Get the number of exploration agents for plan mode v2."""
    env_val = os.environ.get("CLAUDE_CODE_PLAN_V2_EXPLORE_AGENT_COUNT")
    if env_val:
        try:
            count = int(env_val)
            if 0 < count <= 10:
                return count
        except ValueError:
            pass
    return 3


def _is_env_truthy(val: Optional[str]) -> bool:
    if not val:
        return False
    return val.lower() in ("1", "true", "yes")


def _is_env_defined_falsy(val: Optional[str]) -> bool:
    if val is None:
        return False
    return val.lower() in ("0", "false", "no")


def is_plan_mode_interview_phase_enabled() -> bool:
    """Check if plan mode interview phase is enabled."""
    env = os.environ.get("CLAUDE_CODE_PLAN_MODE_INTERVIEW_PHASE")
    if _is_env_truthy(env):
        return True
    if _is_env_defined_falsy(env):
        return False
    # Default off for non-internal users
    return False


PewterLedgerVariant = Optional[Literal["trim", "cut", "cap"]]


def get_pewter_ledger_variant() -> PewterLedgerVariant:
    """Get the pewter ledger variant for plan file structure experiments."""
    # No feature flag system in Python JARVIS; return None (control)
    return None
